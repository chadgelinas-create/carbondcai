"""Deterministic structural and semantic protocol validation."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from skid2.protocol.address import AddressError, CanonicalAddress, normalize_address
from skid2.protocol.codecs import CodecError, codec_spec, raw_bounds
from skid2.protocol.loader import ProtocolInputError, parse_document
from skid2.protocol.models import (
    DOCUMENT_KINDS,
    LIFECYCLE_STATES,
    SCHEMA_VERSION,
    PointSpec,
    ProtocolDocument,
)

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
LOGICAL_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:[._][a-z0-9]+)*$")
GENERATED_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,39}$")
ACCESS_VALUES = frozenset({"read", "write", "read_write"})
SERVICE_ACCESS = {
    "modbus_input_register_04": frozenset({"read"}),
    "modbus_holding_register_03_06": frozenset({"read", "write", "read_write"}),
    "modbus_holding_register_03_16": frozenset({"read", "write", "read_write"}),
    "legacy_unknown": frozenset({"read", "write", "read_write"}),
}
PROFILE_VALUES = frozenset({"candidate", "reference", "production"})


@dataclass(frozen=True, slots=True, order=True)
class Diagnostic:
    """Stable validation output suitable for people and tooling."""

    source: str
    path: str
    code: str
    severity: str
    message: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Validation result for one or more documents."""

    profile: str
    diagnostics: tuple[Diagnostic, ...]
    documents: tuple[ProtocolDocument, ...]

    @property
    def accepted(self) -> bool:
        """Return whether no profile-invalid errors were found."""

        return not any(item.severity == "error" for item in self.diagnostics)

    def as_json(self) -> str:
        """Render stable machine-readable diagnostics."""

        return json.dumps(
            {
                "accepted": self.accepted,
                "diagnostics": [asdict(item) for item in self.diagnostics],
                "document_count": len(self.documents),
                "profile": self.profile,
            },
            indent=2,
            sort_keys=True,
        )


def _diagnostic(
    document: ProtocolDocument,
    path: str,
    code: str,
    message: str,
    severity: str = "error",
) -> Diagnostic:
    return Diagnostic(str(document.path), path, code, severity, message)


