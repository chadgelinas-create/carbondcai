"""Password verification and revocable login sessions."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import TypedDict, cast

from sqlalchemy import Engine, delete, func, insert, select, update

from skid2.persistence.account_audit import record_account_audit
from skid2.persistence.metadata import app_user, user_session

ADMIN_PASSWORD = "carbon"  # pragma: allowlist secret  # noqa: S105
ADMIN_SEEDS = (
    ("chad.gelinas@carbondc.io", "Chad Gelinas"),
    ("erlend@carbondc.io", "Erlend"),
)
_RETIRED_ADMINS = ("admin", "manager", "labadmin", "operator", "chad.gelinas@carbon.io")
_DEV_USERS = (("viewer", "viewer", "viewer"),)


class User(TypedDict):
    username: str
    role: str
    csrf_token: str


def password_hash(password: str, salt: bytes | None = None) -> str:
    """Use a fresh salt for each password."""
    salt = secrets.token_bytes(16) if salt is None else salt
    value = hashlib.scrypt(
        password.encode(), salt=salt, n=131072, r=8, p=1, maxmem=256 * 1024 * 1024, dklen=32
    )
    return f"{salt.hex()}:{value.hex()}"


def verify_password(password: str, stored: str) -> bool:
    salt, _ = stored.split(":", 1)
    return hmac.compare_digest(password_hash(password, bytes.fromhex(salt)), stored)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Accounts:
    def __init__(self, engine: Engine, session_seconds: int) -> None:
        self.engine = engine
        self.session_seconds = session_seconds
        self._dummy_hash = password_hash(secrets.token_urlsafe(32))

    def ensure_admin_users(self) -> None:
        """Keep Chad and Erlend as the only seeded administrators."""

        hashed = password_hash(ADMIN_PASSWORD)
        with self.engine.begin() as connection:
            for username, _display_name in ADMIN_SEEDS:
                existing = connection.execute(
                    select(app_user.c.username).where(
                        func.lower(app_user.c.username) == username.lower()
                    )
                ).first()
                if existing is None:
                    connection.execute(
                        insert(app_user).values(
                            username=username,
                            password_hash=hashed,
                            role="admin",
                            enabled=True,
                            failed_logins=0,
                        )
                    )
                else:
                    connection.execute(
                        update(app_user)
                        .where(func.lower(app_user.c.username) == username.lower())
                        .values(
                            password_hash=hashed,
                            role="admin",
                            enabled=True,
                            failed_logins=0,
                            locked_until=None,
                        )
                    )
            connection.execute(
                update(app_user)
                .where(
                    func.lower(app_user.c.username).in_([name.lower() for name in _RETIRED_ADMINS])
                )
                .values(role="viewer", enabled=False, failed_logins=0, locked_until=None)
            )
            connection.execute(
                delete(user_session).where(
                    func.lower(user_session.c.username).in_(
                        [name.lower() for name in _RETIRED_ADMINS]
                    )
                )
            )

    def ensure_dev_users(self) -> None:
        """Seed local operator/viewer lab accounts after the two administrators exist."""

        with self.engine.begin() as connection:
            existing = {
                str(username).lower()
                for (username,) in connection.execute(select(app_user.c.username))
            }
            for username, password, role in _DEV_USERS:
                if username.lower() not in existing:
                    connection.execute(
                        insert(app_user).values(
                            username=username,
                            password_hash=password_hash(password),
                            role=role,
                            enabled=True,
                            failed_logins=0,
                        )
                    )

    def create_user(self, username: str, password: str, role: str) -> None:
        if not username.strip() or len(username) > 100 or not 12 <= len(password) <= 256:
            raise ValueError("Use a username and a password of 12 to 256 characters")
        if role not in {"viewer", "operator", "admin"}:
            raise ValueError("Unknown user role")
        with self.engine.begin() as connection:
            connection.execute(
                insert(app_user).values(
                    username=username,
                    password_hash=password_hash(password),
                    role=role,
                    enabled=True,
                    failed_logins=0,
                )
            )

    def grant_access(self, username: str, password: str, role: str) -> None:
        """Store or refresh a lab login so an authorized admin can open every workspace."""

        cleaned = username.strip()
        if not cleaned or len(cleaned) > 100 or not 8 <= len(password) <= 256:
            raise ValueError("Use a username and a password of 8 to 256 characters")
        if role not in {"viewer", "operator", "admin"}:
            raise ValueError("Unknown user role")
        with self.engine.begin() as connection:
            existing = connection.execute(
                select(app_user.c.username).where(app_user.c.username == cleaned)
            ).first()
            if existing is None:
                connection.execute(
                    insert(app_user).values(
                        username=cleaned,
                        password_hash=password_hash(password),
                        role=role,
                        enabled=True,
                        failed_logins=0,
                    )
                )
                return
            connection.execute(
                update(app_user)
                .where(app_user.c.username == cleaned)
                .values(
                    password_hash=password_hash(password),
                    role=role,
                    enabled=True,
                    failed_logins=0,
                    locked_until=None,
                )
            )

    def login(self, username: str, password: str) -> tuple[str, User] | None:
        now = datetime.now(UTC)
        identity = username.strip()
        result: tuple[str, User] | None = None
        with self.engine.begin() as connection:
            row = (
                connection.execute(
                    select(app_user)
                    .where(func.lower(app_user.c.username) == identity.lower())
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            stored = self._dummy_hash if row is None else str(row["password_hash"])
            valid = verify_password(password, stored)
            if row is not None and row["enabled"]:
                username = str(row["username"])
                locked = row["locked_until"] is not None and row["locked_until"] > now
                if not valid:
                    failures = int(row["failed_logins"]) + 1
                    connection.execute(
                        update(app_user)
                        .where(app_user.c.username == username)
                        .values(
                            failed_logins=failures if failures < 5 else 0,
                            locked_until=now + timedelta(minutes=5) if failures >= 5 else None,
                        )
                    )
                elif not locked:
                    connection.execute(
                        update(app_user)
                        .where(app_user.c.username == username)
                        .values(failed_logins=0, locked_until=None)
                    )
                    connection.execute(delete(user_session).where(user_session.c.expires_at <= now))
                    token = secrets.token_urlsafe(32)
                    csrf = secrets.token_hex(32)
                    connection.execute(
                        insert(user_session).values(
                            token_hash=token_hash(token),
                            username=username,
                            csrf_token=csrf,
                            expires_at=now + timedelta(seconds=self.session_seconds),
                        )
                    )
                    result = (
                        token,
                        {"username": username, "role": str(row["role"]), "csrf_token": csrf},
                    )
        if identity:
            if result is None:
                record_account_audit(
                    self.engine,
                    actor=identity,
                    action="sign_in_failed",
                    subject=identity,
                    gate="admin",
                )
            else:
                signed_in = result[1]
                record_account_audit(
                    self.engine,
                    actor=signed_in["username"],
                    action="signed_in",
                    subject=signed_in["username"],
                    gate="admin",
                    detail=f"role={signed_in['role']}",
                )
        return result

    def current(self, token: str) -> User | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(app_user.c.username, app_user.c.role, user_session.c.csrf_token)
                    .join(user_session)
                    .where(
                        user_session.c.token_hash == token_hash(token),
                        user_session.c.expires_at > datetime.now(UTC),
                        app_user.c.enabled.is_(True),
                    )
                )
                .mappings()
                .first()
            )
        return None if row is None else cast(User, dict(row))

    def logout(self, token: str) -> None:
        with self.engine.begin() as connection:
            connection.execute(
                delete(user_session).where(user_session.c.token_hash == token_hash(token))
            )
