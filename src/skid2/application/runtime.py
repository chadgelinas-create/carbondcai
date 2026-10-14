"""Orchestrate the one-signal path without coupling domain code to frameworks."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from skid2.application.current_state import CurrentState
from skid2.application.ports import MeasurementStore, MeasurementStoreError
from skid2.contracts import Clock, ComponentStatus, Freshness, Measurement, SystemSnapshot
from skid2.settings import Settings
from skid2.sim.synthetic import (
    SYNTHETIC_POINT_ID,
    SyntheticLifecycle,
    SyntheticSignalSource,
)


@dataclass(frozen=True, slots=True)
class RuntimeHealth:
    """Bounded, non-secret component health for the API boundary."""

    synthetic_source: ComponentStatus
    current_state: Freshness
    historian: ComponentStatus


class WalkingSkeletonRuntime:
    """Own synthetic generation, durable acceptance, and current-state refresh."""

    def __init__(
        self,
        settings: Settings,
        clock: Clock,
        repository: MeasurementStore,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._repository = repository
        self._state = CurrentState(
            clock,
            timedelta(seconds=settings.freshness_threshold_seconds),
        )
        self._source = SyntheticSignalSource(
            clock,
            max_samples=settings.synthetic_max_samples,
        )
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._historian_status = ComponentStatus.HEALTHY

    @property
    def state(self) -> CurrentState:
        """Expose the framework-neutral read boundary."""

        return self._state

    async def start(self) -> None:
        """Restore durable state and start the optional synthetic loop."""

        if self._task is not None:
            return
        try:
            latest = await asyncio.to_thread(
                self._repository.latest,
                SYNTHETIC_POINT_ID,
            )
        except MeasurementStoreError:
            self._historian_status = ComponentStatus.DEGRADED
            latest = None
        if latest is not None:
            self._state.accept(latest)
        if not self._settings.synthetic_enabled:
            return
        self._stop_event.clear()
        self._source.start()
        await asyncio.to_thread(self.emit_once)
        self._task = asyncio.create_task(
            self._run(),
            name="skid2-walking-skeleton",
        )

    async def stop(self) -> None:
        """Stop generation and await task cleanup idempotently."""

        self._stop_event.set()
        task = self._task
        self._task = None
        if task is not None:
            await task
        self._source.stop()
        self._state.record_heartbeat(self._source.heartbeat())

    def emit_once(self) -> Measurement | None:
        """Persist and publish one sample, never exposing an undurable value."""

        measurement = self._source.emit_next()
        self._state.record_heartbeat(self._source.heartbeat())
        if measurement is None:
            return None
        try:
            self._repository.append(measurement)
        except MeasurementStoreError:
            self._historian_status = ComponentStatus.DEGRADED
            return None
        self._historian_status = ComponentStatus.HEALTHY
        self._state.accept(measurement)
        return measurement

    def pause_synthetic(self) -> None:
        """Pause the source for deterministic non-production tests."""

        self._source.pause()
        self._state.record_heartbeat(self._source.heartbeat())

    def resume_synthetic(self) -> None:
        """Resume a paused source for deterministic recovery tests."""

        if self._settings.synthetic_enabled:
            self._source.start()
            self._state.record_heartbeat(self._source.heartbeat())

    def snapshot(self) -> SystemSnapshot:
        """Read the current snapshot."""

        return self._state.snapshot()

    def health(self) -> RuntimeHealth:
        """Return bounded component health without implementation errors."""

        snapshot = self._state.snapshot()
        if not self._settings.synthetic_enabled:
            source_status = ComponentStatus.DISABLED
        elif self._source.lifecycle is SyntheticLifecycle.RUNNING:
            source_status = ComponentStatus.HEALTHY
        elif self._source.lifecycle is SyntheticLifecycle.PAUSED:
            source_status = ComponentStatus.DEGRADED
        else:
            source_status = ComponentStatus.STOPPED
        return RuntimeHealth(
            synthetic_source=source_status,
            current_state=snapshot.freshness,
            historian=self._historian_status,
        )

    async def _run(self) -> None:
        interval = self._settings.synthetic_interval_seconds
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except TimeoutError:
                await asyncio.to_thread(self.emit_once)