def _validate_point(
    document: ProtocolDocument,
    point: PointSpec,
    index: int,
    profile: str,
) -> tuple[list[Diagnostic], CanonicalAddress | None]:
    base = f"$.points[{index}]"
    diagnostics: list[Diagnostic] = []

    if not LOGICAL_ID_PATTERN.fullmatch(point.logical_id):
        diagnostics.append(
            _diagnostic(document, f"{base}.logical_id", "P3_LOGICAL_ID", "invalid logical ID")
        )
    if not GENERATED_NAME_PATTERN.fullmatch(point.generated_name):
        diagnostics.append(
            _diagnostic(
                document,
                f"{base}.generated_name",
                "P3_GENERATED_NAME",
                "name must be an uppercase Logix-compatible identifier of at most 40 characters",
            )
        )
    if point.lifecycle not in LIFECYCLE_STATES:
        diagnostics.append(
            _diagnostic(document, f"{base}.lifecycle", "P3_LIFECYCLE", "unknown lifecycle state")
        )
    if point.access not in ACCESS_VALUES:
        diagnostics.append(
            _diagnostic(document, f"{base}.access", "P3_ACCESS", "unsupported access value")
        )
    allowed_access = SERVICE_ACCESS.get(point.service)
    if allowed_access is None:
        diagnostics.append(
            _diagnostic(document, f"{base}.service", "P3_SERVICE", "unsupported service")
        )
    elif point.access not in allowed_access:
        diagnostics.append(
            _diagnostic(
                document,
                f"{base}.access",
                "P3_SERVICE_ACCESS",
                f"{point.access} is illegal for {point.service}",
            )
        )

    canonical: CanonicalAddress | None = None
    try:
        canonical = normalize_address(
            display=point.address.display,
            notation=point.address.notation,
            convention=point.address.convention,
            space=point.address.space,
            word_count=point.wire.word_count,
            bit=point.address.bit,
        )
    except AddressError as exc:
        diagnostics.append(_diagnostic(document, f"{base}.address", "P3_ADDRESS", str(exc)))
    else:
        if canonical.offset != point.address.wire_offset:
            diagnostics.append(
                _diagnostic(
                    document,
                    f"{base}.address.wire_offset",
                    "P3_ADDRESS_MISMATCH",
                    f"declared {point.address.wire_offset}, normalized {canonical.offset}",
                )
            )

    try:
        minimum_raw, maximum_raw = raw_bounds(codec_spec(point))
    except CodecError as exc:
        diagnostics.append(_diagnostic(document, f"{base}.wire", "P3_CODEC_SPEC", str(exc)))
    else:
        diagnostics.extend(
            _diagnostic(
                document,
                f"{base}.engineering.sentinels",
                "P3_SENTINEL_RANGE",
                f"sentinel {sentinel} is outside {minimum_raw}..{maximum_raw}",
            )
            for sentinel in point.engineering.sentinels
            if sentinel < minimum_raw or sentinel > maximum_raw
        )
    if point.engineering.scale == 0:
        diagnostics.append(
            _diagnostic(document, f"{base}.engineering.scale", "P3_SCALE", "scale cannot be zero")
        )
    if (
        point.engineering.minimum is not None
        and point.engineering.maximum is not None
        and point.engineering.minimum > point.engineering.maximum
    ):
        diagnostics.append(
            _diagnostic(
                document,
                f"{base}.engineering",
                "P3_RANGE",
                "minimum cannot exceed maximum",
            )
        )
    if point.wire.primitive == "bitfield":
        seen_bits: set[int] = set()
        for bit, _label in point.wire.bit_definitions:
            if not 0 <= bit <= 15 or bit in seen_bits:
                diagnostics.append(
                    _diagnostic(
                        document,
                        f"{base}.wire.bit_definitions",
                        "P3_BIT_DEFINITION",
                        "bit definitions must be unique values in 0..15",
                    )
                )
            seen_bits.add(bit)

    writable = point.access in {"write", "read_write"}
    if writable and point.command is None:
        diagnostics.append(
            _diagnostic(
                document,
                f"{base}.command",
                "P3_COMMAND_MISSING",
                "writable points require explicit command safety metadata",
            )
        )
    if not writable and point.command is not None:
        diagnostics.append(
            _diagnostic(
                document,
                f"{base}.command",
                "P3_COMMAND_READ_ONLY",
                "read-only points cannot define a command",
            )
        )

    source_by_id = {source.source_id: source for source in document.sources}
    source = source_by_id.get(point.provenance.source_id)
    if source is None or source.sha256 != point.provenance.source_sha256:
        diagnostics.append(
            _diagnostic(
                document,
                f"{base}.provenance",
                "P3_PROVENANCE",
                "point source ID/hash does not match the document source register",
            )
        )

    if profile == "production":
        if point.lifecycle != "approved":
            diagnostics.append(
                _diagnostic(
                    document,
                    f"{base}.lifecycle",
                    "P3_PRODUCTION_POINT_STATUS",
                    "production points must be explicitly approved",
                )
            )
        if point.address.status != "approved":
            diagnostics.append(
                _diagnostic(
                    document,
                    f"{base}.address.status",
                    "P3_PRODUCTION_ADDRESS",
                    "production address normalization must be approved",
                )
            )
        if point.wire.status != "approved":
            diagnostics.append(
                _diagnostic(
                    document,
                    f"{base}.wire.status",
                    "P3_PRODUCTION_WIRE",
                    "production type and byte/word order must be approved",
                )
            )
        if point.engineering.status != "approved":
            diagnostics.append(
                _diagnostic(
                    document,
                    f"{base}.engineering.status",
                    "P3_PRODUCTION_ENGINEERING",
                    "production scale, unit, and range must be approved",
                )
            )
        if point.timing.status != "approved":
            diagnostics.append(
                _diagnostic(
                    document,
                    f"{base}.timing.status",
                    "P3_PRODUCTION_TIMING",
                    "production timing must be approved",
                )
            )
        if writable and (
            point.command is None
            or point.command.status != "approved"
            or point.command.timeout_ms is None
            or point.command.safe_value is None
            or not point.command.prerequisites
            or point.command.acknowledgement == "unknown"
        ):
            diagnostics.append(
                _diagnostic(
                    document,
                    f"{base}.command",
                    "P3_PRODUCTION_COMMAND",
                    "production command safety metadata is incomplete or unapproved",
                )
            )
    return diagnostics, canonical


