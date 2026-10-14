"""Reusable deterministic simulator command-processing boundary tests."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from skid2.contracts import DataQuality
from skid2.sim import (
    AcknowledgementOutcome,
    AcknowledgementPolicy,
    CapabilityKind,
    CommandBatch,
    CommandContext,
    CommandDisposition,
    CommandPolicy,
    CommandProcessorConfigurationError,
    CommandProcessorState,
    DeviceCapability,
    DeviceCommand,
    DeviceLifecycle,
    DeviceValue,
    DynamicCommandLimit,
    EvidenceClassification,
    ExecutionProfile,
    FidelityLevel,
    NumericCommandRule,
    process_commands,
)

NOW = datetime(2026, 8, 7, 10, tzinfo=UTC)
CAPABILITY_ID = "fake.command.power"


def make_capability(
    *,
    available: bool = True,
    evidence: EvidenceClassification = EvidenceClassification.PROVISIONAL,
    profiles: frozenset[ExecutionProfile] = frozenset({ExecutionProfile.STAGE_0}),
) -> DeviceCapability:
    return DeviceCapability(
        CAPABILITY_ID,
        "fake-1",
        CapabilityKind.COMMAND,
        evidence,
        FidelityLevel.CONTRACT,
        "stage0_test",
        available=available,
        enabled_profiles=profiles if available else frozenset(),
        assumptions=("Contract-test behavior only",),
    )


def acknowledgement(
    outcome: AcknowledgementOutcome = AcknowledgementOutcome.ACKNOWLEDGE,
    *,
    delay: timedelta | None = timedelta(0),
    timeout: timedelta = timedelta(seconds=10),
    evidence: EvidenceClassification = EvidenceClassification.PROVISIONAL,
) -> AcknowledgementPolicy:
    return AcknowledgementPolicy(outcome, timeout, delay, evidence)


DEFAULT_ACKNOWLEDGEMENT = acknowledgement()
DEFAULT_NUMERIC_RULE = NumericCommandRule("kW", Decimal("-10"), Decimal("10"))


def policy(
    *,
    capability: DeviceCapability | None = None,
    metadata_status: str | None = "candidate",
    writable: bool = True,
    allowed_lifecycles: frozenset[DeviceLifecycle] = frozenset(
        {DeviceLifecycle.READY, DeviceLifecycle.ACTIVE}
    ),
    allowed_modes: frozenset[str] = frozenset({"remote"}),
    prerequisites: frozenset[str] = frozenset({"interlock.closed"}),
    minimum_spacing: timedelta = timedelta(0),
    acknowledgement: AcknowledgementPolicy | None = DEFAULT_ACKNOWLEDGEMENT,
    numeric_rule: NumericCommandRule | None = DEFAULT_NUMERIC_RULE,
) -> CommandPolicy:
    return CommandPolicy(
        capability=capability if capability is not None else make_capability(),
        metadata_status=metadata_status,
        writable=writable,
        allowed_lifecycles=allowed_lifecycles,
        allowed_modes=allowed_modes,
        prerequisites=prerequisites,
        minimum_spacing=minimum_spacing,
        acknowledgement=acknowledgement,
        numeric_rule=numeric_rule,
    )


def context(
    *,
    profile: ExecutionProfile = ExecutionProfile.STAGE_0,
    lifecycle: DeviceLifecycle = DeviceLifecycle.READY,
    mode: str | None = "remote",
    satisfied_prerequisites: frozenset[str] = frozenset({"interlock.closed"}),
    dynamic_limits: tuple[DynamicCommandLimit, ...] = (),
    current_values: tuple[tuple[str, Decimal], ...] = (),
) -> CommandContext:
    return CommandContext(
        profile,
        lifecycle,
        mode,
        satisfied_prerequisites,
        dynamic_limits,
        current_values,
    )


def argument(value: bool | int | float | str, *, unit: str = "kW") -> DeviceValue:
    return DeviceValue(
        "fake.command.argument",
        value,
        unit,
        DataQuality.GOOD,
        "test",
        EvidenceClassification.PROVISIONAL,
    )


def command(command_id: str, value: bool | int | float | str = 0) -> DeviceCommand:
    return DeviceCommand(command_id, CAPABILITY_ID, NOW, (argument(value),))


def run(
    *commands: DeviceCommand,
    state: CommandProcessorState | None = None,
    selected_policy: CommandPolicy | None = None,
    selected_context: CommandContext | None = None,
    at: datetime = NOW,
    dt: timedelta = timedelta(seconds=1),
) -> CommandBatch:
    policies = () if selected_policy is None else (selected_policy,)
    return process_commands(
        CommandProcessorState() if state is None else state,
        tuple(commands),
        policies,
        context() if selected_context is None else selected_context,
        at,
        dt,
    )


def dispositions(batch: CommandBatch) -> tuple[CommandDisposition, ...]:
    return tuple(result.disposition for result in batch.results)


@pytest.mark.parametrize(
    ("selected_policy", "expected"),
    [
        (None, CommandDisposition.REJECTED_UNSUPPORTED),
        (policy(writable=False), CommandDisposition.REJECTED_READ_ONLY),
        (policy(metadata_status="blocked"), CommandDisposition.REJECTED_UNAPPROVED),
        (
            policy(capability=make_capability(available=False)),
            CommandDisposition.REJECTED_UNSUPPORTED,
        ),
    ],
)
def test_missing_read_only_blocked_and_unavailable_commands_fail_closed(
    selected_policy: CommandPolicy | None,
    expected: CommandDisposition,
) -> None:
    batch = run(command("command-1"), selected_policy=selected_policy)

    assert dispositions(batch) == (expected,)
    assert batch.effects == ()
    assert batch.state.pending == ()


@pytest.mark.parametrize(
    "selected_context",
    [
        context(lifecycle=DeviceLifecycle.FAULTED),
        context(mode="local"),
        context(satisfied_prerequisites=frozenset()),
    ],
)
def test_wrong_lifecycle_mode_and_prerequisite_produce_no_effect(
    selected_context: CommandContext,
) -> None:
    batch = run(
        command("command-1"),
        selected_policy=policy(),
        selected_context=selected_context,
    )

    assert dispositions(batch) == (CommandDisposition.REJECTED_PREREQUISITE,)
    assert batch.effects == ()


@pytest.mark.parametrize("value", [-10, 10])
def test_static_exact_minimum_and_maximum_are_acknowledged(value: int) -> None:
    batch = run(command(f"command-{value}", value), selected_policy=policy())

    assert dispositions(batch) == (
        CommandDisposition.ACCEPTED,
        CommandDisposition.ACKNOWLEDGED,
    )
    assert len(batch.effects) == 1
    assert batch.effects[0].effective_argument is not None
    assert batch.effects[0].effective_argument.value == value


@pytest.mark.parametrize("value", [-10.01, 10.01])
def test_values_just_outside_static_bounds_are_rejected(value: float) -> None:
    batch = run(command("outside", value), selected_policy=policy())

    assert dispositions(batch) == (CommandDisposition.REJECTED_OUT_OF_RANGE,)
    assert batch.effects == ()


def test_dynamic_limits_narrow_static_limits() -> None:
    selected_context = context(
        dynamic_limits=(DynamicCommandLimit(CAPABILITY_ID, Decimal("-4"), Decimal("4")),)
    )

    batch = run(
        command("dynamic-outside", 5),
        selected_policy=policy(),
        selected_context=selected_context,
    )

    assert dispositions(batch) == (CommandDisposition.REJECTED_OUT_OF_RANGE,)
    assert batch.effects == ()


def test_explicit_dynamic_saturation_is_applied_once() -> None:
    selected_context = context(
        dynamic_limits=(DynamicCommandLimit(CAPABILITY_ID, Decimal("-4"), Decimal("4")),)
    )
    selected_policy = policy(
        numeric_rule=NumericCommandRule(
            "kW",
            Decimal("-10"),
            Decimal("10"),
            saturate_dynamic_limit=True,
        )
    )

    batch = run(
        command("saturated", 8),
        selected_policy=selected_policy,
        selected_context=selected_context,
    )

    assert len(batch.effects) == 1
    assert batch.effects[0].effective_argument is not None
    assert batch.effects[0].effective_argument.value == 4


def test_ramp_requires_current_value_and_clamps_from_authoritative_value() -> None:
    selected_policy = policy(
        numeric_rule=NumericCommandRule(
            "kW",
            Decimal("-10"),
            Decimal("10"),
            saturate_dynamic_limit=True,
            ramp_per_second=Decimal("2"),
        )
    )
    missing = run(command("missing-current", 9), selected_policy=selected_policy)
    supplied = run(
        command("supplied-current", 9),
        selected_policy=selected_policy,
        selected_context=context(current_values=((CAPABILITY_ID, Decimal("0")),)),
        dt=timedelta(seconds=2),
    )

    assert dispositions(missing) == (CommandDisposition.REJECTED_PREREQUISITE,)
    assert missing.effects == ()
    assert supplied.effects[0].effective_argument is not None
    assert supplied.effects[0].effective_argument.value == 4


def test_candidate_non_ram_spacing_rejects_before_and_accepts_at_5001_ms() -> None:
    selected_policy = policy(minimum_spacing=timedelta(milliseconds=5001))
    first = run(command("first"), selected_policy=selected_policy)
    too_soon = run(
        command("too-soon"),
        state=first.state,
        selected_policy=selected_policy,
        at=NOW + timedelta(milliseconds=5000),
    )
    exact = run(
        command("exact"),
        state=too_soon.state,
        selected_policy=selected_policy,
        at=NOW + timedelta(milliseconds=5001),
    )

    assert dispositions(too_soon) == (CommandDisposition.REJECTED_CADENCE,)
    assert too_soon.effects == ()
    assert dispositions(exact) == (
        CommandDisposition.ACCEPTED,
        CommandDisposition.ACKNOWLEDGED,
    )


def test_delayed_acknowledgement_uses_manual_time_without_sleep() -> None:
    selected_policy = policy(acknowledgement=acknowledgement(delay=timedelta(seconds=3)))
    accepted = run(command("delayed"), selected_policy=selected_policy)
    before = run(
        state=accepted.state,
        selected_policy=selected_policy,
        at=NOW + timedelta(seconds=2, milliseconds=999),
    )
    exact = run(
        state=before.state,
        selected_policy=selected_policy,
        at=NOW + timedelta(seconds=3),
    )

    assert dispositions(accepted) == (CommandDisposition.ACCEPTED,)
    assert accepted.effects == ()
    assert before.results == ()
    assert dispositions(exact) == (CommandDisposition.ACKNOWLEDGED,)
    assert exact.results[0].occurred_at == NOW + timedelta(seconds=3)
    assert len(exact.effects) == 1


def test_configured_device_rejection_and_timeout_return_no_effect() -> None:
    rejected_policy = policy(
        acknowledgement=acknowledgement(
            AcknowledgementOutcome.REJECT,
            delay=timedelta(seconds=1),
        )
    )
    rejected_pending = run(command("rejected"), selected_policy=rejected_policy)
    rejected = run(
        state=rejected_pending.state,
        selected_policy=rejected_policy,
        at=NOW + timedelta(seconds=1),
    )
    timeout_policy = policy(
        acknowledgement=acknowledgement(
            AcknowledgementOutcome.TIMEOUT,
            delay=None,
            timeout=timedelta(seconds=2),
        )
    )
    timeout_pending = run(command("timeout"), selected_policy=timeout_policy)
    timed_out = run(
        state=timeout_pending.state,
        selected_policy=timeout_policy,
        at=NOW + timedelta(seconds=2),
    )

    assert dispositions(rejected) == (CommandDisposition.REJECTED_DEVICE,)
    assert rejected.effects == ()
    assert dispositions(timed_out) == (CommandDisposition.TIMED_OUT,)
    assert timed_out.effects == ()
    assert timed_out.results[0].occurred_at == NOW + timedelta(seconds=2)


def test_timeout_can_recover_with_new_command_id() -> None:
    timeout_policy = policy(
        acknowledgement=acknowledgement(
            AcknowledgementOutcome.TIMEOUT,
            delay=None,
            timeout=timedelta(seconds=1),
        )
    )
    pending = run(command("first"), selected_policy=timeout_policy)
    timed_out = run(
        state=pending.state,
        selected_policy=timeout_policy,
        at=NOW + timedelta(seconds=1),
    )
    recovered = run(
        command("recovery"),
        state=timed_out.state,
        selected_policy=policy(),
        at=NOW + timedelta(seconds=2),
    )

    assert dispositions(recovered) == (
        CommandDisposition.ACCEPTED,
        CommandDisposition.ACKNOWLEDGED,
    )
    assert len(recovered.effects) == 1


def test_duplicate_id_never_executes_twice() -> None:
    first = run(command("same", 1), selected_policy=policy())
    duplicate = run(
        command("same", 9),
        state=first.state,
        selected_policy=policy(),
        at=NOW + timedelta(seconds=1),
    )

    assert dispositions(duplicate) == (CommandDisposition.DUPLICATE,)
    assert duplicate.effects == ()


def test_valid_new_command_supersedes_pending_same_capability() -> None:
    selected_policy = policy(acknowledgement=acknowledgement(delay=timedelta(seconds=5)))
    first = run(command("first", 1), selected_policy=selected_policy)
    replacement = run(
        command("replacement", 2),
        state=first.state,
        selected_policy=selected_policy,
        at=NOW + timedelta(seconds=1),
    )

    assert dispositions(replacement) == (
        CommandDisposition.SUPERSEDED,
        CommandDisposition.ACCEPTED,
    )
    assert tuple(item.command.command_id for item in replacement.state.pending) == ("replacement",)
    assert replacement.effects == ()


def test_invalid_new_command_does_not_supersede_valid_pending_command() -> None:
    selected_policy = policy(acknowledgement=acknowledgement(delay=timedelta(seconds=5)))
    first = run(command("first", 1), selected_policy=selected_policy)
    invalid = run(
        command("invalid", 11),
        state=first.state,
        selected_policy=selected_policy,
        at=NOW + timedelta(seconds=1),
    )

    assert dispositions(invalid) == (CommandDisposition.REJECTED_OUT_OF_RANGE,)
    assert tuple(item.command.command_id for item in invalid.state.pending) == ("first",)


def test_missing_acknowledgement_fails_closed_and_provisional_ack_is_stage0_only() -> None:
    missing = run(command("missing"), selected_policy=policy(acknowledgement=None))
    sourced_capability = make_capability(
        evidence=EvidenceClassification.SOURCED,
        profiles=frozenset({ExecutionProfile.STAGE_0, ExecutionProfile.HIL}),
    )
    hil = run(
        command("hil"),
        selected_policy=policy(capability=sourced_capability),
        selected_context=context(profile=ExecutionProfile.HIL),
    )

    assert dispositions(missing) == (CommandDisposition.REJECTED_UNAPPROVED,)
    assert dispositions(hil) == (CommandDisposition.REJECTED_UNAPPROVED,)
    assert missing.effects == hil.effects == ()


def test_candidate_metadata_is_stage0_only_even_with_sourced_acknowledgement() -> None:
    sourced_capability = make_capability(
        evidence=EvidenceClassification.SOURCED,
        profiles=frozenset({ExecutionProfile.STAGE_0, ExecutionProfile.HIL}),
    )
    sourced_acknowledgement = acknowledgement(evidence=EvidenceClassification.SOURCED)

    batch = run(
        command("hil-candidate"),
        selected_policy=policy(
            capability=sourced_capability,
            acknowledgement=sourced_acknowledgement,
        ),
        selected_context=context(profile=ExecutionProfile.HIL),
    )

    assert dispositions(batch) == (CommandDisposition.REJECTED_UNAPPROVED,)
    assert batch.results[0].reason == "candidate command metadata is limited to Stage 0"
    assert batch.effects == ()


def test_events_preserve_correlation_without_argument_values() -> None:
    secret_value = 7.39281
    batch = run(command("correlation-123", secret_value), selected_policy=policy())

    assert all("correlation-123" in event.message for event in batch.events)
    assert all(str(secret_value) not in event.message for event in batch.events)
    assert all(event.category == "device_command" for event in batch.events)


def test_identical_inputs_produce_equal_immutable_batches() -> None:
    first = run(command("replay", 3), selected_policy=policy())
    second = run(command("replay", 3), selected_policy=policy())

    assert first == second
    with pytest.raises(FrozenInstanceError):
        first.state.pending = ()  # type: ignore[misc]


def test_invalid_argument_precedes_lifecycle_and_rejected_id_is_remembered() -> None:
    invalid = DeviceCommand("invalid", CAPABILITY_ID, NOW, ())
    first = run(
        invalid,
        selected_policy=policy(),
        selected_context=context(lifecycle=DeviceLifecycle.FAULTED),
    )
    duplicate = run(invalid, state=first.state, selected_policy=policy())

    assert dispositions(first) == (CommandDisposition.REJECTED_PREREQUISITE,)
    assert first.results[0].reason == "numeric command requires exactly one argument"
    assert dispositions(duplicate) == (CommandDisposition.DUPLICATE,)


def test_configuration_rejects_duplicate_policy_and_broadened_dynamic_limit() -> None:
    with pytest.raises(CommandProcessorConfigurationError, match="duplicate command policy"):
        process_commands(
            CommandProcessorState(),
            (),
            (policy(), policy()),
            context(),
            NOW,
            timedelta(seconds=1),
        )
    with pytest.raises(CommandProcessorConfigurationError, match="must not broaden"):
        run(
            command("broadened"),
            selected_policy=policy(),
            selected_context=context(
                dynamic_limits=(DynamicCommandLimit(CAPABILITY_ID, Decimal("-11"), Decimal("10")),)
            ),
        )
