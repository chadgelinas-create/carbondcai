"""Account field checks for the basic EMS login."""

import pytest

from skid2.ems.auth import (
    AuthError,
    AuthService,
    AuthUser,
    check_new_password,
    normalize_email,
    normalize_name,
    normalize_username,
)


def test_profile_fields_accept_admin_and_manager_shapes() -> None:
    assert normalize_username("manager") == "manager"
    assert normalize_email(" Manager@Powerstation.Local ") == "manager@powerstation.local"
    assert normalize_name("Powerstation Manager", "name") == "Powerstation Manager"
    assert normalize_name("Carbon DC", "company") == "Carbon DC"


def test_profile_fields_reject_blank_and_invalid_values() -> None:
    with pytest.raises(AuthError, match="username"):
        normalize_username("power station")
    with pytest.raises(AuthError, match="email"):
        normalize_email("not-an-email")
    with pytest.raises(AuthError, match="name"):
        normalize_name("   ", "name")
    with pytest.raises(AuthError, match="8 to 128"):
        check_new_password("short")


def test_token_still_round_trips_for_manager() -> None:
    service = AuthService.__new__(AuthService)
    service._secret = b"test-secret"
    user = AuthUser(
        "manager",
        "manager",
        email="manager@powerstation.local",
        display_name="Powerstation Manager",
        company="Powerstation",
        system_name="Skid 2",
    )
    parsed = service.parse_token(service.issue_token(user))
    assert parsed.username == "manager"
    assert parsed.role == "manager"