def validate_document(document: ProtocolDocument, profile: str = "candidate") -> ValidationReport:
    """Validate one parsed protocol document."""

    if profile not in PROFILE_VALUES:
        raise ValueError(f"unsupported validation profile {profile!r}")
    diagnostics: list[Diagnostic] = []
    if document.schema_version != SCHEMA_VERSION:
        diagnostics.append(
            _diagnostic(
                document,
                "$.schema_version",
                "P3_SCHEMA_VERSION",
                f"expected schema version {SCHEMA_VERSION}",
            )
        )
    if document.document_kind not in DOCUMENT_KINDS:
        diagnostics.append(
            _diagnostic(document, "$.document_kind", "P3_DOCUMENT_KIND", "unknown document kind")
        )
    if document.lifecycle not in LIFECYCLE_STATES:
        diagnostics.append(
            _diagnostic(document, "$.lifecycle", "P3_LIFECYCLE", "unknown lifecycle state")
        )
    if document.device.lifecycle not in LIFECYCLE_STATES:
        diagnostics.append(
            _diagnostic(
                document,
                "$.device.lifecycle",
                "P3_LIFECYCLE",
                "unknown device lifecycle state",
            )
        )
    if not document.sources:
        diagnostics.append(
            _diagnostic(document, "$.sources", "P3_SOURCE_MISSING", "a source is required")
        )
    for index, source in enumerate(document.sources):
        if not SHA256_PATTERN.fullmatch(source.sha256):
            diagnostics.append(
                _diagnostic(
                    document,
                    f"$.sources[{index}].sha256",
                    "P3_SOURCE_HASH",
                    "source hash must be a lowercase SHA-256 value",
                )
            )
    if document.document_kind == "blocker_manifest":
        if document.points:
            diagnostics.append(
                _diagnostic(
                    document,
                    "$.points",
                    "P3_BLOCKED_POINTS",
                    "blocked interfaces cannot define points",
                )
            )
        if not document.blockers:
            diagnostics.append(
                _diagnostic(
                    document,
                    "$.blockers",
                    "P3_BLOCKER_MISSING",
                    "blocked interfaces require an actionable blocker",
                )
            )
    elif document.blockers and document.lifecycle not in {
        "candidate",
        "blocked",
        "reference_only",
    }:
        diagnostics.append(
            _diagnostic(
                document,
                "$.blockers",
                "P3_BLOCKER_STATUS",
                "documents with blockers must remain candidate or blocked",
            )
        )

    logical_ids: set[str] = set()
    generated_names: set[str] = set()
    occupied: dict[tuple[str, int], list[tuple[int, PointSpec, CanonicalAddress]]] = defaultdict(
        list
    )
    for index, point in enumerate(document.points):
        if point.logical_id in logical_ids:
            diagnostics.append(
                _diagnostic(
                    document,
                    f"$.points[{index}].logical_id",
                    "P3_DUPLICATE_LOGICAL_ID",
                    f"duplicate logical ID {point.logical_id}",
                )
            )
        logical_ids.add(point.logical_id)
        if point.generated_name in generated_names:
            diagnostics.append(
                _diagnostic(
                    document,
                    f"$.points[{index}].generated_name",
                    "P3_DUPLICATE_GENERATED_NAME",
                    f"duplicate generated name {point.generated_name}",
                )
            )
        generated_names.add(point.generated_name)
        point_diagnostics, canonical = _validate_point(document, point, index, profile)
        diagnostics.extend(point_diagnostics)
        if canonical is not None:
            for offset in canonical.occupied_offsets:
                occupied[(canonical.space, offset)].append((index, point, canonical))

    for entries in occupied.values():
        if len(entries) < 2:
            continue
        bits = [entry[2].bit for entry in entries]
        bit_alias_is_valid = all(bit is not None for bit in bits) and len(set(bits)) == len(bits)
        reference_alias_is_valid = all(
            entry[1].lifecycle in {"reference_only", "blocked"} and bool(entry[1].wire.atomic_group)
            for entry in entries
        )
        if bit_alias_is_valid or reference_alias_is_valid:
            continue
        for index, point, _canonical in entries:
            diagnostics.append(
                _diagnostic(
                    document,
                    f"$.points[{index}].address",
                    "P3_ADDRESS_OVERLAP",
                    f"{point.logical_id} overlaps another point",
                )
            )

    if profile == "reference" and document.lifecycle != "reference_only":
        diagnostics.append(
            _diagnostic(
                document,
                "$.lifecycle",
                "P3_REFERENCE_STATUS",
                "reference profile accepts only reference_only documents",
            )
        )
    if profile == "production":
        if document.document_kind != "device_map":
            diagnostics.append(
                _diagnostic(
                    document,
                    "$.document_kind",
                    "P3_PRODUCTION_DOCUMENT_KIND",
                    "reference and blocker documents cannot enter production",
                )
            )
        if document.lifecycle != "approved" or document.device.lifecycle != "approved":
            diagnostics.append(
                _diagnostic(
                    document,
                    "$.lifecycle",
                    "P3_PRODUCTION_DOCUMENT_STATUS",
                    "document and device must be approved for production",
                )
            )
        for name, applicability in (
            ("model", document.device.model),
            ("firmware", document.device.firmware),
            ("map_revision", document.device.map_revision),
        ):
            if applicability.status != "exact" or not applicability.values:
                diagnostics.append(
                    _diagnostic(
                        document,
                        f"$.device.{name}",
                        "P3_PRODUCTION_APPLICABILITY",
                        f"{name} applicability must be exact and evidenced",
                    )
                )
        if document.device.endpoint_status != "approved":
            diagnostics.append(
                _diagnostic(
                    document,
                    "$.device.endpoint_status",
                    "P3_PRODUCTION_ENDPOINT",
                    "production endpoint/role must be approved",
                )
            )
        for index, _blocker in enumerate(document.blockers):
            diagnostics.append(
                _diagnostic(
                    document,
                    f"$.blockers[{index}]",
                    "P3_PRODUCTION_BLOCKER",
                    "unresolved blocker prevents production generation",
                )
            )

    return ValidationReport(
        profile,
        tuple(sorted(set(diagnostics))),
        (document,),
    )


