"""Deterministic, transport-independent communication health processing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from math import isfinite

from skid2.contracts import ComponentStatus, DataQuality
from skid2.contracts.domain import normalize_utc
from skid2.sim.device import (
    CapabilityKind,
    DeviceCapability,
    DeviceEvent,
    DeviceHealth,
    DeviceLifecycle,
    DeviceValue,
    EvidenceClassification,
    ExecutionProfile,
)


class CommunicationConfigurationError(ValueError):
    """A communication policy, state, or update is inconsistent."""


class CommunicationOutcome(StrEnum):
    """Explicit result of one observed or simulated communication transaction."""

    SUCCESS = "success"
    DROP_REQUEST = "drop_request"
    DROP_RESPONSE = "drop_response"
    EXCEPTION = "exception"
    DISCONNECT = "disconnect"
    RECONNECT = "reconnect"
    STUCK = "stuck"
    CORRUPT = "corrupt"
    OUT_OF_RANGE = "out_of_range"


@dataclass(frozen=True, slots=True)
class CommunicationPolicy:
    """Reviewed timing, recovery, and capability rules for one device."""

    behavior_capability: DeviceCapability
    fault_capability: DeviceCapability
    timeout: timedelta
    stale_after: timedelta
    watchdog_after: timedelta
    recovery_updates: int = 1

    def __post_init__(self) -> None:
        behavior = self.behavior_capability
        faults = self.fault_capability
        if behavior.kind is not CapabilityKind.BEHAVIOR:
            raise CommunicationConfigurationError(
                "communication behavior capability must have behavior kind"
            )
        if faults.kind is not CapabilityKind.FAULT:
            raise CommunicationConfigurationError(
                "communication fault capability must have fault kind"
            )
        if behavior.device_id != faults.device_id:
            raise CommunicationConfigurationError(
                "communication capabilities must belong to the same device"
            )
        _validate_duration(self.timeout, "communication timeout")
        _validate_duration(self.stale_after, "stale threshold")
        _validate_duration(self.watchdog_after, "watchdog threshold")
        if self.stale_after >= self.watchdog_after:
            raise CommunicationConfigurationError(
                "stale threshold must be less than watchdog threshold"
            )
        if not isinstance(self.recovery_updates, int) or isinstance(self.recovery_updates, bool):
            raise CommunicationConfigurationError("recovery updates must be a positive integer")
        if self.recovery_updates < 1:
            raise CommunicationConfigurationError("recovery updates must be a positive integer")


@dataclass(frozen=True, slots=True)
class CommunicationUpdate:
    """One explicit communication outcome measured against logical time."""

    outcome: CommunicationOutcome
    elapsed: timedelta
    value: DeviceValue | None = None
    injected: bool = False

    def __post_init__(self) -> None:
        _validate_duration(
            self.elapsed,
            "communication elapsed time",
            allow_zero=True,
        )


@dataclass(frozen=True, slots=True)
class CommunicationState:
    """Immutable transaction, heartbeat, freshness, and recovery state."""

    device_id: str
    connected: bool = True
    quality: DataQuality = DataQuality.COMM_ERROR
    value: DeviceValue | None = None
    last_request_at: datetime | None = None
    last_response_at: datetime | None = None
    last_heartbeat_at: datetime | None = None
    last_good_at: datetime | None = None
    checked_at: datetime | None = None
    good_updates: int = 0

    def __post_init__(self) -> None:
        if not self.device_id.strip():
            raise CommunicationConfigurationError("device_id must not be empty")


@dataclass(frozen=True, slots=True)
class CommunicationBatch:
    """Communication state and device-facing outputs produced by one step."""

    state: CommunicationState
    value: DeviceValue | None
    health: DeviceHealth
    events: tuple[DeviceEvent, ...]


def process_communication(
    state: CommunicationState,
    update: CommunicationUpdate | None,
    policy: CommunicationPolicy,
    lifecycle: DeviceLifecycle,
    profile: ExecutionProfile,
    at: datetime,
) -> CommunicationBatch:
    """Apply one communication update using injected logical time."""

    current_at = normalize_utc(at, "at")
    if state.device_id != policy.behavior_capability.device_id:
        raise CommunicationConfigurationError("communication state does not match policy device")
    if state.checked_at is not None and current_at < state.checked_at:
        raise CommunicationConfigurationError("communication time must not move backwards")
    _validate_behavior_profile(policy, profile)
    _validate_fault_injection(policy, update, profile)

    if update is None:
        current, event = _age_state(state, policy, current_at)
    else:
        current, event = _apply_update(state, update, policy, current_at)

    current = replace(current, checked_at=current_at)
    health = DeviceHealth(
        lifecycle=lifecycle,
        status=_status(current),
        quality=current.quality,
        source=current.device_id,
        evidence_classification=policy.behavior_capability.evidence_classification,
    )
    return CommunicationBatch(
        state=current,
        value=current.value,
        health=health,
        events=() if event is None else (event,),
    )


def _apply_update(
    state: CommunicationState,
    update: CommunicationUpdate,
    policy: CommunicationPolicy,
    at: datetime,
) -> tuple[CommunicationState, DeviceEvent | None]:
    evidence = (
        policy.fault_capability.evidence_classification
        if update.injected
        else policy.behavior_capability.evidence_classification
    )

    if update.outcome is CommunicationOutcome.DISCONNECT:
        return _bad(
            replace(state, connected=False),
            DataQuality.COMM_ERROR,
            state.value,
            "disconnected",
            at,
            evidence,
        )
    if update.outcome is CommunicationOutcome.RECONNECT:
        return _bad(
            replace(state, connected=True),
            DataQuality.COMM_ERROR,
            state.value,
            "reconnected",
            at,
            evidence,
        )
    if not state.connected:
        raise CommunicationConfigurationError("communication update requires reconnect")
    if update.outcome is CommunicationOutcome.DROP_REQUEST:
        return _bad(
            state,
            DataQuality.COMM_ERROR,
            state.value,
            "request_dropped",
            at,
            evidence,
        )

    request_at = at - update.elapsed
    current = replace(state, last_request_at=request_at)
    if update.outcome is CommunicationOutcome.DROP_RESPONSE:
        return _bad(
            current,
            DataQuality.COMM_ERROR,
            state.value,
            "response_dropped",
            at,
            evidence,
        )
    if update.elapsed > policy.timeout:
        return _bad(
            current,
            DataQuality.COMM_ERROR,
            state.value,
            "timed_out",
            at,
            evidence,
        )

    current = replace(current, last_response_at=at)
    if update.outcome is CommunicationOutcome.EXCEPTION:
        return _bad(
            current,
            DataQuality.COMM_ERROR,
            state.value,
            "exception",
            at,
            evidence,
        )

    current = replace(current, last_heartbeat_at=at)
    if update.outcome is CommunicationOutcome.STUCK:
        if state.value is None or state.quality not in {
            DataQuality.GOOD,
            DataQuality.STALE,
        }:
            raise CommunicationConfigurationError("stuck update requires a previous trusted value")
        return _bad(
            current,
            DataQuality.STALE,
            state.value,
            "stuck",
            at,
            evidence,
        )

    value = _checked_value(update.value, policy)
    if update.outcome is CommunicationOutcome.CORRUPT:
        return _bad(
            current,
            DataQuality.INVALID,
            value,
            "corrupt",
            at,
            evidence,
        )
    if update.outcome is CommunicationOutcome.OUT_OF_RANGE:
        return _bad(
            current,
            DataQuality.OUT_OF_RANGE,
            value,
            "out_of_range",
            at,
            evidence,
        )
    if update.outcome is not CommunicationOutcome.SUCCESS:
        raise AssertionError(f"unhandled communication outcome: {update.outcome}")
    if value.quality is not DataQuality.GOOD:
        raise CommunicationConfigurationError(
            "successful communication requires a good input value"
        )
    return _good(current, value, policy, at, evidence)


def _age_state(
    state: CommunicationState,
    policy: CommunicationPolicy,
    at: datetime,
) -> tuple[CommunicationState, DeviceEvent | None]:
    evidence = policy.behavior_capability.evidence_classification
    if (
        state.last_heartbeat_at is not None
        and at - state.last_heartbeat_at >= policy.watchdog_after
        and state.quality is not DataQuality.COMM_ERROR
    ):
        occurred_at = state.last_heartbeat_at + policy.watchdog_after
        return _bad(
            state,
            DataQuality.COMM_ERROR,
            state.value,
            "watchdog_timed_out",
            occurred_at,
            evidence,
        )
    if (
        state.last_good_at is not None
        and at - state.last_good_at >= policy.stale_after
        and state.quality is DataQuality.GOOD
    ):
        occurred_at = state.last_good_at + policy.stale_after
        return _bad(
            state,
            DataQuality.STALE,
            state.value,
            "stale",
            occurred_at,
            evidence,
        )
    return state, None


def _good(
    state: CommunicationState,
    value: DeviceValue,
    policy: CommunicationPolicy,
    at: datetime,
    evidence: EvidenceClassification,
) -> tuple[CommunicationState, DeviceEvent | None]:
    good_updates = min(state.good_updates + 1, policy.recovery_updates)
    recovered = good_updates == policy.recovery_updates
    quality = DataQuality.GOOD if recovered else DataQuality.COMM_ERROR
    current = replace(
        state,
        quality=quality,
        value=replace(value, quality=quality),
        last_good_at=at if recovered else state.last_good_at,
        good_updates=good_updates,
    )
    if state.quality is DataQuality.GOOD:
        return current, None
    name = "recovered" if recovered else "recovering"
    return current, _event(current.device_id, name, at, evidence)


def _bad(
    state: CommunicationState,
    quality: DataQuality,
    value: DeviceValue | None,
    name: str,
    at: datetime,
    evidence: EvidenceClassification,
) -> tuple[CommunicationState, DeviceEvent]:
    current = replace(
        state,
        quality=quality,
        value=None if value is None else replace(value, quality=quality),
        good_updates=0,
    )
    return current, _event(current.device_id, name, at, evidence)


def _checked_value(
    value: DeviceValue | None,
    policy: CommunicationPolicy,
) -> DeviceValue:
    if value is None:
        raise CommunicationConfigurationError("communication outcome requires a value")
    device_id = policy.behavior_capability.device_id
    if value.source != device_id:
        raise CommunicationConfigurationError(
            "communication value source does not match policy device"
        )
    return value


def _validate_behavior_profile(
    policy: CommunicationPolicy,
    profile: ExecutionProfile,
) -> None:
    capability = policy.behavior_capability
    if not capability.available or profile not in capability.enabled_profiles:
        raise CommunicationConfigurationError(
            "communication behavior is unavailable for the execution profile"
        )


def _validate_fault_injection(
    policy: CommunicationPolicy,
    update: CommunicationUpdate | None,
    profile: ExecutionProfile,
) -> None:
    if update is None or not update.injected:
        return
    if profile is ExecutionProfile.PRODUCTION:
        raise CommunicationConfigurationError(
            "communication fault injection is unavailable in production"
        )
    capability = policy.fault_capability
    if not capability.available or profile not in capability.enabled_profiles:
        raise CommunicationConfigurationError(
            "communication fault injection is unavailable for the execution profile"
        )


def _status(state: CommunicationState) -> ComponentStatus:
    if state.quality is DataQuality.GOOD:
        return ComponentStatus.HEALTHY
    if state.quality is DataQuality.COMM_ERROR and (not state.connected or state.good_updates == 0):
        return ComponentStatus.UNAVAILABLE
    return ComponentStatus.DEGRADED


def _event(
    device_id: str,
    name: str,
    at: datetime,
    evidence: EvidenceClassification,
) -> DeviceEvent:
    return DeviceEvent(
        event_id=f"communication:{device_id}:{name}:{at.isoformat()}",
        category="device_communication",
        message=f"{device_id} communication {name.replace('_', ' ')}",
        occurred_at=at,
        source="communication-processor",
        evidence_classification=evidence,
    )


def _validate_duration(
    value: timedelta,
    name: str,
    *,
    allow_zero: bool = False,
) -> None:
    seconds = value.total_seconds()
    if not isfinite(seconds) or seconds < 0 or (seconds == 0 and not allow_zero):
        requirement = "non-negative" if allow_zero else "positive"
        raise CommunicationConfigurationError(f"{name} must be a finite {requirement} duration")
