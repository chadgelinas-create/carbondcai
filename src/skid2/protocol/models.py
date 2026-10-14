"""Framework-neutral protocol contract values.

The repository stores protocol sources as JSON-compatible YAML. JSON is a strict
subset of YAML 1.2, which gives the project deterministic parsing without adding
an unlocked parser dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

type JsonScalar = None | bool | int | float | str
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]

SCHEMA_VERSION = "1.0.0"
LIFECYCLE_STATES = frozenset(
    {
        "raw",
        "imported",
        "candidate",
        "reviewed",
        "approved",
        "reference_only",
        "blocked",
        "superseded",
        "rejected",
    }
)
DOCUMENT_KINDS = frozenset({"device_map", "reference_map", "blocker_manifest"})


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Controlled input identity without embedding the confidential source."""

    source_id: str
    filename: str
    revision: str
    sha256: str
    classification: str


@dataclass(frozen=True, slots=True)
class Applicability:
    """Model, firmware, or protocol-map applicability statement."""

    status: str
    values: tuple[str, ...]
    evidence: str


@dataclass(frozen=True, slots=True)
class ReviewRecord:
    """A review authority and its current decision."""

    authority: str
    reviewer: str
    status: str
    date: str
    evidence: str


@dataclass(frozen=True, slots=True)
class DeviceSpec:
    """Device identity and applicability boundary."""

    device_id: str
    display_name: str
    role: str
    lifecycle: str
    protocol: str
    protocol_role: str
    endpoint_status: str
    model: Applicability
    firmware: Applicability
    map_revision: Applicability


@dataclass(frozen=True, slots=True)
class AddressSpec:
    """Original and canonical register address."""

    status: str
    display: str
    notation: str
    convention: str
    space: str
    wire_offset: int
    bit: int | None


@dataclass(frozen=True, slots=True)
class WireSpec:
    """Raw wire representation."""

    status: str
    primitive: str
    signed: bool
    word_count: int
    byte_order: str
    word_order: str
    atomic_group: str
    bit_definitions: tuple[tuple[int, str], ...]


@dataclass(frozen=True, slots=True)
class EngineeringSpec:
    """Engineering conversion and validity range."""

    status: str
    scale: Decimal
    offset: Decimal
    unit: str
    minimum: Decimal | None
    maximum: Decimal | None
    sentinels: tuple[int, ...]
    rounding: str


@dataclass(frozen=True, slots=True)
class TimingSpec:
    """Poll, timeout, stale, and retry contract."""

    status: str
    poll_class: str
    timeout_ms: int | None
    stale_after_ms: int | None
    retry_policy: str
    source_timestamp: str
    receive_timestamp: str


@dataclass(frozen=True, slots=True)
class QualitySpec:
    """Quality outcomes for invalid or unavailable data."""

    sentinel: str
    communication_error: str
    out_of_range: str


@dataclass(frozen=True, slots=True)
class CommandSpec:
    """Safety metadata required before a point can be written."""

    status: str
    prerequisites: tuple[str, ...]
    acknowledgement: str
    timeout_ms: int | None
    safe_value: Decimal | None
    minimum_spacing_ms: int | None
    ramp: str


@dataclass(frozen=True, slots=True)
class PointProvenance:
    """Exact source location for one normalized point."""

    source_id: str
    source_sha256: str
    sheet: str
    row: int
    cells: str
    raw_vendor_name: str


@dataclass(frozen=True, slots=True)
class PointSpec:
    """One logical point and its full wire contract."""

    logical_id: str
    generated_name: str
    vendor_name: str
    lifecycle: str
    service: str
    access: str
    address: AddressSpec
    wire: WireSpec
    engineering: EngineeringSpec
    timing: TimingSpec
    quality: QualitySpec
    command: CommandSpec | None
    provenance: PointProvenance
    notes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class BlockerSpec:
    """Machine-readable missing-interface or approval blocker."""

    blocker_id: str
    owner: str
    decision_id: str
    source_id: str
    needed_by: str
    reason: str
    missing: tuple[str, ...]
    known: tuple[str, ...]
    affects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProtocolDocument:
    """A validated, versioned protocol source document."""

    path: Path
    schema_version: str
    document_kind: str
    document_id: str
    title: str
    lifecycle: str
    device: DeviceSpec
    sources: tuple[SourceReference, ...]
    reviews: tuple[ReviewRecord, ...]
    points: tuple[PointSpec, ...]
    blockers: tuple[BlockerSpec, ...]