def validate_paths(paths: list[Path], profile: str = "candidate") -> ValidationReport:
    """Load and validate paths with stable cross-document diagnostics."""

    diagnostics: list[Diagnostic] = []
    documents: list[ProtocolDocument] = []
    for path in sorted(paths):
        try:
            document = parse_document(path)
        except ProtocolInputError as exc:
            diagnostics.append(
                Diagnostic(
                    str(exc.source),
                    exc.path,
                    "P3_INPUT",
                    "error",
                    exc.message,
                )
            )
            continue
        report = validate_document(document, profile)
        diagnostics.extend(report.diagnostics)
        documents.append(document)

    logical: dict[str, Path] = {}
    generated: dict[str, Path] = {}
    for document in documents:
        for point in document.points:
            for value, seen, code, label in (
                (
                    point.logical_id,
                    logical,
                    "P3_DUPLICATE_LOGICAL_ID",
                    "logical ID",
                ),
                (
                    point.generated_name,
                    generated,
                    "P3_DUPLICATE_GENERATED_NAME",
                    "generated name",
                ),
            ):
                previous = seen.get(value)
                if previous is not None:
                    diagnostics.append(
                        Diagnostic(
                            str(document.path),
                            "$.points",
                            code,
                            "error",
                            f"{label} {value!r} also exists in {previous}",
                        )
                    )
                else:
                    seen[value] = document.path

    return ValidationReport(
        profile,
        tuple(sorted(set(diagnostics))),
        tuple(documents),
    )
