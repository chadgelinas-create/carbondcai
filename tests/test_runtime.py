"""Walking-skeleton runtime orchestration tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from skid2.application.ports import MeasurementStoreError
from skid2.application.runtime import WalkingSkeletonRuntime
from skid2.contracts import ComponentStatus, DataQuality, Freshness, ManualClock, Measurement
from skid2.settings import Settings
from skid2.sim.synthetic import SYNTHETIC_POINT_ID

NOW = datetime(2026, 7, 27, 10, tzinfo=UTC)


def runtime_settings(
    *,
    enabled: bool = True,
    max_samples: int = 1,
) -> Settings:
    """Create valid isolated runtime settings."""

    return Settings(
        environment="test",
        service_name="runtime-test",
        service_host="127.0.0.1",
        service_port=8000,
        database_url=SecretStr(
            "postgresql+psycopg://user:fake-test-value@db:5432/skid2"  # pragma: allowlist secret
        ),
        synthetic_enabled=enabled,
        synthetic_interval_seconds=0.1,
        freshness_threshold_seconds=0.2,
        synthetic_max_samples=max_samples,
    )


def restored_measurement(at: datetime = NOW) -> Measurement:
    """Build a durable measurement as if loaded after restart."""

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


@dataclass
class MemoryStore:
    """Small persistence test double with bounded failure controls."""

    samples: list[Measurement] = field(default_factory=list)
    fail_latest: bool = False
    fail_append: bool = False

    def append(self, measurement: Measurement) -> bool:
        if self.fail_append:
            raise MeasurementStoreError("test append failure")
        self.samples.append(measurement)
        return True

    def latest(self, point_id: str) -> Measurement | None:
        if self.fail_latest:
            raise MeasurementStoreError("test latest failure")
        matches = [item for item in self.samples if item.point_id == point_id]
        return None if not matches else matches[-1]


@pytest.mark.anyio
async def test_start_persists_before_publish_and_stops_cleanly() -> None:
    """The runtime publishes only after the generated sample is durable."""

    store = MemoryStore()
    runtime = WalkingSkeletonRuntime(
        runtime_settings(),
        ManualClock(NOW),
        store,
    )

    await runtime.start()
    snapshot = runtime.snapshot()

    assert len(store.samples) == 1
    assert snapshot.measurement == store.samples[0]
    assert snapshot.freshness is Freshness.LIVE
    assert runtime.health().synthetic_source is ComponentStatus.DEGRADED
    assert runtime.health().historian is ComponentStatus.HEALTHY

    await runtime.stop()
    await runtime.stop()
    assert runtime.health().synthetic_source is ComponentStatus.STOPPED


@pytest.mark.anyio
async def test_disabled_source_restores_latest_without_generating() -> None:
    """Restart restoration works while the test source is explicitly disabled."""

    item = restored_measurement()
    store = MemoryStore(samples=[item])
    runtime = WalkingSkeletonRuntime(
        runtime_settings(enabled=False),
        ManualClock(NOW),
        store,
    )

    await runtime.start()
    await runtime.start()

    assert store.samples == [item]
    assert runtime.snapshot().measurement == item
    assert runtime.health().synthetic_source is ComponentStatus.DISABLED
    await runtime.stop()


@pytest.mark.anyio
async def test_persistence_failures_degrade_without_publishing_undurable_data() -> None:
    """Infrastructure errors are bounded and never expose an undurable value."""

    store = MemoryStore(fail_latest=True, fail_append=True)
    runtime = WalkingSkeletonRuntime(
        runtime_settings(),
        ManualClock(NOW),
        store,
    )

    await runtime.start()

    assert runtime.snapshot().measurement is None
    assert runtime.snapshot().freshness is Freshness.UNAVAILABLE
    assert runtime.health().historian is ComponentStatus.DEGRADED
    await runtime.stop()


@pytest.mark.anyio
async def test_pause_stale_and_resume_recovery_use_injected_time() -> None:
    """Pause creates no replacement values and a later sample recovers state."""

    clock = ManualClock(NOW)
    runtime = WalkingSkeletonRuntime(
        runtime_settings(max_samples=0),
        clock,
        MemoryStore(),
    )
    await runtime.start()
    first = runtime.snapshot().measurement
    runtime.pause_synthetic()
    clock.advance(timedelta(seconds=1))

    assert runtime.snapshot().freshness is Freshness.STALE
    assert runtime.snapshot().measurement == first
    assert runtime.emit_once() is None

    runtime.resume_synthetic()
    recovered = runtime.emit_once()
    assert recovered is not None
    assert recovered.value == 10.5
    assert runtime.snapshot().freshness is Freshness.LIVE
    await runtime.stop()


def test_disabled_runtime_does_not_resume_or_emit() -> None:
    """The runtime cannot turn on synthetic data when configuration forbids it."""

    runtime = WalkingSkeletonRuntime(
        runtime_settings(enabled=False),
        ManualClock(NOW),
        MemoryStore(),
    )

    runtime.resume_synthetic()

    assert runtime.emit_once() is None
    assert runtime.health().synthetic_source is ComponentStatus.DISABLED
