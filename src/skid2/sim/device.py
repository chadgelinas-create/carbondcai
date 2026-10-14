"""Transport-independent, deterministic device-model contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from math import isfinite
from typing import TYPE_CHECKING, Protocol

from skid2.contracts import AlarmSeverity, ComponentStatus, DataQuality
from skid2.contracts.domain import normalize_utc

if TYPE_CHECKING:
    from skid2.plant.model import PlantValue


class DeviceLifecycle(StrEnum):
    INITIALIZING = "initializing"
    DISABLED = "disabled"
    OFFLINE = "offline"
    READY = "ready"
    IDLE = "idle"
    ACTIVE = "active"
    FAULTED = "faulted"
    SHUTDOWN = "shutdown"


class EvidenceClassification(StrEnum):
    SOURCED = "sourced"
    CALIBRATED = "calibrated"
    PROVISIONAL = "provisional"
    BLOCKED = "blocked"
    UNSUPPORTED = "unsupported"


class FidelityLevel(StrEnum):
    CONTRACT = "contract"
    LEVEL_1 = "level_1"
    LEVEL_2 = "level_2"
    HARDWARE_CALIBRATED = "hardware_calibrated"


class CapabilityKind(StrEnum):
    MODE = "mode"
    COMMAND = "command"
    FAULT = "fault"
    TRANSPORT = "transport"
    BEHAVIOR = "behavior"


class ExecutionProfile(StrEnum):
    STAGE_0 = "stage0"
    HIL = "hil"
    PRODUCTION = "production"


class CommandDisposition(StrEnum):
    ACCEPTED = "accepted"
    ACKNOWLEDGED = "acknowledged"
    REJECTED_UNSUPPORTED = "rejected_unsupported"
    REJECTED_READ_ONLY = "rejected_read_only"
    REJECTED_UNAPPROVED = "rejected_unapproved"
    REJECTED_PREREQUISITE = "rejected_prerequisite"
    REJECTED_OUT_OF_RANGE = "rejected_out_of_range"
    REJECTED_CADENCE = "rejected_cadence"
    REJECTED_DEVICE = "rejected_device"
    TIMED_OUT = "timed_out"
    SUPERSEDED = "superseded"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    device_id: str
    display_name: str
    model: str | None = None
    firmware: str | None = None


@dataclass(frozen=True, slots=True)
class DeviceCapability:
    capability_id: str
    device_id: str
    kind: CapabilityKind
    evidence_classification: EvidenceClassification
    fidelity_level: FidelityLevel
    review_status: str
    available: bool = False
    enabled_profiles: frozenset[ExecutionProfile] = frozenset()
    source_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    calibration_evidence: str | None = None
    calibration_tolerance: str | None = None

    def __post_init__(self) -> None:
        unavailable = {
            EvidenceClassification.BLOCKED,
            EvidenceClassification.UNSUPPORTED,
        }
        if self.evidence_classification in unavailable and self.available:
            raise ValueError("blocked or unsupported capability cannot be available")
        if self.enabled_profiles and not self.available:
            raise ValueError("unavailable capability cannot enable execution profiles")
        if (
            self.evidence_classification is EvidenceClassification.PROVISIONAL
            and self.enabled_profiles - {ExecutionProfile.STAGE_0}
        ):
            raise ValueError("provisional capability is limited to Stage 0")
        if (
            self.fidelity_level is FidelityLevel.HARDWARE_CALIBRATED
            and self.evidence_classification is not EvidenceClassification.CALIBRATED
        ):
            raise ValueError("hardware-calibrated fidelity requires calibrated evidence")
        if self.evidence_classification is EvidenceClassification.CALIBRATED and (
            not self.calibration_evidence or not self.calibration_tolerance
        ):
            raise ValueError("calibrated capability requires evidence and tolerance")


@dataclass(frozen=True, slots=True)
class DeviceValue:
    logical_id: str
    value: bool | int | float | str
    unit: str
    quality: DataQuality
    source: str
    evidence_classification: EvidenceClassification
    test_id: str | None = None
    test_started_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.test_started_at is not None:
            object.__setattr__(
                self,
                "test_started_at",
                normalize_utc(self.test_started_at, "test_started_at"),
            )

    @property
    def provisional(self) -> bool:
        return self.evidence_classification is EvidenceClassification.PROVISIONAL


@dataclass(frozen=True, slots=True)
class DeviceCommand:
    command_id: str
    capability_id: str
    issued_at: datetime
    arguments: tuple[DeviceValue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "issued_at", normalize_utc(self.issued_at, "issued_at"))


@dataclass(frozen=True, slots=True)
class DeviceCommandResult:
    command_id: str
    capability_id: str
    disposition: CommandDisposition
    reason: str
    occurred_at: datetime
    evidence_classification: EvidenceClassification

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "occurred_at",
            normalize_utc(self.occurred_at, "occurred_at"),
        )


@dataclass(frozen=True, slots=True)
class DeviceHealth:
    lifecycle: DeviceLifecycle
    status: ComponentStatus
    quality: DataQuality
    source: str
    evidence_classification: EvidenceClassification
    test_id: str | None = None
    test_started_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.test_started_at is not None:
            object.__setattr__(
                self,
                "test_started_at",
                normalize_utc(self.test_started_at, "test_started_at"),
            )


@dataclass(frozen=True, slots=True)
class DeviceFault:
    fault_id: str
    active: bool
    severity: AlarmSeverity
    source: str
    evidence_classification: EvidenceClassification
    test_id: str | None = None
    test_started_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.test_started_at is not None:
            object.__setattr__(
                self,
                "test_started_at",
                normalize_utc(self.test_started_at, "test_started_at"),
            )


@dataclass(frozen=True, slots=True)
class DeviceEvent:
    event_id: str
    category: str
    message: str
    occurred_at: datetime
    source: str
    evidence_classification: EvidenceClassification
    test_id: str | None = None
    test_started_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "occurred_at",
            normalize_utc(self.occurred_at, "occurred_at"),
        )
        if self.test_started_at is not None:
            object.__setattr__(
                self,
                "test_started_at",
                normalize_utc(self.test_started_at, "test_started_at"),
            )


class DeviceState(Protocol):
    @property
    def lifecycle(self) -> DeviceLifecycle: ...


@dataclass(frozen=True, slots=True)
class DeviceStep[ConfigurationT, StateT: DeviceState, InputsT]:
    state: StateT
    inputs: InputsT
    commands: tuple[DeviceCommand, ...]
    configuration: ConfigurationT
    at: datetime
    dt: timedelta
    plant: tuple[PlantValue, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "at", normalize_utc(self.at, "at"))
        seconds = self.dt.total_seconds()
        if not isfinite(seconds) or seconds <= 0:
            raise ValueError("dt must be a finite positive duration")


@dataclass(frozen=True, slots=True)
class DeviceStepResult[StateT: DeviceState]:
    state: StateT
    telemetry: tuple[DeviceValue, ...]
    health: DeviceHealth
    faults: tuple[DeviceFault, ...] = ()
    events: tuple[DeviceEvent, ...] = ()
    command_results: tuple[DeviceCommandResult, ...] = ()


class DeviceModel[ConfigurationT, StateT: DeviceState, InputsT](Protocol):
    @property
    def identity(self) -> DeviceIdentity: ...

    @property
    def capabilities(self) -> tuple[DeviceCapability, ...]: ...

    def initial_state(self, configuration: ConfigurationT) -> StateT: ...

    def step(
        self,
        request: DeviceStep[ConfigurationT, StateT, InputsT],
    ) -> DeviceStepResult[StateT]: ...
