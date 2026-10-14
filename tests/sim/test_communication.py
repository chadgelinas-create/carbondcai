"""Deterministic communication health and recovery tests."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta

import pytest

from skid2.contracts import ComponentStatus, DataQuality
from skid2.sim import (
    CapabilityKind,
    CommunicationBatch,
    CommunicationConfigurationError,
    CommunicationOutcome,
    CommunicationPolicy,
    CommunicationState,
    CommunicationUpdate,
    DeviceCapability,
    DeviceLifecycle,
    DeviceValue,
    EvidenceClassification,
    ExecutionProfile,
    FidelityLevel,
    process_communication,
)

NOW = datetime(2026, 8, 12, 10, tzinfo=UTC)


def capability(
    device_id: str,
    kind: CapabilityKind,
    *,
    profiles: frozenset[ExecutionProfile] = frozenset({ExecutionProfile.STAGE_0}),
    available: bool = True,
) -> DeviceCapability:
    evidence = (
        EvidenceClassification.SOURCED
        if ExecutionProfile.PRODUCTION in profiles
        else EvidenceClassification.PROVISIONAL
    )
    return DeviceCapability(
        f"{device_id}.{kind.value}.communication",
        device_id,
        kind,
        evidence,
        FidelityLevel.LEVEL_2,
        "stage0_test" if evidence is EvidenceClassification.PROVISIONAL else "approved",
        available=available,
        enabled_profiles=profiles if available else frozenset(),
        assumptions=("Communication timing is a test policy",),
    )


def policy(
    device_id: str = "device-a",
    *,
    recovery_updates: int = 1,
    profiles: frozenset[ExecutionProfile] = frozenset({ExecutionProfile.STAGE_0}),
    fault_available: bool = True,
) -> CommunicationPolicy:
    return CommunicationPolicy(
        capability(device_id, CapabilityKind.BEHAVIOR, profiles=profiles),
        capability(
            device_id,
            CapabilityKind.FAULT,
            profiles=profiles,
            available=fault_available,
        ),
        timeout=timedelta(seconds=2),
        stale_after=timedelta(seconds=5),
        watchdog_after=timedelta(seconds=10),
        recovery_updates=recovery_updates,
    )


def value(
    device_id: str = "device-a",
    number: float = 0.0,
    *,
    quality: DataQuality = DataQuality.GOOD,
) -> DeviceValue:
    return DeviceValue(
        f"{device_id}.power",
        number,
        "kW",
        quality,
        device_id,
        EvidenceClassification.PROVISIONAL,
    )


def run(
    selected_policy: CommunicationPolicy,
    *,
    state: CommunicationState | None = None,
    update: CommunicationUpdate | None = None,
    at: datetime = NOW,
    profile: ExecutionProfile = ExecutionProfile.STAGE_0,
) -> CommunicationBatch:
    selected_state = (
        CommunicationState(selected_policy.behavior_capability.device_id)
        if state is None
        else state
    )
    return process_communication(
        selected_state,
        update,
        selected_policy,
        DeviceLifecycle.READY,
        profile,
        at,
    )


def test_valid_zero_is_good_and_not_missing_communication() -> None:
    batch = run(
        policy(),
        update=CommunicationUpdate(
            CommunicationOutcome.SUCCESS,
            timedelta(0),
            value(number=0.0),
        ),
    )

    assert batch.value is not None
    assert batch.value.value == 0.0
    assert batch.value.quality is DataQuality.GOOD
    assert batch.health.status is ComponentStatus.HEALTHY
    assert batch.state.last_request_at == NOW
    assert batch.state.last_response_at == NOW
    assert batch.state.last_heartbeat_at == NOW


def test_stale_and_watchdog_change_at_exact_boundaries() -> None:
    selected_policy = policy()
    good = run(
        selected_policy,
        update=CommunicationUpdate(
            CommunicationOutcome.SUCCESS,
            timedelta(0),
            value(),
        ),
    )
    before = run(
        selected_policy,
        state=good.state,
        at=NOW + timedelta(seconds=4, milliseconds=999),
    )
    stale = run(
        selected_policy,
        state=before.state,
        at=NOW + timedelta(seconds=5),
    )
    watchdog = run(
        selected_policy,
        state=stale.state,
        at=NOW + timedelta(seconds=10),
    )

    assert before.health.quality is DataQuality.GOOD
    assert stale.health.quality is DataQuality.STALE
    assert stale.value is not None
    assert stale.value.value == 0.0
    assert stale.events[0].occurred_at == NOW + timedelta(seconds=5)
    assert watchdog.health.quality is DataQuality.COMM_ERROR
    assert watchdog.health.status is ComponentStatus.UNAVAILABLE
    assert watchdog.events[0].occurred_at == NOW + timedelta(seconds=10)


def test_response_at_timeout_is_accepted_and_late_response_fails() -> None:
    selected_policy = policy()
    exact = run(
        selected_policy,
        update=CommunicationUpdate(
            CommunicationOutcome.SUCCESS,
            timedelta(seconds=2),
            value(),
        ),
    )
    late = run(
        selected_policy,
        update=CommunicationUpdate(
            CommunicationOutcome.SUCCESS,
            timedelta(seconds=2, microseconds=1),
            value(),
        ),
    )

    assert exact.health.quality is DataQuality.GOOD
    assert late.health.quality is DataQuality.COMM_ERROR
    assert late.state.last_response_at is None
    assert late.events[0].message == "device-a communication timed out"


def test_dropped_request_and_response_keep_distinct_transaction_state() -> None:
    selected_policy = policy()
    request = run(
        selected_policy,
        update=CommunicationUpdate(
            CommunicationOutcome.DROP_REQUEST,
            timedelta(seconds=1),
            injected=True,
        ),
    )
    response = run(
        selected_policy,
        update=CommunicationUpdate(
            CommunicationOutcome.DROP_RESPONSE,
            timedelta(seconds=1),
            injected=True,
        ),
    )

    assert request.state.last_request_at is None
    assert response.state.last_request_at == NOW - timedelta(seconds=1)
    assert request.state.last_response_at is None
    assert response.state.last_response_at is None
    assert request.health.quality is DataQuality.COMM_ERROR
    assert response.health.quality is DataQuality.COMM_ERROR


@pytest.mark.parametrize(
    ("outcome", "expected_quality"),
    [
        (CommunicationOutcome.EXCEPTION, DataQuality.COMM_ERROR),
        (CommunicationOutcome.CORRUPT, DataQuality.INVALID),
        (CommunicationOutcome.OUT_OF_RANGE, DataQuality.OUT_OF_RANGE),
    ],
)
def test_bad_outcomes_never_return_good_quality(
    outcome: CommunicationOutcome,
    expected_quality: DataQuality,
) -> None:
    sample = None if outcome is CommunicationOutcome.EXCEPTION else value(number=7)
    batch = run(
        policy(),
        update=CommunicationUpdate(
            outcome,
            timedelta(0),
            sample,
            injected=True,
        ),
    )

    assert batch.health.quality is expected_quality
    if batch.value is not None:
        assert batch.value.quality is expected_quality
    assert batch.events[0].evidence_classification is (EvidenceClassification.PROVISIONAL)


def test_stuck_update_preserves_value_and_marks_it_stale() -> None:
    selected_policy = policy()
    good = run(
        selected_policy,
        update=CommunicationUpdate(
            CommunicationOutcome.SUCCESS,
            timedelta(0),
            value(number=12),
        ),
    )
    stuck = run(
        selected_policy,
        state=good.state,
        update=CommunicationUpdate(
            CommunicationOutcome.STUCK,
            timedelta(0),
            injected=True,
        ),
        at=NOW + timedelta(seconds=1),
    )

    assert stuck.value is not None
    assert stuck.value.value == 12
    assert stuck.value.quality is DataQuality.STALE
    assert stuck.state.last_good_at == NOW
    assert stuck.state.last_heartbeat_at == NOW + timedelta(seconds=1)


def test_reconnect_requires_configured_good_updates() -> None:
    selected_policy = policy(recovery_updates=2)
    first = run(
        selected_policy,
        update=CommunicationUpdate(
            CommunicationOutcome.SUCCESS,
            timedelta(0),
            value(),
        ),
    )
    healthy = run(
        selected_policy,
        state=first.state,
        update=CommunicationUpdate(
            CommunicationOutcome.SUCCESS,
            timedelta(0),
            value(),
        ),
        at=NOW + timedelta(seconds=1),
    )
    disconnected = run(
        selected_policy,
        state=healthy.state,
        update=CommunicationUpdate(
            CommunicationOutcome.DISCONNECT,
            timedelta(0),
            injected=True,
        ),
        at=NOW + timedelta(seconds=2),
    )
    reconnected = run(
        selected_policy,
        state=disconnected.state,
        update=CommunicationUpdate(
            CommunicationOutcome.RECONNECT,
            timedelta(0),
            injected=True,
        ),
        at=NOW + timedelta(seconds=3),
    )
    recovering = run(
        selected_policy,
        state=reconnected.state,
        update=CommunicationUpdate(
            CommunicationOutcome.SUCCESS,
            timedelta(0),
            value(),
        ),
        at=NOW + timedelta(seconds=4),
    )
    recovered = run(
        selected_policy,
        state=recovering.state,
        update=CommunicationUpdate(
            CommunicationOutcome.SUCCESS,
            timedelta(0),
            value(),
        ),
        at=NOW + timedelta(seconds=5),
    )

    assert disconnected.state.connected is False
    assert reconnected.state.connected is True
    assert reconnected.health.quality is DataQuality.COMM_ERROR
    assert recovering.health.status is ComponentStatus.DEGRADED
    assert recovering.health.quality is DataQuality.COMM_ERROR
    assert recovered.health.status is ComponentStatus.HEALTHY
    assert recovered.health.quality is DataQuality.GOOD


def test_disconnected_device_rejects_updates_until_reconnect() -> None:
    selected_policy = policy()
    disconnected = run(
        selected_policy,
        update=CommunicationUpdate(
            CommunicationOutcome.DISCONNECT,
            timedelta(0),
            injected=True,
        ),
    )

    with pytest.raises(
        CommunicationConfigurationError,
        match="requires reconnect",
    ):
        run(
            selected_policy,
            state=disconnected.state,
            update=CommunicationUpdate(
                CommunicationOutcome.SUCCESS,
                timedelta(0),
                value(),
            ),
            at=NOW + timedelta(seconds=1),
        )


def test_injected_fault_is_always_rejected_in_production() -> None:
    production = frozenset({ExecutionProfile.PRODUCTION})
    selected_policy = policy(profiles=production)

    observed = run(
        selected_policy,
        update=CommunicationUpdate(
            CommunicationOutcome.EXCEPTION,
            timedelta(0),
        ),
        profile=ExecutionProfile.PRODUCTION,
    )

    assert observed.health.quality is DataQuality.COMM_ERROR
    with pytest.raises(
        CommunicationConfigurationError,
        match="unavailable in production",
    ):
        run(
            selected_policy,
            update=CommunicationUpdate(
                CommunicationOutcome.EXCEPTION,
                timedelta(0),
                injected=True,
            ),
            profile=ExecutionProfile.PRODUCTION,
        )


def test_fault_injection_requires_an_enabled_fault_capability() -> None:
    with pytest.raises(
        CommunicationConfigurationError,
        match="fault injection is unavailable",
    ):
        run(
            policy(fault_available=False),
            update=CommunicationUpdate(
                CommunicationOutcome.DISCONNECT,
                timedelta(0),
                injected=True,
            ),
        )


def test_one_device_failure_does_not_change_another_device() -> None:
    first_policy = policy("device-a")
    second_policy = policy("device-b")
    first_good = run(
        first_policy,
        update=CommunicationUpdate(
            CommunicationOutcome.SUCCESS,
            timedelta(0),
            value("device-a"),
        ),
    )
    second_good = run(
        second_policy,
        update=CommunicationUpdate(
            CommunicationOutcome.SUCCESS,
            timedelta(0),
            value("device-b"),
        ),
    )

    first_failed = run(
        first_policy,
        state=first_good.state,
        update=CommunicationUpdate(
            CommunicationOutcome.EXCEPTION,
            timedelta(0),
            injected=True,
        ),
        at=NOW + timedelta(seconds=1),
    )
    second_unchanged = run(
        second_policy,
        state=second_good.state,
        at=NOW + timedelta(seconds=1),
    )

    assert first_failed.health.quality is DataQuality.COMM_ERROR
    assert second_unchanged.health.quality is DataQuality.GOOD
    assert second_unchanged.value == second_good.value


def test_replay_is_equal_and_state_is_immutable() -> None:
    selected_policy = policy()
    update = CommunicationUpdate(
        CommunicationOutcome.CORRUPT,
        timedelta(milliseconds=20),
        value(number=3),
        injected=True,
    )

    first = run(selected_policy, update=update)
    second = run(selected_policy, update=update)

    assert first == second
    with pytest.raises(FrozenInstanceError):
        first.state.connected = False  # type: ignore[misc]


def test_invalid_policy_state_source_and_time_fail_closed() -> None:
    with pytest.raises(
        CommunicationConfigurationError,
        match="stale threshold",
    ):
        CommunicationPolicy(
            capability("device-a", CapabilityKind.BEHAVIOR),
            capability("device-a", CapabilityKind.FAULT),
            timedelta(seconds=1),
            timedelta(seconds=5),
            timedelta(seconds=5),
        )

    selected_policy = policy()
    with pytest.raises(
        CommunicationConfigurationError,
        match="state does not match",
    ):
        run(selected_policy, state=CommunicationState("device-b"))

    good = run(
        selected_policy,
        update=CommunicationUpdate(
            CommunicationOutcome.SUCCESS,
            timedelta(0),
            value(),
        ),
    )
    with pytest.raises(
        CommunicationConfigurationError,
        match="must not move backwards",
    ):
        run(
            selected_policy,
            state=good.state,
            at=NOW - timedelta(microseconds=1),
        )

    with pytest.raises(
        CommunicationConfigurationError,
        match="source does not match",
    ):
        run(
            selected_policy,
            update=CommunicationUpdate(
                CommunicationOutcome.SUCCESS,
                timedelta(0),
                value("device-b"),
            ),
        )


def test_success_requires_good_input_and_stuck_requires_trusted_history() -> None:
    selected_policy = policy()
    with pytest.raises(
        CommunicationConfigurationError,
        match="requires a good input value",
    ):
        run(
            selected_policy,
            update=CommunicationUpdate(
                CommunicationOutcome.SUCCESS,
                timedelta(0),
                value(quality=DataQuality.STALE),
            ),
        )

    with pytest.raises(
        CommunicationConfigurationError,
        match="requires a previous trusted value",
    ):
        run(
            selected_policy,
            update=CommunicationUpdate(
                CommunicationOutcome.STUCK,
                timedelta(0),
                injected=True,
            ),
        )
