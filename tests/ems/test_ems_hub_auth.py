"""Additional EMS unit coverage for auth tokens and live hub."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from skid2.ems.auth import AuthError, AuthService, AuthUser
from skid2.ems.hub import EmsHub, LivePoint


def test_token_round_trip_without_database() -> None:
    service = AuthService.__new__(AuthService)
    service._secret = b"test-secret"
    user = AuthUser("operator", "operator")
    token = AuthService.issue_token(service, user)
    parsed = AuthService.parse_token(service, token)
    assert parsed.username == "operator"
    assert parsed.role == "operator"


def test_invalid_token_rejected() -> None:
    service = AuthService.__new__(AuthService)
    service._secret = b"test-secret"
    with pytest.raises(AuthError):
        AuthService.parse_token(service, "not.a.token")


@pytest.mark.anyio
async def test_hub_snapshot_and_subscribe_bootstrap() -> None:
    hub = EmsHub()
    now = datetime(2026, 9, 25, tzinfo=UTC)
    await hub.publish(
        [
            LivePoint(
                "bess.cluster.soc",
                "ba1-1",
                "bms",
                "bess.cluster.soc",
                0.55,
                "ratio",
                "good",
                now,
                False,
            )
        ]
    )
    assert hub.get("bess.cluster.soc") is not None
    assert hub.snapshot("bms")[0].value == 0.55
    agen = hub.subscribe()
    first = await agen.__anext__()
    assert first["type"] == "snapshot"
    assert first["points"][0]["display_value"] == 0.55
    await agen.aclose()
