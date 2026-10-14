"""Framework-neutral value contracts shared across the walking skeleton."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import isfinite
from typing import TypedDict


class DataQuality(StrEnum):
    """Validity assigned by the producing/accepting data boundary."""

    GOOD = "good"
    STALE = "stale"
    INVALID = "invalid"
    COMM_ERROR = "comm_error"
    OUT_OF_RANGE = "out_of_range"


class Freshness(StrEnum):
    """Presentation state derived from availability, quality, and elapsed time."""

    LIVE = "live"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class ComponentStatus(StrEnum):
    """Small component-health vocabulary for the Phase 2 path."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    STOPPED = "stopped"
    DISABLED = "disabled"


class AlarmSeverity(StrEnum):
    """Minimal severity vocabulary for future alarm contracts."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class MeasurementRecord(TypedDict):
    """Primitive, deterministic measurement representation."""

    point_id: str
    label: str
    source: str
    value: float
    unit: str
    source_timestamp: str
    receive_timestamp: str
    quality: str
    synthetic: bool


def normalize_utc(value: datetime, field_name: str) -> datetime:
    """Reject naive instants and normalize aware timestamps to UTC."""

    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _nonempty(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class Measurement:
    """One unit-bearing value with identity, timing, quality, and origin."""

    point_id: str
    label: str
    source: str
    value: float
    unit: str
    source_timestamp: datetime
    receive_timestamp: datetime
    quality: DataQuality
    synthetic: bool

    def __post_init__(self) -> None:
        """Enforce safe value/timestamp semantics at construction."""

        object.__setattr__(self, "point_id", _nonempty(self.point_id, "point_id"))
        object.__setattr__(self, "label", _nonempty(self.label, "label"))
        object.__setattr__(self, "source", _nonempty(self.source, "source"))
        object.__setattr__(self, "unit", _nonempty(self.unit, "unit"))
        if isinstance(self.value, bool) or not isinstance(self.value, (int, float)):
            raise TypeError("value must be numeric")
        numeric_value = float(self.value)
        if not isfinite(numeric_value):
            raise ValueError("value must be finite")
        object.__setattr__(self, "value", numeric_value)
        source_timestamp = normalize_utc(self.source_timestamp, "source_timestamp")
        receive_timestamp = normalize_utc(self.receive_timestamp, "receive_timestamp")
        if receive_timestamp < source_timestamp:
            raise ValueError("receive_timestamp must not precede source_timestamp")
        object.__setattr__(self, "source_timestamp", source_timestamp)
        object.__setattr__(self, "receive_timestamp", receive_timestamp)

    def to_record(self) -> MeasurementRecord:
        """Return a transport-neutral primitive record for deterministic evidence."""

        return MeasurementRecord(
            point_id=self.point_id,
            label=self.label,
            source=self.source,
            value=self.value,
            unit=self.unit,
            source_timestamp=self.source_timestamp.isoformat(),
            receive_timestamp=self.receive_timestamp.isoformat(),
            quality=self.quality.value,
            synthetic=self.synthetic,
        )


@dataclass(frozen=True, slots=True)
class Heartbeat:
    """Observed lifecycle/health of a component independently of its values."""

    component: str
    observed_at: datetime
    status: ComponentStatus
    synthetic: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "component", _nonempty(self.component, "component"))
        object.__setattr__(
            self,
            "observed_at",
            normalize_utc(self.observed_at, "observed_at"),
        )


@dataclass(frozen=True, slots=True)
class Command:
    """Minimal command intent contract; no transport or device semantics."""

    command_id: str
    target: str
    action: str
    issued_at: datetime
    synthetic: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "command_id", _nonempty(self.command_id, "command_id"))
        object.__setattr__(self, "target", _nonempty(self.target, "target"))
        object.__setattr__(self, "action", _nonempty(self.action, "action"))
        object.__setattr__(self, "issued_at", normalize_utc(self.issued_at, "issued_at"))


@dataclass(frozen=True, slots=True)
class Alarm:
    """Minimal alarm fact suitable for later catalog-backed expansion."""

    alarm_id: str
    message: str
    occurred_at: datetime
    severity: AlarmSeverity
    active: bool
    synthetic: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "alarm_id", _nonempty(self.alarm_id, "alarm_id"))
        object.__setattr__(self, "message", _nonempty(self.message, "message"))
        object.__setattr__(
            self,
            "occurred_at",
            normalize_utc(self.occurred_at, "occurred_at"),
        )


@dataclass(frozen=True, slots=True)
class Event:
    """Minimal timestamped event fact without a speculative event framework."""

    event_id: str
    category: str
    message: str
    occurred_at: datetime
    synthetic: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _nonempty(self.event_id, "event_id"))
        object.__setattr__(self, "category", _nonempty(self.category, "category"))
        object.__setattr__(self, "message", _nonempty(self.message, "message"))
        object.__setattr__(
            self,
            "occurred_at",
            normalize_utc(self.occurred_at, "occurred_at"),
        )


@dataclass(frozen=True, slots=True)
class SystemSnapshot:
    """Current Phase 2 state without device-specific fields."""

    observed_at: datetime
    freshness: Freshness
    measurement: Measurement | None
    heartbeat: Heartbeat | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observed_at",
            normalize_utc(self.observed_at, "observed_at"),
        )
        if self.measurement is None and self.freshness is not Freshness.UNAVAILABLE:
            raise ValueError("a snapshot without a measurement must be unavailable")
