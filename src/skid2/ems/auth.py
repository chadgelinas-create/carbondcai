"""Password hashing and signed session tokens for EMS roles."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import Connection, Engine, RowMapping, func, or_, select, update
from sqlalchemy import insert as sa_insert
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from skid2.persistence.account_audit import (
    AccountAuditRow,
    list_account_audit,
    record_account_audit,
    write_account_audit,
)
from skid2.persistence.accounts import ADMIN_PASSWORD, ADMIN_SEEDS
from skid2.persistence.metadata import account_invite, ems_user

Role = Literal["viewer", "operator", "manager", "admin"]

_PBKDF2_ITERATIONS = 120_000
_TOKEN_TTL_SECONDS = 12 * 60 * 60
_INVITE_TTL = timedelta(hours=48)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_SEED_USERS = (
    *(
        {
            "username": email,
            "password": ADMIN_PASSWORD,
            "role": "admin",
            "email": email,
            "display_name": display_name,
            "company": "Carbon DC",
            "system_name": "Skid 2",
        }
        for email, display_name in ADMIN_SEEDS
    ),
    {
        "username": "manager",
        "password": "manager",
        "role": "manager",
        "email": "manager@powerstation.local",
        "display_name": "Powerstation Manager",
        "company": "Powerstation",
        "system_name": "Skid 2",
    },
    {
        "username": "viewer",
        "password": "viewer",
        "role": "viewer",
        "email": "viewer@carbondc.local",
        "display_name": "Viewer",
        "company": "Carbon DC",
        "system_name": "Skid 2",
    },
)
_RETIRED_EMS_ADMINS = ("admin", "chad.gelinas@carbon.io")
_RETIRED_EMS_USERS = ("operator",)


@dataclass(frozen=True, slots=True)
class AuthUser:
    username: str
    role: Role
    email: str = ""
    display_name: str = ""
    company: str = ""
    system_name: str = ""
    active: bool = True


class AuthError(ValueError):
    """Authentication or authorization failure."""


@dataclass(frozen=True, slots=True)
class InvitePreview:
    username: str
    email: str
    display_name: str
    purpose: str


def hash_password(password: str, salt: bytes | None = None) -> str:
    """Return `salt_hex$iterations$hash_hex` for durable storage."""

    material = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        material,
        _PBKDF2_ITERATIONS,
    )
    return f"{material.hex()}${_PBKDF2_ITERATIONS}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, iterations_text, digest_hex = stored.split("$", 2)
        expected = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            bytes.fromhex(salt_hex),
            int(iterations_text),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected.hex(), digest_hex)


def normalize_username(value: str) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > 64 or any(character.isspace() for character in cleaned):
        raise AuthError("Enter a username without spaces, up to 64 characters")
    return cleaned


def normalize_email(value: str) -> str:
    cleaned = value.strip().lower()
    if not _EMAIL.fullmatch(cleaned) or len(cleaned) > 254:
        raise AuthError("Enter a valid email address")
    return cleaned


def normalize_name(value: str, label: str) -> str:
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > 120:
        raise AuthError(f"Enter a {label} up to 120 characters")
    return cleaned


def check_new_password(password: str) -> None:
    if password != password.strip() or not 8 <= len(password) <= 128:
        raise AuthError("Use a password of 8 to 128 characters")


class AuthService:
    """Own EMS users and bearer tokens without external JWT dependencies."""

    def __init__(self, engine: Engine, signing_secret: str) -> None:
        self._engine = engine
        self._secret = signing_secret.encode("utf-8")

    def ensure_seed_users(self) -> None:
        """Insert default local accounts and keep the two named administrators current."""

        try:
            with self._engine.begin() as connection:
                for spec in _SEED_USERS:
                    hashed = hash_password(spec["password"])
                    values = {
                        "username": spec["username"],
                        "password_hash": hashed,
                        "role": spec["role"],
                        "active": True,
                        "email": spec["email"],
                        "display_name": spec["display_name"],
                        "company": spec["company"],
                        "system_name": spec["system_name"],
                    }
                    statement = insert(ems_user).values(**values)
                    if spec["role"] == "admin" and spec["email"] in {
                        email for email, _name in ADMIN_SEEDS
                    }:
                        statement = statement.on_conflict_do_update(
                            index_elements=["username"],
                            set_={
                                "password_hash": hashed,
                                "role": spec["role"],
                                "active": True,
                                "email": spec["email"],
                                "display_name": spec["display_name"],
                                "company": spec["company"],
                                "system_name": spec["system_name"],
                            },
                        )
                    else:
                        statement = statement.on_conflict_do_nothing(index_elements=["username"])
                    connection.execute(statement)
                    connection.execute(
                        update(ems_user)
                        .where(
                            ems_user.c.username == spec["username"],
                            ems_user.c.email.is_(None),
                        )
                        .values(
                            email=spec["email"],
                            display_name=spec["display_name"],
                            company=spec["company"],
                            system_name=spec["system_name"],
                        )
                    )
                connection.execute(
                    update(ems_user)
                    .where(func.lower(ems_user.c.username).in_(_RETIRED_EMS_ADMINS))
                    .values(active=False, role="viewer")
                )
                connection.execute(
                    update(ems_user)
                    .where(func.lower(ems_user.c.username).in_(_RETIRED_EMS_USERS))
                    .values(active=False)
                )
        except SQLAlchemyError as error:
            raise AuthError("could not seed EMS users") from error

    def authenticate(self, username: str, password: str) -> tuple[AuthUser, str]:
        identity = username.strip()
        if not identity or not password:
            raise AuthError("invalid credentials")
        try:
            with self._engine.connect() as connection:
                row = (
                    connection.execute(
                        select(ems_user).where(
                            ems_user.c.active.is_(True),
                            or_(
                                func.lower(ems_user.c.username) == identity.lower(),
                                func.lower(ems_user.c.email) == identity.lower(),
                            ),
                        )
                    )
                    .mappings()
                    .first()
                )
        except SQLAlchemyError as error:
            raise AuthError("could not authenticate") from error
        if row is not None and bool(row.get("must_change_password")):
            record_account_audit(
                self._engine,
                actor=identity,
                action="sign_in_failed",
                subject=identity,
                gate="user",
                detail="invitation pending",
            )
            raise AuthError("Check your email to accept access and set a password")
        if row is None or not verify_password(password, str(row["password_hash"])):
            record_account_audit(
                self._engine,
                actor=identity,
                action="sign_in_failed",
                subject=identity,
                gate="user",
            )
            raise AuthError("invalid credentials")
        user = _user_from_row(row)
        record_account_audit(
            self._engine,
            actor=user.username,
            action="signed_in",
            subject=user.username,
            gate="user",
            detail=f"role={user.role}",
        )
        return user, self.issue_token(user)

    def load_user(self, username: str) -> AuthUser:
        try:
            with self._engine.connect() as connection:
                row = _row_by_username(connection, username)
        except SQLAlchemyError as error:
            raise AuthError("could not load account") from error
        if row is None or not bool(row["active"]):
            raise AuthError("account not found")
        return _user_from_row(row)

    def update_own_profile(
        self,
        user: AuthUser,
        *,
        username: str,
        email: str,
        display_name: str,
    ) -> tuple[AuthUser, str]:
        username = normalize_username(username)
        email = normalize_email(email)
        display_name = normalize_name(display_name, "name")
        updated = self._update_row(
            user.username,
            actor=user.username,
            username=username,
            email=email,
            display_name=display_name,
        )
        return updated, self.issue_token(updated)

    def reset_own_password(
        self,
        user: AuthUser,
        current_password: str,
        new_password: str,
    ) -> None:
        check_new_password(new_password)
        try:
            with self._engine.begin() as connection:
                row = _row_by_username(connection, user.username)
                if row is None or not verify_password(current_password, str(row["password_hash"])):
                    raise AuthError("Current password is incorrect")
                connection.execute(
                    update(ems_user)
                    .where(ems_user.c.id == row["id"])
                    .values(password_hash=hash_password(new_password))
                )
                write_account_audit(
                    connection,
                    actor=user.username,
                    action="password_reset",
                    subject=user.username,
                    gate="directory",
                )
        except AuthError:
            raise
        except SQLAlchemyError as error:
            raise AuthError("could not reset password") from error

    def list_users(self, actor: AuthUser) -> list[AuthUser]:
        self.require_role(actor, "admin")
        try:
            with self._engine.connect() as connection:
                rows = (
                    connection.execute(select(ems_user).order_by(ems_user.c.username))
                    .mappings()
                    .all()
                )
        except SQLAlchemyError as error:
            raise AuthError("could not list users") from error
        return [_user_from_row(row) for row in rows]

    def create_user(
        self,
        actor: AuthUser,
        *,
        username: str,
        password: str = "",
        email: str,
        display_name: str,
        company: str,
        system_name: str,
        role: str,
    ) -> AuthUser:
        self.require_role(actor, "admin")
        username = normalize_username(username or email)
        email = normalize_email(email)
        display_name = normalize_name(display_name or username, "name")
        company = normalize_name(company or "Carbon DC", "company")
        system_name = normalize_name(system_name or "Skid 2", "system name")
        must_change = not password.strip()
        if must_change:
            password = secrets.token_urlsafe(24)
        else:
            check_new_password(password)
        if role not in {"viewer", "operator", "manager", "admin"}:
            raise AuthError("Choose viewer, operator, manager, or admin")
        try:
            with self._engine.begin() as connection:
                _ensure_unique(connection, username=username, email=email, user_id=None)
                connection.execute(
                    sa_insert(ems_user).values(
                        username=username,
                        password_hash=hash_password(password),
                        role=role,
                        active=True,
                        email=email,
                        display_name=display_name,
                        company=company,
                        system_name=system_name,
                        must_change_password=must_change,
                    )
                )
                row = _row_by_username(connection, username)
                write_account_audit(
                    connection,
                    actor=actor.username,
                    action="created",
                    subject=username,
                    gate="directory",
                    detail=f"role={role}",
                )
        except AuthError:
            raise
        except IntegrityError as error:
            raise AuthError("That username or email is already in use") from error
        except SQLAlchemyError as error:
            raise AuthError("could not create account") from error
        if row is None:
            raise AuthError("could not create account")
        return _user_from_row(row)

    def issue_invite(
        self,
        actor: AuthUser,
        target_username: str,
        *,
        purpose: str = "invite",
    ) -> str:
        self.require_role(actor, "admin")
        if purpose not in {"invite", "password_change"}:
            raise AuthError("unknown invitation")
        now = datetime.now(UTC)
        try:
            with self._engine.begin() as connection:
                row = _row_by_username(connection, target_username)
                if row is None or not bool(row["active"]):
                    raise AuthError("account not found")
                return _store_login_token(
                    connection,
                    row,
                    purpose=purpose,
                    created_by=actor.username,
                    now=now,
                    audit_action="invited",
                    audit_detail=purpose,
                    gate="directory",
                )
        except AuthError:
            raise
        except SQLAlchemyError as error:
            raise AuthError("could not send invitation") from error
        raise AuthError("could not send invitation")

    def request_password_reset(self, identity: str) -> tuple[str, str, str] | None:
        """Issue a login-link token if an active account matches username or email."""

        cleaned = identity.strip()
        if not cleaned:
            return None
        now = datetime.now(UTC)
        try:
            with self._engine.begin() as connection:
                row = (
                    connection.execute(
                        select(ems_user).where(
                            ems_user.c.active.is_(True),
                            or_(
                                func.lower(ems_user.c.username) == cleaned.lower(),
                                func.lower(ems_user.c.email) == cleaned.lower(),
                            ),
                        )
                    )
                    .mappings()
                    .first()
                )
                if row is None or not str(row["email"] or ""):
                    return None
                token = _store_login_token(
                    connection,
                    row,
                    purpose="password_change",
                    created_by=str(row["username"]),
                    now=now,
                    audit_action="password_reset",
                    audit_detail="email requested",
                    gate="user",
                )
                return token, str(row["email"]), str(row["display_name"] or row["username"])
        except SQLAlchemyError as error:
            raise AuthError("could not send reset email") from error

    def read_invite(self, token: str) -> InvitePreview:
        now = datetime.now(UTC)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        try:
            with self._engine.connect() as connection:
                invite = (
                    connection.execute(
                        select(account_invite).where(account_invite.c.token_hash == digest)
                    )
                    .mappings()
                    .first()
                )
                if invite is None or invite["used_at"] is not None or _expired(invite["expires_at"], now):
                    raise AuthError("This login link is invalid or has expired")
                user_row = _row_by_username(connection, str(invite["username"]))
        except AuthError:
            raise
        except SQLAlchemyError as error:
            raise AuthError("could not read invitation") from error
        if user_row is None:
            raise AuthError("This login link is invalid or has expired")
        return InvitePreview(
            username=str(invite["username"]),
            email=str(invite["email"]),
            display_name=str(user_row["display_name"] or invite["username"]),
            purpose=str(invite["purpose"]),
        )

    def accept_invite(self, token: str, new_password: str) -> AuthUser:
        check_new_password(new_password)
        now = datetime.now(UTC)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        try:
            with self._engine.begin() as connection:
                invite = (
                    connection.execute(
                        select(account_invite).where(account_invite.c.token_hash == digest)
                    )
                    .mappings()
                    .first()
                )
                if invite is None or invite["used_at"] is not None or _expired(invite["expires_at"], now):
                    raise AuthError("This login link is invalid or has expired")
                user_row = _row_by_username(connection, str(invite["username"]))
                if user_row is None or not bool(user_row["active"]):
                    raise AuthError("This login link is invalid or has expired")
                connection.execute(
                    update(ems_user)
                    .where(ems_user.c.id == user_row["id"])
                    .values(
                        password_hash=hash_password(new_password),
                        must_change_password=False,
                    )
                )
                connection.execute(
                    update(account_invite)
                    .where(account_invite.c.id == invite["id"])
                    .values(used_at=now)
                )
                write_account_audit(
                    connection,
                    actor=str(user_row["username"]),
                    action="password_reset",
                    subject=str(user_row["username"]),
                    gate="user",
                    detail="invitation accepted",
                )
                updated = _row_by_id(connection, int(user_row["id"]))
        except AuthError:
            raise
        except SQLAlchemyError as error:
            raise AuthError("could not set password") from error
        if updated is None:
            raise AuthError("could not set password")
        return _user_from_row(updated)

    def set_user_active(self, actor: AuthUser, target_username: str, active: bool) -> AuthUser:
        self.require_role(actor, "admin")
        if not active and target_username.lower() == actor.username.lower():
            raise AuthError("You cannot disable your own account")
        try:
            with self._engine.begin() as connection:
                row = _row_by_username(connection, target_username)
                if row is None:
                    raise AuthError("account not found")
                connection.execute(
                    update(ems_user).where(ems_user.c.id == row["id"]).values(active=active)
                )
                write_account_audit(
                    connection,
                    actor=actor.username,
                    action="enabled" if active else "disabled",
                    subject=str(row["username"]),
                    gate="directory",
                )
                updated = _row_by_id(connection, int(row["id"]))
        except AuthError:
            raise
        except SQLAlchemyError as error:
            raise AuthError("could not update account") from error
        if updated is None:
            raise AuthError("account not found")
        return _user_from_row(updated)

    def update_user(
        self,
        actor: AuthUser,
        target_username: str,
        *,
        username: str,
        email: str,
        display_name: str,
        company: str,
        system_name: str,
        role: str,
    ) -> AuthUser:
        self.require_role(actor, "admin")
        username = normalize_username(username)
        email = normalize_email(email)
        display_name = normalize_name(display_name, "name")
        company = normalize_name(company, "company")
        system_name = normalize_name(system_name, "system name")
        if role not in {"viewer", "operator", "manager", "admin"}:
            raise AuthError("Choose Admin or Manager")
        return self._update_row(
            target_username,
            actor=actor.username,
            username=username,
            email=email,
            display_name=display_name,
            company=company,
            system_name=system_name,
            role=role,
        )

    def reset_user_password(self, actor: AuthUser, target_username: str, new_password: str) -> None:
        self.require_role(actor, "admin")
        check_new_password(new_password)
        try:
            with self._engine.begin() as connection:
                row = _row_by_username(connection, target_username)
                if row is None:
                    raise AuthError("account not found")
                connection.execute(
                    update(ems_user)
                    .where(ems_user.c.id == row["id"])
                    .values(password_hash=hash_password(new_password))
                )
                write_account_audit(
                    connection,
                    actor=actor.username,
                    action="password_reset",
                    subject=str(row["username"]),
                    gate="directory",
                )
        except AuthError:
            raise
        except SQLAlchemyError as error:
            raise AuthError("could not reset password") from error

    def list_activity(self, actor: AuthUser, limit: int = 100) -> list[AccountAuditRow]:
        self.require_role(actor, "admin")
        try:
            return list_account_audit(self._engine, limit)
        except SQLAlchemyError as error:
            raise AuthError("could not read account activity") from error

    def issue_token(self, user: AuthUser) -> str:
        payload = {
            "u": user.username,
            "r": user.role,
            "exp": int(time.time()) + _TOKEN_TTL_SECONDS,
            "n": secrets.token_hex(8),
        }
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        signature = hmac.new(self._secret, body.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{_b64(body)}.{signature}"

    def parse_token(self, token: str) -> AuthUser:
        try:
            body_b64, signature = token.split(".", 1)
            body = _unb64(body_b64)
            expected = hmac.new(self._secret, body.encode("utf-8"), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise AuthError("invalid token")
            payload = json.loads(body)
            if int(payload["exp"]) < int(time.time()):
                raise AuthError("token expired")
            return AuthUser(username=str(payload["u"]), role=_role(str(payload["r"])))
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            raise AuthError("invalid token") from error

    def require_role(self, user: AuthUser, *allowed: Role) -> None:
        if user.role not in allowed and user.role != "admin":
            raise AuthError("insufficient role")

    def _update_row(self, current_username: str, *, actor: str, **values: str) -> AuthUser:
        try:
            with self._engine.begin() as connection:
                row = _row_by_username(connection, current_username)
                if row is None:
                    raise AuthError("account not found")
                _ensure_unique(
                    connection,
                    username=values["username"],
                    email=values["email"],
                    user_id=int(row["id"]),
                )
                connection.execute(
                    update(ems_user).where(ems_user.c.id == row["id"]).values(**values)
                )
                detail = f"role={values['role']}" if "role" in values else "profile"
                write_account_audit(
                    connection,
                    actor=actor,
                    action="updated",
                    subject=values["username"],
                    gate="directory",
                    detail=detail,
                )
                updated = _row_by_id(connection, int(row["id"]))
        except AuthError:
            raise
        except SQLAlchemyError as error:
            raise AuthError("could not update account") from error
        if updated is None:
            raise AuthError("account not found")
        return _user_from_row(updated)


def _user_from_row(row: RowMapping) -> AuthUser:
    return AuthUser(
        username=str(row["username"]),
        role=_role(str(row["role"])),
        email=str(row["email"] or ""),
        display_name=str(row["display_name"] or ""),
        company=str(row["company"] or ""),
        system_name=str(row["system_name"] or ""),
        active=bool(row["active"]),
    )


def _store_login_token(
    connection: Connection,
    row: RowMapping,
    *,
    purpose: str,
    created_by: str,
    now: datetime,
    audit_action: str,
    audit_detail: str,
    gate: str,
) -> str:
    email = str(row["email"] or "")
    if not email:
        raise AuthError("This account needs an email address")
    raw = secrets.token_urlsafe(32)
    connection.execute(
        update(account_invite)
        .where(
            account_invite.c.username == str(row["username"]),
            account_invite.c.used_at.is_(None),
        )
        .values(expires_at=now)
    )
    connection.execute(
        sa_insert(account_invite).values(
            token_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            username=str(row["username"]),
            email=email,
            purpose=purpose,
            created_by=created_by,
            expires_at=now + _INVITE_TTL,
        )
    )
    write_account_audit(
        connection,
        actor=created_by,
        action=audit_action,
        subject=str(row["username"]),
        gate=gate,
        detail=audit_detail,
    )
    return raw


def _row_by_username(connection: Connection, username: str) -> RowMapping | None:
    return (
        connection.execute(
            select(ems_user).where(func.lower(ems_user.c.username) == username.lower())
        )
        .mappings()
        .first()
    )


def _row_by_id(connection: Connection, user_id: int) -> RowMapping | None:
    return connection.execute(select(ems_user).where(ems_user.c.id == user_id)).mappings().first()


def _ensure_unique(
    connection: Connection, *, username: str, email: str, user_id: int | None
) -> None:
    name_match = [func.lower(ems_user.c.username) == username.lower()]
    email_match = [func.lower(ems_user.c.email) == email.lower()]
    if user_id is not None:
        name_match.append(ems_user.c.id != user_id)
        email_match.append(ems_user.c.id != user_id)
    taken_name = connection.execute(select(ems_user.c.id).where(*name_match)).first()
    if taken_name is not None:
        raise AuthError("That username is already in use")
    taken_email = connection.execute(select(ems_user.c.id).where(*email_match)).first()
    if taken_email is not None:
        raise AuthError("That email is already in use")


def _expired(expires_at: object, now: datetime) -> bool:
    if not isinstance(expires_at, datetime):
        return True
    if expires_at.tzinfo is None:
        return expires_at.replace(tzinfo=UTC) <= now
    return expires_at <= now


def _role(value: str) -> Role:
    if value not in {"viewer", "operator", "manager", "admin"}:
        raise AuthError("unknown role")
    return value  # type: ignore[return-value]


def _b64(value: str) -> str:
    import base64

    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _unb64(value: str) -> str:
    import base64

    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding).decode("utf-8")
