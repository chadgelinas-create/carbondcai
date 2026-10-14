"""Backend health-service tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

from skid2.application.runtime import WalkingSkeletonRuntime
from skid2.backend import app as app_module
from skid2.contracts import DataQuality, ManualClock, Measurement
from skid2.persistence.database import DatabaseStatus
from skid2.settings import Settings
from skid2.sim.synthetic import SYNTHETIC_POINT_ID

pytestmark = pytest.mark.usefixtures("legacy_api")

NOW = datetime(2026, 7, 27, 10, tzinfo=UTC)


def settings() -> Settings:
    """Create valid settings independent of the developer environment."""

    return Settings(
        environment="test",
        service_name="test",
        service_host="127.0.0.1",
        service_port=8000,
        database_url=SecretStr(
            "postgresql+psycopg://user:fake-test-value@db:5432/skid2"  # pragma: allowlist secret
        ),
        synthetic_enabled=True,
        synthetic_interval_seconds=1,
        freshness_threshold_seconds=3,
    )


@dataclass
class MemoryStore:
    """Minimal runtime repository for API contract tests."""

    samples: list[Measurement] = field(default_factory=list)

    def append(self, measurement: Measurement) -> bool:
        self.samples.append(measurement)
        return True

    def latest(self, point_id: str) -> Measurement | None:
        matches = [item for item in self.samples if item.point_id == point_id]
        return None if not matches else matches[-1]


def measurement(at: datetime = NOW) -> Measurement:
    """Build the one explicitly synthetic API value."""

    return Measurement(
        point_id=SYNTHETIC_POINT_ID,
        label="Synthetic Walking-Skeleton Test Signal",
        source="synthetic.phase2.generator",
        value=10,
        unit="test_unit",
        source_timestamp=at,
        receive_timestamp=at,
        quality=DataQuality.GOOD,
        synthetic=True,
    )


def install_runtime(clock: ManualClock, item: Measurement | None) -> WalkingSkeletonRuntime:
    """Install a real application runtime without invoking the ASGI lifespan."""

    runtime = WalkingSkeletonRuntime(settings(), clock, MemoryStore())
    if item is not None:
        runtime.state.accept(item)
    app_module.app.state.walking_skeleton = runtime
    return runtime


@pytest.mark.anyio
async def test_liveness_does_not_require_database() -> None:
    """Liveness reports only process responsiveness."""

    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


@pytest.mark.anyio
async def test_readiness_reports_database_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Readiness exposes only safe migration/extension versions."""

    monkeypatch.setattr(app_module, "get_settings", settings)
    monkeypatch.setattr(
        app_module,
        "database_status",
        lambda _settings: DatabaseStatus("20260721_0001", "2.28.2"),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database_revision": "20260721_0001",
        "timescaledb_version": "2.28.2",
    }


@pytest.mark.anyio
async def test_readiness_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Database errors yield a generic failure that cannot leak a URL."""

    def fail_database(_settings: Settings) -> DatabaseStatus:
        raise SQLAlchemyError(
            "postgresql+psycopg://user:fake-test-value@db/skid2"  # pragma: allowlist secret
        )

    monkeypatch.setattr(app_module, "get_settings", settings)
    monkeypatch.setattr(app_module, "database_status", fail_database)

    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "backend database is not ready"}
    assert "secret" not in response.text


@pytest.mark.anyio
async def test_current_state_reports_live_and_stale_without_losing_contract() -> None:
    """The API preserves domain fields and derives stale state at read time."""

    clock = ManualClock(NOW)
    install_runtime(clock, measurement())

    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        live_response = await client.get("/api/v1/current-state")
        clock.advance(timedelta(seconds=4))
        stale_response = await client.get("/api/v1/current-state")

    assert live_response.status_code == 200
    assert live_response.json() == {
        "observed_at": "2026-07-27T10:00:00Z",
        "freshness": "live",
        "measurement": {
            "point_id": SYNTHETIC_POINT_ID,
            "label": "Synthetic Walking-Skeleton Test Signal",
            "source": "synthetic.phase2.generator",
            "value": 10.0,
            "unit": "test_unit",
            "source_timestamp": "2026-07-27T10:00:00Z",
            "receive_timestamp": "2026-07-27T10:00:00Z",
            "quality": "good",
            "synthetic": True,
        },
        "heartbeat": None,
    }
    assert stale_response.json()["freshness"] == "stale"
    assert stale_response.json()["measurement"] == live_response.json()["measurement"]


@pytest.mark.anyio
async def test_current_state_reports_unavailable_as_null_not_zero() -> None:
    """Missing current data is an explicit null measurement."""

    install_runtime(ManualClock(NOW), None)

    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/current-state")

    assert response.status_code == 200
    assert response.json()["freshness"] == "unavailable"
    assert response.json()["measurement"] is None
    assert "value" not in response.json()


@pytest.mark.anyio
async def test_runtime_dependency_failure_is_bounded() -> None:
    """A request before runtime initialization receives a stable 503 response."""

    if hasattr(app_module.app.state, "walking_skeleton"):
        del app_module.app.state.walking_skeleton

    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/current-state")

    assert response.status_code == 503
    assert response.json() == {"detail": "walking-skeleton runtime is not ready"}


@pytest.mark.anyio
async def test_component_health_distinguishes_all_walking_skeleton_boundaries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Component health is bounded and more informative than process liveness."""

    install_runtime(ManualClock(NOW), measurement())
    monkeypatch.setattr(app_module, "get_settings", settings)
    monkeypatch.setattr(
        app_module,
        "database_status",
        lambda _settings: DatabaseStatus("20260727_0002", "2.28.2"),
    )

    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/health/components")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "database": "healthy",
        "migration": "current",
        "synthetic_source": "stopped",
        "current_state": "live",
        "historian": "healthy",
    }


@pytest.mark.anyio
async def test_component_health_database_failure_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Component health returns no database diagnostic or credential."""

    install_runtime(ManualClock(NOW), measurement())

    def fail_database(_settings: Settings) -> DatabaseStatus:
        raise SQLAlchemyError("postgresql://user:sensitive@db/skid2")  # pragma: allowlist secret

    monkeypatch.setattr(app_module, "get_settings", settings)
    monkeypatch.setattr(app_module, "database_status", fail_database)

    async with AsyncClient(
        transport=ASGITransport(app=app_module.app), base_url="http://test"
    ) as client:
        response = await client.get("/api/v1/health/components")

    assert response.status_code == 503
    assert response.json() == {"detail": "component health is not ready"}
    assert "sensitive" not in response.text


def test_openapi_documents_current_state_and_quality_vocabulary() -> None:
    """Generated OpenAPI keeps API response types discoverable."""

    schema = app_module.app.openapi()

    assert "/api/v1/current-state" in schema["paths"]
    assert schema["components"]["schemas"]["DataQuality"]["enum"] == [
        "good",
        "stale",
        "invalid",
        "comm_error",
        "out_of_range",
    ]
