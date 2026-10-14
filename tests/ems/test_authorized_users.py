"""Admin storage of authorized EMS users in the plant database."""

# ruff: noqa: S105

from __future__ import annotations

import pytest
from sqlalchemy import Engine, create_engine, text

from skid2.ems.auth import AuthError, AuthService, AuthUser, hash_password
from skid2.persistence.metadata import ems_user

PASSWORD = "operator-pass"  # pragma: allowlist secret


def _engine() -> Engine:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE ems_user (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username VARCHAR(64) NOT NULL UNIQUE,
                    password_hash VARCHAR(200) NOT NULL,
                    role VARCHAR(32) NOT NULL,
                    active BOOLEAN NOT NULL,
                    email VARCHAR(254) UNIQUE,
                    display_name VARCHAR(120) NOT NULL DEFAULT '',
                    company VARCHAR(120) NOT NULL DEFAULT '',
                    system_name VARCHAR(120) NOT NULL DEFAULT '',
                    must_change_password BOOLEAN NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE account_invite (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    token_hash VARCHAR(64) NOT NULL UNIQUE,
                    username VARCHAR(64) NOT NULL,
                    email VARCHAR(254) NOT NULL,
                    purpose VARCHAR(32) NOT NULL,
                    created_by VARCHAR(64) NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    used_at TIMESTAMP
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE account_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    actor VARCHAR(64) NOT NULL,
                    action VARCHAR(32) NOT NULL,
                    subject VARCHAR(64) NOT NULL,
                    gate VARCHAR(16) NOT NULL,
                    detail VARCHAR(200) NOT NULL DEFAULT ''
                )
                """
            )
        )
    return engine


def _admin(service: AuthService) -> AuthUser:
    engine = service._engine
    with engine.begin() as connection:
        connection.execute(
            ems_user.insert().values(
                username="admin",
                password_hash=hash_password("admin-pass"),
                role="admin",
                active=True,
                email="admin@carbondc.local",
                display_name="Carbon DC Admin",
                company="Carbon DC",
                system_name="Skid 2",
            )
        )
    return service.load_user("admin")


def test_admin_stores_user_that_can_authenticate() -> None:
    service = AuthService(_engine(), "test-secret")
    admin = _admin(service)
    created = service.create_user(
        admin,
        username="plant.op",
        password=PASSWORD,
        email="op@carbondc.local",
        display_name="Plant Operator",
        company="Carbon DC",
        system_name="Skid 2",
        role="operator",
    )
    assert created.username == "plant.op"
    assert created.role == "operator"
    assert created.active is True
    user, _token = service.authenticate("plant.op", "operator-pass")
    assert user.email == "op@carbondc.local"
    names = [account.username for account in service.list_users(admin)]
    assert names == ["admin", "plant.op"]
    events = service.list_activity(admin)
    created_events = [row for row in events if row.action == "created"]
    assert created_events[0].actor == "admin"
    assert created_events[0].subject == "plant.op"
    assert created_events[0].gate == "directory"
    assert any(row.action == "signed_in" and row.subject == "plant.op" for row in events)


def test_operator_cannot_store_users() -> None:
    service = AuthService(_engine(), "test-secret")
    admin = _admin(service)
    service.create_user(
        admin,
        username="plant.op",
        password=PASSWORD,
        email="op@carbondc.local",
        display_name="Plant Operator",
        company="Carbon DC",
        system_name="Skid 2",
        role="operator",
    )
    operator = AuthUser("plant.op", "operator")
    with pytest.raises(AuthError, match="insufficient"):
        service.create_user(
            operator,
            username="other",
            password=PASSWORD,
            email="other@carbondc.local",
            display_name="Other",
            company="Carbon DC",
            system_name="Skid 2",
            role="operator",
        )


def test_duplicate_username_is_rejected() -> None:
    service = AuthService(_engine(), "test-secret")
    admin = _admin(service)
    with pytest.raises(AuthError, match="username"):
        service.create_user(
            admin,
            username="admin",
            password=PASSWORD,
            email="second@carbondc.local",
            display_name="Second",
            company="Carbon DC",
            system_name="Skid 2",
            role="operator",
        )


def test_disabled_user_cannot_sign_in() -> None:
    service = AuthService(_engine(), "test-secret")
    admin = _admin(service)
    service.create_user(
        admin,
        username="plant.op",
        password=PASSWORD,
        email="op@carbondc.local",
        display_name="Plant Operator",
        company="Carbon DC",
        system_name="Skid 2",
        role="operator",
    )
    updated = service.set_user_active(admin, "plant.op", False)
    assert updated.active is False
    with pytest.raises(AuthError, match="invalid credentials"):
        service.authenticate("plant.op", "operator-pass")
    with pytest.raises(AuthError, match="your own account"):
        service.set_user_active(admin, "admin", False)


def test_only_named_emails_are_seeded_admins() -> None:
    from skid2.ems.auth import _SEED_USERS
    from skid2.persistence.accounts import ADMIN_PASSWORD, ADMIN_SEEDS

    asserted = {"chad.gelinas@carbondc.io", "erlend@carbondc.io"}
    assert {email for email, _name in ADMIN_SEEDS} == asserted
    admins = [row for row in _SEED_USERS if row["role"] == "admin"]
    assert {row["email"] for row in admins} == asserted
    assert all(row["password"] == ADMIN_PASSWORD for row in admins)
    assert all(row["username"] == row["email"] for row in admins)


def test_user_login_has_no_seeded_operator_email() -> None:
    from skid2.ems.auth import _SEED_USERS

    users = [row for row in _SEED_USERS if row["role"] == "operator"]
    assert users == []


def test_authorization_invite_sets_password() -> None:
    service = AuthService(_engine(), "test-secret")
    admin = _admin(service)
    created = service.create_user(
        admin,
        username="client.op",
        email="client@carbondc.local",
        display_name="Client Operator",
        company="Carbon DC",
        system_name="Skid 2",
        role="operator",
    )
    with pytest.raises(AuthError, match="email"):
        service.authenticate("client.op", "not-yet")
    token = service.issue_invite(admin, created.username)
    preview = service.read_invite(token)
    assert preview.email == "client@carbondc.local"
    accepted = service.accept_invite(token, "client-pass")
    assert accepted.username == "client.op"
    user, _token = service.authenticate("client.op", "client-pass")
    assert user.email == "client@carbondc.local"
    with pytest.raises(AuthError, match="expired"):
        service.accept_invite(token, "other-pass1")


def test_forgot_password_email_sets_new_password() -> None:
    service = AuthService(_engine(), "test-secret")
    admin = _admin(service)
    service.create_user(
        admin,
        username="plant.op",
        password=PASSWORD,
        email="op@carbondc.local",
        display_name="Plant Operator",
        company="Carbon DC",
        system_name="Skid 2",
        role="operator",
    )
    assert service.request_password_reset("nobody@carbondc.local") is None
    issued = service.request_password_reset("op@carbondc.local")
    assert issued is not None
    token, email, display_name = issued
    assert email == "op@carbondc.local"
    assert display_name == "Plant Operator"
    preview = service.read_invite(token)
    assert preview.purpose == "password_change"
    service.authenticate("plant.op", PASSWORD)
    accepted = service.accept_invite(token, "new-pass-99")
    assert accepted.username == "plant.op"
    with pytest.raises(AuthError):
        service.authenticate("plant.op", PASSWORD)
    user, _token = service.authenticate("op@carbondc.local", "new-pass-99")
    assert user.username == "plant.op"
