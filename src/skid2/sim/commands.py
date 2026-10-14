"""Deterministic, transport-independent device command processing."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from math import isfinite

from skid2.contracts import DataQuality
from skid2.contracts.domain import normalize_utc
from skid2.sim.device import (
    CapabilityKind,
    CommandDisposition,
    DeviceCapability,
    DeviceCommand,
    DeviceCommandResult,
    DeviceEvent,
    DeviceLifecycle,
    DeviceValue,
    EvidenceClassification,
    ExecutionProfile,
)


class CommandProcessorConfigurationError(ValueError):
    """A command policy or context is ambiguous or internally inconsistent."""


class AcknowledgementOutcome(StrEnum):
    ACKNOWLEDGE = "acknowledge"
    REJECT = "reject"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class AcknowledgementPolicy:
    outcome: AcknowledgementOutcome
    timeout: timedelta
    response_delay: timedelta | None
    evidence_classification: EvidenceClassification

    def __post_init__(self) -> None:
        _validate_duration(self.timeout, "acknowledgement timeout", allow_zero=False)
        if self.evidence_classification in {
            EvidenceClassification.BLOCKED,
            EvidenceClassification.UNSUPPORTED,
        }:
            raise CommandProcessorConfigurationError(
                "acknowledgement policy requires usable evidence"
            )
        if self.outcome is AcknowledgementOutcome.TIMEOUT:
            if self.response_delay is not None:
                raise CommandProcessorConfigurationError(
                    "timeout outcome cannot define a response delay"
                )
            return
        if self.response_delay is None:
            raise CommandProcessorConfigurationError(
                "acknowledge and reject outcomes require a response delay"
            )
        _validate_duration(
            self.response_delay,
            "acknowledgement response delay",
            allow_zero=True,
        )
        if self.response_delay > self.timeout:
            raise CommandProcessorConfigurationError(
                "acknowledgement response delay cannot exceed timeout"
            )


@dataclass(frozen=True, slots=True)
class NumericCommandRule:
    unit: str
    minimum: Decimal
    maximum: Decimal
    saturate_dynamic_limit: bool = False
    ramp_per_second: Decimal | None = None

    def __post_init__(self) -> None:
        if not self.unit.strip():
            raise CommandProcessorConfigurationError("numeric command unit must not be empty")
        _validate_decimal(self.minimum, "numeric minimum")
        _validate_decimal(self.maximum, "numeric maximum")
        if self.minimum > self.maximum:
            raise CommandProcessorConfigurationError(
                "numeric minimum cannot exceed numeric maximum"
            )
        if self.ramp_per_second is not None:
            _validate_decimal(self.ramp_per_second, "ramp per second")
            if self.ramp_per_second <= 0:
                raise CommandProcessorConfigurationError("ramp per second must be positive")


@dataclass(frozen=True, slots=True)
class DynamicCommandLimit:
    capability_id: str
    minimum: Decimal
    maximum: Decimal

    def __post_init__(self) -> None:
        if not self.capability_id.strip():
            raise CommandProcessorConfigurationError(
                "dynamic limit capability_id must not be empty"
            )
        _validate_decimal(self.minimum, "dynamic minimum")
        _validate_decimal(self.maximum, "dynamic maximum")
        if self.minimum > self.maximum:
            raise CommandProcessorConfigurationError(
                "dynamic minimum cannot exceed dynamic maximum"
            )


@dataclass(frozen=True, slots=True)
class CommandPolicy:
    capability: DeviceCapability
    metadata_status: str | None
    writable: bool
    allowed_lifecycles: frozenset[DeviceLifecycle]
    allowed_modes: frozenset[str]
    prerequisites: frozenset[str]
    minimum_spacing: timedelta
    acknowledgement: AcknowledgementPolicy | None
    numeric_rule: NumericCommandRule | None = None

    def __post_init__(self) -> None:
        if self.capability.kind is not CapabilityKind.COMMAND:
            raise CommandProcessorConfigurationError(
                "command policy capability must have command kind"
            )
        _validate_duration(self.minimum_spacing, "minimum write spacing", allow_zero=True)


@dataclass(frozen=True, slots=True)
class CommandContext:
    profile: ExecutionProfile
    lifecycle: DeviceLifecycle
    mode: str | None
    satisfied_prerequisites: frozenset[str] = frozenset()
    dynamic_limits: tuple[DynamicCommandLimit, ...] = ()
    current_values: tuple[tuple[str, Decimal], ...] = ()


@dataclass(frozen=True, slots=True)
class PendingCommand:
    command: DeviceCommand
    accepted_at: datetime
    response_at: datetime | None
    timeout_at: datetime
    outcome: AcknowledgementOutcome
    effective_argument: DeviceValue | None
    evidence_classification: EvidenceClassification


@dataclass(frozen=True, slots=True)
class CommandWrite:
    capability_id: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class CommandProcessorState:
    seen_command_ids: frozenset[str] = frozenset()
    pending: tuple[PendingCommand, ...] = ()
    last_writes: tuple[CommandWrite, ...] = ()


@dataclass(frozen=True, slots=True)
class CommandEffect:
    command_id: str
    capability_id: str
    effective_argument: DeviceValue | None


@dataclass(frozen=True, slots=True)
class CommandBatch:
    state: CommandProcessorState
    effects: tuple[CommandEffect, ...]
    results: tuple[DeviceCommandResult, ...]
    events: tuple[DeviceEvent, ...]


@dataclass(frozen=True, slots=True)
class _CommandDecision:
    rejection: DeviceCommandResult | None
    effective_argument: DeviceValue | None
    acknowledgement: AcknowledgementPolicy | None
    evidence_classification: EvidenceClassification


def process_commands(
    state: CommandProcessorState,
    commands: tuple[DeviceCommand, ...],
    policies: tuple[CommandPolicy, ...],
    context: CommandContext,
    at: datetime,
    dt: timedelta,
) -> CommandBatch:
    """Advance pending outcomes and validate ordered commands using logical time only."""

    current_at = normalize_utc(at, "at")
    _validate_duration(dt, "dt", allow_zero=False)
    policies_by_id = _index_policies(policies)
    dynamic_limits = _index_dynamic_limits(context.dynamic_limits)
    current_values = _index_current_values(context.current_values)

    current_state, settled_effects, settled_results, settled_events = _settle_pending(
        state, current_at
    )
    effects = list(settled_effects)
    results = list(settled_results)
    events = list(settled_events)

    for command in commands:
        if command.command_id in current_state.seen_command_ids:
            duplicate = _result(
                command,
                CommandDisposition.DUPLICATE,
                "command_id was already observed",
                current_at,
                EvidenceClassification.PROVISIONAL,
            )
            results.append(duplicate)
            events.append(_event(command, duplicate))
            continue

        current_state = replace(
            current_state,
            seen_command_ids=current_state.seen_command_ids | {command.command_id},
        )
        decision = _validate_command(
            command,
            policies_by_id.get(command.capability_id),
            current_state,
            context,
            current_at,
            dt,
            dynamic_limits,
            current_values,
        )
        if decision.rejection is not None:
            results.append(decision.rejection)
            events.append(_event(command, decision.rejection))
            continue

        current_state, superseded_results, superseded_events = _supersede_pending(
            current_state, command.capability_id, current_at
        )
        results.extend(superseded_results)
        events.extend(superseded_events)

        acknowledgement = decision.acknowledgement
        if acknowledgement is None:  # The validator rejects this condition.
            raise AssertionError("validated command is missing acknowledgement policy")
        response_at = (
            None
            if acknowledgement.response_delay is None
            else current_at + acknowledgement.response_delay
        )
        pending = PendingCommand(
            command=command,
            accepted_at=current_at,
            response_at=response_at,
            timeout_at=current_at + acknowledgement.timeout,
            outcome=acknowledgement.outcome,
            effective_argument=decision.effective_argument,
            evidence_classification=decision.evidence_classification,
        )
        current_state = replace(
            current_state,
            pending=(*current_state.pending, pending),
            last_writes=_record_write(
                current_state.last_writes,
                CommandWrite(command.capability_id, current_at),
            ),
        )
        accepted = _result(
            command,
            CommandDisposition.ACCEPTED,
            "command passed validation and awaits configured outcome",
            current_at,
            decision.evidence_classification,
        )
        results.append(accepted)
        events.append(_event(command, accepted))

        current_state, new_effects, newly_settled, new_events = _settle_pending(
            current_state, current_at
        )
        effects.extend(new_effects)
        results.extend(newly_settled)
        events.extend(new_events)

    return CommandBatch(current_state, tuple(effects), tuple(results), tuple(events))


def _validate_command(
    command: DeviceCommand,
    policy: CommandPolicy | None,
    state: CommandProcessorState,
    context: CommandContext,
    at: datetime,
    dt: timedelta,
    dynamic_limits: dict[str, DynamicCommandLimit],
    current_values: dict[str, Decimal],
) -> _CommandDecision:
    if policy is None:
        return _rejected_decision(
            command,
            CommandDisposition.REJECTED_UNSUPPORTED,
            "command policy or capability is unavailable",
            at,
            EvidenceClassification.UNSUPPORTED,
        )
    evidence = policy.capability.evidence_classification
    if not policy.writable:
        return _rejected_decision(
            command,
            CommandDisposition.REJECTED_READ_ONLY,
            "command target is read-only",
            at,
            evidence,
        )
    if policy.metadata_status not in {"candidate", "approved"}:
        return _rejected_decision(
            command,
            CommandDisposition.REJECTED_UNAPPROVED,
            "command metadata is blocked or unapproved",
            at,
            evidence,
        )
    if not policy.capability.available:
        return _rejected_decision(
            command,
            CommandDisposition.REJECTED_UNSUPPORTED,
            "command capability is unavailable",
            at,
            evidence,
        )
    if context.profile not in policy.capability.enabled_profiles:
        return _rejected_decision(
            command,
            CommandDisposition.REJECTED_UNAPPROVED,
            "command capability is disabled for the execution profile",
            at,
            evidence,
        )
    if policy.metadata_status == "candidate" and context.profile is not ExecutionProfile.STAGE_0:
        return _rejected_decision(
            command,
            CommandDisposition.REJECTED_UNAPPROVED,
            "candidate command metadata is limited to Stage 0",
            at,
            evidence,
        )

    parsed_argument, argument_rejection = _parse_argument(
        command, policy.numeric_rule, at, evidence
    )
    if argument_rejection is not None:
        return argument_rejection
    if context.lifecycle not in policy.allowed_lifecycles:
        return _rejected_decision(
            command,
            CommandDisposition.REJECTED_PREREQUISITE,
            "device lifecycle does not permit the command",
            at,
            evidence,
        )
    if policy.allowed_modes and context.mode not in policy.allowed_modes:
        return _rejected_decision(
            command,
            CommandDisposition.REJECTED_PREREQUISITE,
            "device mode does not permit the command",
            at,
            evidence,
        )
    if not policy.prerequisites <= context.satisfied_prerequisites:
        return _rejected_decision(
            command,
            CommandDisposition.REJECTED_PREREQUISITE,
            "required command prerequisite is not satisfied",
            at,
            evidence,
        )

    effective_argument = parsed_argument
    if policy.numeric_rule is not None:
        if parsed_argument is None:
            raise AssertionError("numeric command is missing parsed argument")
        numeric_value = _decimal_value(parsed_argument.value)
        rule = policy.numeric_rule
        if numeric_value < rule.minimum or numeric_value > rule.maximum:
            return _rejected_decision(
                command,
                CommandDisposition.REJECTED_OUT_OF_RANGE,
                "command argument is outside the static range",
                at,
                evidence,
            )
        limited_value, limit_rejection = _apply_dynamic_limits(
            command,
            parsed_argument,
            numeric_value,
            rule,
            dynamic_limits.get(command.capability_id),
            current_values.get(command.capability_id),
            dt,
            at,
            evidence,
        )
        if limit_rejection is not None:
            return limit_rejection
        effective_argument = limited_value

    if not _write_spacing_elapsed(state, policy, at):
        return _rejected_decision(
            command,
            CommandDisposition.REJECTED_CADENCE,
            "minimum command write spacing has not elapsed",
            at,
            evidence,
        )
    acknowledgement = policy.acknowledgement
    if acknowledgement is None:
        return _rejected_decision(
            command,
            CommandDisposition.REJECTED_UNAPPROVED,
            "acknowledgement behavior is not approved",
            at,
            evidence,
        )
    if (
        acknowledgement.evidence_classification is EvidenceClassification.PROVISIONAL
        and context.profile is not ExecutionProfile.STAGE_0
    ):
        return _rejected_decision(
            command,
            CommandDisposition.REJECTED_UNAPPROVED,
            "provisional acknowledgement is limited to Stage 0",
            at,
            EvidenceClassification.PROVISIONAL,
        )
    return _CommandDecision(
        rejection=None,
        effective_argument=effective_argument,
        acknowledgement=acknowledgement,
        evidence_classification=acknowledgement.evidence_classification,
    )


def _parse_argument(
    command: DeviceCommand,
    rule: NumericCommandRule | None,
    at: datetime,
    evidence: EvidenceClassification,
) -> tuple[DeviceValue | None, _CommandDecision | None]:
    if rule is None:
        if command.arguments:
            return None, _rejected_decision(
                command,
                CommandDisposition.REJECTED_PREREQUISITE,
                "command does not accept arguments",
                at,
                evidence,
            )
        return None, None
    if len(command.arguments) != 1:
        return None, _rejected_decision(
            command,
            CommandDisposition.REJECTED_PREREQUISITE,
            "numeric command requires exactly one argument",
            at,
            evidence,
        )
    argument = command.arguments[0]
    if argument.unit != rule.unit:
        return None, _rejected_decision(
            command,
            CommandDisposition.REJECTED_PREREQUISITE,
            "command argument unit does not match policy",
            at,
            evidence,
        )
    if argument.quality is not DataQuality.GOOD:
        return None, _rejected_decision(
            command,
            CommandDisposition.REJECTED_PREREQUISITE,
            "command argument quality is not good",
            at,
            evidence,
        )
    try:
        _decimal_value(argument.value)
    except (InvalidOperation, TypeError, ValueError):
        return None, _rejected_decision(
            command,
            CommandDisposition.REJECTED_PREREQUISITE,
            "command argument must be a finite numeric value",
            at,
            evidence,
        )
    return argument, None


def _apply_dynamic_limits(
    command: DeviceCommand,
    argument: DeviceValue,
    requested: Decimal,
    rule: NumericCommandRule,
    dynamic_limit: DynamicCommandLimit | None,
    current_value: Decimal | None,
    dt: timedelta,
    at: datetime,
    evidence: EvidenceClassification,
) -> tuple[DeviceValue | None, _CommandDecision | None]:
    lower = rule.minimum
    upper = rule.maximum
    if dynamic_limit is not None:
        if dynamic_limit.minimum < rule.minimum or dynamic_limit.maximum > rule.maximum:
            raise CommandProcessorConfigurationError(
                "dynamic command limit must not broaden the static range"
            )
        lower = dynamic_limit.minimum
        upper = dynamic_limit.maximum
    if rule.ramp_per_second is not None:
        if current_value is None:
            return None, _rejected_decision(
                command,
                CommandDisposition.REJECTED_PREREQUISITE,
                "authoritative current value is required for command ramping",
                at,
                evidence,
            )
        _validate_decimal(current_value, "current command value")
        ramp_delta = rule.ramp_per_second * Decimal(str(dt.total_seconds()))
        lower = max(lower, current_value - ramp_delta)
        upper = min(upper, current_value + ramp_delta)
        if lower > upper:
            raise CommandProcessorConfigurationError(
                "ramp envelope does not intersect the permitted command range"
            )
    if lower <= requested <= upper:
        return argument, None
    if not rule.saturate_dynamic_limit:
        return None, _rejected_decision(
            command,
            CommandDisposition.REJECTED_OUT_OF_RANGE,
            "command argument is outside the dynamic range",
            at,
            evidence,
        )
    effective = min(max(requested, lower), upper)
    return replace(argument, value=_coerce_effective_value(effective, argument.value)), None


def _settle_pending(
    state: CommandProcessorState,
    at: datetime,
) -> tuple[
    CommandProcessorState,
    tuple[CommandEffect, ...],
    tuple[DeviceCommandResult, ...],
    tuple[DeviceEvent, ...],
]:
    remaining: list[PendingCommand] = []
    effects: list[CommandEffect] = []
    results: list[DeviceCommandResult] = []
    events: list[DeviceEvent] = []
    for pending in state.pending:
        disposition: CommandDisposition | None = None
        reason = ""
        occurred_at = at
        if pending.outcome is AcknowledgementOutcome.TIMEOUT:
            if at >= pending.timeout_at:
                disposition = CommandDisposition.TIMED_OUT
                reason = "command reached its configured timeout"
                occurred_at = pending.timeout_at
        elif pending.response_at is not None and at >= pending.response_at:
            occurred_at = pending.response_at
            if pending.outcome is AcknowledgementOutcome.ACKNOWLEDGE:
                disposition = CommandDisposition.ACKNOWLEDGED
                reason = "command reached its configured acknowledgement"
                effects.append(
                    CommandEffect(
                        pending.command.command_id,
                        pending.command.capability_id,
                        pending.effective_argument,
                    )
                )
            else:
                disposition = CommandDisposition.REJECTED_DEVICE
                reason = "device returned its configured rejection"
        elif at >= pending.timeout_at:
            disposition = CommandDisposition.TIMED_OUT
            reason = "command reached its configured timeout"
            occurred_at = pending.timeout_at

        if disposition is None:
            remaining.append(pending)
            continue
        result = _result(
            pending.command,
            disposition,
            reason,
            occurred_at,
            pending.evidence_classification,
        )
        results.append(result)
        events.append(_event(pending.command, result))
    return replace(state, pending=tuple(remaining)), tuple(effects), tuple(results), tuple(events)


def _supersede_pending(
    state: CommandProcessorState,
    capability_id: str,
    at: datetime,
) -> tuple[CommandProcessorState, tuple[DeviceCommandResult, ...], tuple[DeviceEvent, ...]]:
    remaining: list[PendingCommand] = []
    results: list[DeviceCommandResult] = []
    events: list[DeviceEvent] = []
    for pending in state.pending:
        if pending.command.capability_id != capability_id:
            remaining.append(pending)
            continue
        result = _result(
            pending.command,
            CommandDisposition.SUPERSEDED,
            "pending command was superseded by a newer valid command",
            at,
            pending.evidence_classification,
        )
        results.append(result)
        events.append(_event(pending.command, result))
    return replace(state, pending=tuple(remaining)), tuple(results), tuple(events)


def _write_spacing_elapsed(
    state: CommandProcessorState,
    policy: CommandPolicy,
    at: datetime,
) -> bool:
    previous = next(
        (
            write
            for write in state.last_writes
            if write.capability_id == policy.capability.capability_id
        ),
        None,
    )
    return previous is None or at - previous.occurred_at >= policy.minimum_spacing


def _record_write(
    writes: tuple[CommandWrite, ...], new_write: CommandWrite
) -> tuple[CommandWrite, ...]:
    return (
        *(write for write in writes if write.capability_id != new_write.capability_id),
        new_write,
    )


def _index_policies(policies: tuple[CommandPolicy, ...]) -> dict[str, CommandPolicy]:
    result: dict[str, CommandPolicy] = {}
    for policy in policies:
        capability_id = policy.capability.capability_id
        if capability_id in result:
            raise CommandProcessorConfigurationError(f"duplicate command policy: {capability_id}")
        result[capability_id] = policy
    return result


def _index_dynamic_limits(
    limits: tuple[DynamicCommandLimit, ...],
) -> dict[str, DynamicCommandLimit]:
    result: dict[str, DynamicCommandLimit] = {}
    for limit in limits:
        if limit.capability_id in result:
            raise CommandProcessorConfigurationError(
                f"duplicate dynamic command limit: {limit.capability_id}"
            )
        result[limit.capability_id] = limit
    return result


def _index_current_values(values: tuple[tuple[str, Decimal], ...]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for capability_id, value in values:
        if capability_id in result:
            raise CommandProcessorConfigurationError(
                f"duplicate current command value: {capability_id}"
            )
        _validate_decimal(value, "current command value")
        result[capability_id] = value
    return result


def _rejected_decision(
    command: DeviceCommand,
    disposition: CommandDisposition,
    reason: str,
    at: datetime,
    evidence: EvidenceClassification,
) -> _CommandDecision:
    return _CommandDecision(
        rejection=_result(command, disposition, reason, at, evidence),
        effective_argument=None,
        acknowledgement=None,
        evidence_classification=evidence,
    )


def _result(
    command: DeviceCommand,
    disposition: CommandDisposition,
    reason: str,
    at: datetime,
    evidence: EvidenceClassification,
) -> DeviceCommandResult:
    return DeviceCommandResult(
        command.command_id,
        command.capability_id,
        disposition,
        reason,
        at,
        evidence,
    )


def _event(command: DeviceCommand, result: DeviceCommandResult) -> DeviceEvent:
    return DeviceEvent(
        event_id=(
            f"command:{command.command_id}:{result.disposition.value}:"
            f"{result.occurred_at.isoformat()}"
        ),
        category="device_command",
        message=f"command {command.command_id} {result.disposition.value}",
        occurred_at=result.occurred_at,
        source="command-processor",
        evidence_classification=result.evidence_classification,
    )


def _decimal_value(value: bool | int | float | str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("numeric command value must be int or float")
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("numeric command value must be finite")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("numeric command value must be finite")
    return result


def _coerce_effective_value(value: Decimal, original: bool | int | float | str) -> int | float:
    if isinstance(original, int) and value == value.to_integral_value():
        return int(value)
    return float(value)


def _validate_decimal(value: Decimal, field_name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise CommandProcessorConfigurationError(f"{field_name} must be a finite Decimal")


def _validate_duration(value: timedelta, field_name: str, *, allow_zero: bool) -> None:
    seconds = value.total_seconds()
    valid = isfinite(seconds) and (seconds >= 0 if allow_zero else seconds > 0)
    if not valid:
        qualifier = "non-negative" if allow_zero else "positive"
        raise CommandProcessorConfigurationError(
            f"{field_name} must be a finite {qualifier} duration"
        )
