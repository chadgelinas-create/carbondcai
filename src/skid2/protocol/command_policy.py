"""Strict conversion from generated point metadata to simulator command policy."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from skid2.sim.commands import (
    AcknowledgementPolicy,
    CommandPolicy,
    CommandProcessorConfigurationError,
    NumericCommandRule,
)
from skid2.sim.device import DeviceCapability, DeviceLifecycle


class CommandPolicyConfigurationError(ValueError):
    """Generated command metadata cannot form an unambiguous policy."""


def command_policy_from_generated(
    descriptor: Mapping[str, object],
    *,
    capability: DeviceCapability,
    allowed_lifecycles: frozenset[DeviceLifecycle],
    allowed_modes: frozenset[str] = frozenset(),
    acknowledgement: AcknowledgementPolicy | None = None,
    saturate_dynamic_limit: bool = False,
    ramp_per_second: Decimal | None = None,
) -> CommandPolicy:
    """Map exact generated fields without inferring acknowledgement or permission."""

    logical_id = _required_string(descriptor, "logical_id")
    device_id = _required_string(descriptor, "device_id")
    if logical_id != capability.capability_id:
        raise CommandPolicyConfigurationError("logical_id does not match capability_id")
    if device_id != capability.device_id:
        raise CommandPolicyConfigurationError(
            "generated device_id does not match capability device_id"
        )

    access = _required_string(descriptor, "access")
    if access not in {"read", "write", "read_write"}:
        raise CommandPolicyConfigurationError("unsupported access metadata")
    writable = access in {"write", "read_write"}

    metadata_status = _optional_string(descriptor, "command_status")
    if metadata_status not in {None, "blocked", "candidate", "approved"}:
        raise CommandPolicyConfigurationError("unsupported command status metadata")
    prerequisites = frozenset(_required_string_tuple(descriptor, "command_prerequisites"))
    _optional_string(descriptor, "command_acknowledgement")
    _optional_integer(descriptor, "command_timeout_ms")
    _optional_decimal(descriptor, "command_safe_value")
    spacing_ms = _optional_integer(descriptor, "command_minimum_spacing_ms")
    _optional_string(descriptor, "command_ramp")

    minimum = _optional_decimal(descriptor, "minimum")
    maximum = _optional_decimal(descriptor, "maximum")
    numeric_rule = None
    if minimum is not None or maximum is not None:
        if minimum is None or maximum is None:
            raise CommandPolicyConfigurationError(
                "numeric command requires both minimum and maximum"
            )
        try:
            numeric_rule = NumericCommandRule(
                unit=_required_string(descriptor, "unit"),
                minimum=minimum,
                maximum=maximum,
                saturate_dynamic_limit=saturate_dynamic_limit,
                ramp_per_second=ramp_per_second,
            )
        except CommandProcessorConfigurationError as exc:
            raise CommandPolicyConfigurationError(str(exc)) from exc
    elif saturate_dynamic_limit or ramp_per_second is not None:
        raise CommandPolicyConfigurationError(
            "saturation and ramp require numeric command boundaries"
        )

    try:
        return CommandPolicy(
            capability=capability,
            metadata_status=metadata_status,
            writable=writable,
            allowed_lifecycles=allowed_lifecycles,
            allowed_modes=allowed_modes,
            prerequisites=prerequisites,
            minimum_spacing=timedelta(milliseconds=spacing_ms or 0),
            acknowledgement=acknowledgement,
            numeric_rule=numeric_rule,
        )
    except CommandProcessorConfigurationError as exc:
        raise CommandPolicyConfigurationError(str(exc)) from exc


def _required_value(descriptor: Mapping[str, object], field_name: str) -> object:
    if field_name not in descriptor:
        raise CommandPolicyConfigurationError(f"missing generated field: {field_name}")
    return descriptor[field_name]


def _required_string(descriptor: Mapping[str, object], field_name: str) -> str:
    value = _required_value(descriptor, field_name)
    if not isinstance(value, str) or not value.strip():
        raise CommandPolicyConfigurationError(f"{field_name} must be a non-empty string")
    return value


def _optional_string(descriptor: Mapping[str, object], field_name: str) -> str | None:
    value = _required_value(descriptor, field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CommandPolicyConfigurationError(f"{field_name} must be a string or null")
    return value


def _optional_integer(descriptor: Mapping[str, object], field_name: str) -> int | None:
    value = _required_value(descriptor, field_name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise CommandPolicyConfigurationError(
            f"{field_name} must be a non-negative integer or null"
        )
    return value


def _optional_decimal(descriptor: Mapping[str, object], field_name: str) -> Decimal | None:
    value = _required_value(descriptor, field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise CommandPolicyConfigurationError(f"{field_name} must be a decimal string or null")
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise CommandPolicyConfigurationError(
            f"{field_name} must be a decimal string or null"
        ) from exc
    if not result.is_finite():
        raise CommandPolicyConfigurationError(f"{field_name} must be finite")
    return result


def _required_string_tuple(descriptor: Mapping[str, object], field_name: str) -> tuple[str, ...]:
    value = _required_value(descriptor, field_name)
    if not isinstance(value, tuple) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise CommandPolicyConfigurationError(f"{field_name} must be a tuple of non-empty strings")
    return value
