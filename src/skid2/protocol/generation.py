"""Deterministic protocol artifact generation and drift detection."""

from __future__ import annotations

import csv
import difflib
import hashlib
import io
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from skid2.protocol.models import PointSpec, ProtocolDocument
from skid2.protocol.validation import ValidationReport, validate_paths

GENERATOR_VERSION = "1.0.0"
WARNING = "DO NOT EDIT: generated review artifact; candidate data is not production approved."


class GenerationError(ValueError):
    """Invalid source, ineligible profile, or committed artifact drift."""


@dataclass(frozen=True, slots=True)
class GeneratedArtifact:
    """One relative generated file and deterministic bytes."""

    path: Path
    content: bytes


def protocol_paths(input_root: Path) -> list[Path]:
    """Return every manually maintained protocol source in stable order."""

    return sorted(path for path in input_root.rglob("*.yaml") if "examples" not in path.parts)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _input_identity(paths: list[Path], input_root: Path) -> tuple[str, list[dict[str, str]]]:
    digest = hashlib.sha256()
    manifest: list[dict[str, str]] = []
    for path in paths:
        relative = path.relative_to(input_root).as_posix()
        content = path.read_bytes()
        source_hash = _digest(content)
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(source_hash.encode())
        digest.update(b"\0")
        manifest.append({"path": relative, "sha256": source_hash})
    return digest.hexdigest(), manifest


def _eligible_points(
    documents: Iterable[ProtocolDocument], profile: str
) -> list[tuple[ProtocolDocument, PointSpec]]:
    allowed = {"approved"} if profile == "production" else {"candidate", "reviewed", "approved"}
    return sorted(
        (
            (document, point)
            for document in documents
            if document.document_kind == "device_map"
            and document.lifecycle in allowed
            and document.device.lifecycle in allowed
            for point in document.points
            if point.lifecycle in allowed
        ),
        key=lambda item: item[1].logical_id,
    )


def _point_record(document: ProtocolDocument, point: PointSpec) -> dict[str, object]:
    """Return one complete JSON-compatible generated point contract."""

    command = (
        None
        if point.command is None
        else {
            "status": point.command.status,
            "prerequisites": list(point.command.prerequisites),
            "acknowledgement": point.command.acknowledgement,
            "timeout_ms": point.command.timeout_ms,
            "safe_value": (
                None if point.command.safe_value is None else str(point.command.safe_value)
            ),
            "minimum_spacing_ms": point.command.minimum_spacing_ms,
            "ramp": point.command.ramp,
        }
    )
    return {
        "access": point.access,
        "address": {
            "bit": point.address.bit,
            "convention": point.address.convention,
            "display": point.address.display,
            "notation": point.address.notation,
            "space": point.address.space,
            "status": point.address.status,
            "wire_offset": point.address.wire_offset,
        },
        "applicability": {
            "endpoint_status": document.device.endpoint_status,
            "firmware": {
                "status": document.device.firmware.status,
                "values": list(document.device.firmware.values),
            },
            "map_revision": {
                "status": document.device.map_revision.status,
                "values": list(document.device.map_revision.values),
            },
            "model": {
                "status": document.device.model.status,
                "values": list(document.device.model.values),
            },
        },
        "command": command,
        "device_id": document.device.device_id,
        "engineering": {
            "maximum": (
                None if point.engineering.maximum is None else str(point.engineering.maximum)
            ),
            "minimum": (
                None if point.engineering.minimum is None else str(point.engineering.minimum)
            ),
            "offset": str(point.engineering.offset),
            "rounding": point.engineering.rounding,
            "scale": str(point.engineering.scale),
            "sentinels": list(point.engineering.sentinels),
            "status": point.engineering.status,
            "unit": point.engineering.unit,
        },
        "generated_name": point.generated_name,
        "lifecycle": point.lifecycle,
        "logical_id": point.logical_id,
        "notes": list(point.notes),
        "provenance": {
            "cells": point.provenance.cells,
            "raw_vendor_name": point.provenance.raw_vendor_name,
            "row": point.provenance.row,
            "sheet": point.provenance.sheet,
            "source_id": point.provenance.source_id,
            "source_sha256": point.provenance.source_sha256,
        },
        "quality": {
            "communication_error": point.quality.communication_error,
            "out_of_range": point.quality.out_of_range,
            "sentinel": point.quality.sentinel,
        },
        "service": point.service,
        "timing": {
            "poll_class": point.timing.poll_class,
            "receive_timestamp": point.timing.receive_timestamp,
            "retry_policy": point.timing.retry_policy,
            "source_timestamp": point.timing.source_timestamp,
            "stale_after_ms": point.timing.stale_after_ms,
            "status": point.timing.status,
            "timeout_ms": point.timing.timeout_ms,
        },
        "vendor_name": point.vendor_name,
        "wire": {
            "atomic_group": point.wire.atomic_group,
            "bit_definitions": dict(point.wire.bit_definitions),
            "byte_order": point.wire.byte_order,
            "primitive": point.wire.primitive,
            "signed": point.wire.signed,
            "status": point.wire.status,
            "word_count": point.wire.word_count,
            "word_order": point.wire.word_order,
        },
    }


def _python_string(value: str) -> str:
    """Render one deterministic double-quoted Python string literal."""

    return json.dumps(value, ensure_ascii=False)


def _python_optional_string(value: str | None) -> str:
    return "None" if value is None else _python_string(value)


def _python_string_tuple(values: tuple[str, ...]) -> str:
    items = ", ".join(_python_string(value) for value in values)
    if len(items) > 58:
        rendered = "\n".join(f"            {_python_string(value)}," for value in values)
        return f"(\n{rendered}\n        )"
    suffix = "," if len(values) == 1 else ""
    return f"({items}{suffix})"


def _python_artifact(points: list[tuple[ProtocolDocument, PointSpec]], input_hash: str) -> bytes:
    lines = [
        f'"""{WARNING}"""',
        "",
        "from __future__ import annotations",
        "",
        "from decimal import Decimal",
        "from typing import Final, TypedDict",
        "",
        "from skid2.protocol.codecs import CodecSpec",
        "",
        "",
        "class PointDescriptor(TypedDict):",
        '    """Generated candidate point metadata."""',
        "",
        "    logical_id: str",
        "    generated_name: str",
        "    device_id: str",
        "    service: str",
        "    access: str",
        "    address_space: str",
        "    address_display: str",
        "    wire_offset: int",
        "    bit: int | None",
        "    primitive: str",
        "    signed: bool",
        "    word_count: int",
        "    byte_order: str",
        "    word_order: str",
        "    atomic_group: str",
        "    scale: str",
        "    engineering_offset: str",
        "    unit: str",
        "    minimum: str | None",
        "    maximum: str | None",
        "    sentinels: tuple[int, ...]",
        "    rounding: str",
        "    timing_status: str",
        "    poll_class: str",
        "    timeout_ms: int | None",
        "    stale_after_ms: int | None",
        "    retry_policy: str",
        "    sentinel_quality: str",
        "    communication_error_quality: str",
        "    out_of_range_quality: str",
        "    model_status: str",
        "    model_values: tuple[str, ...]",
        "    firmware_status: str",
        "    firmware_values: tuple[str, ...]",
        "    map_revision_status: str",
        "    map_revision_values: tuple[str, ...]",
        "    endpoint_status: str",
        "    command_status: str | None",
        "    command_prerequisites: tuple[str, ...]",
        "    command_acknowledgement: str | None",
        "    command_timeout_ms: int | None",
        "    command_safe_value: str | None",
        "    command_minimum_spacing_ms: int | None",
        "    command_ramp: str | None",
        "    lifecycle: str",
        "    source_id: str",
        "    source_sha256: str",
        "    source_sheet: str",
        "    source_row: int",
        "    source_cells: str",
        "",
        "",
        f'INPUT_SET_SHA256: Final = "{input_hash}"',
        f'GENERATOR_VERSION: Final = "{GENERATOR_VERSION}"',
        "CANDIDATE_POINTS: Final[tuple[PointDescriptor, ...]] = (",
    ]
    for document, point in points:
        command = point.command
        lines.extend(
            [
                "    {",
                f'        "logical_id": {_python_string(point.logical_id)},',
                f'        "generated_name": {_python_string(point.generated_name)},',
                f'        "device_id": {_python_string(document.device.device_id)},',
                f'        "service": {_python_string(point.service)},',
                f'        "access": {_python_string(point.access)},',
                f'        "address_space": {_python_string(point.address.space)},',
                f'        "address_display": {_python_string(point.address.display)},',
                f'        "wire_offset": {point.address.wire_offset},',
                f'        "bit": {point.address.bit!r},',
                f'        "primitive": {_python_string(point.wire.primitive)},',
                f'        "signed": {point.wire.signed!r},',
                f'        "word_count": {point.wire.word_count},',
                f'        "byte_order": {_python_string(point.wire.byte_order)},',
                f'        "word_order": {_python_string(point.wire.word_order)},',
                f'        "atomic_group": {_python_string(point.wire.atomic_group)},',
                f'        "scale": {_python_string(str(point.engineering.scale))},',
                (f'        "engineering_offset": {_python_string(str(point.engineering.offset))},'),
                f'        "unit": {_python_string(point.engineering.unit)},',
                (
                    f'        "minimum": '
                    f"{_python_optional_string(None if point.engineering.minimum is None else str(point.engineering.minimum))},"
                ),
                (
                    f'        "maximum": '
                    f"{_python_optional_string(None if point.engineering.maximum is None else str(point.engineering.maximum))},"
                ),
                f'        "sentinels": {point.engineering.sentinels!r},',
                f'        "rounding": {_python_string(point.engineering.rounding)},',
                f'        "timing_status": {_python_string(point.timing.status)},',
                f'        "poll_class": {_python_string(point.timing.poll_class)},',
                f'        "timeout_ms": {point.timing.timeout_ms!r},',
                f'        "stale_after_ms": {point.timing.stale_after_ms!r},',
                f'        "retry_policy": {_python_string(point.timing.retry_policy)},',
                f'        "sentinel_quality": {_python_string(point.quality.sentinel)},',
                (
                    f'        "communication_error_quality": '
                    f"{_python_string(point.quality.communication_error)},"
                ),
                (f'        "out_of_range_quality": {_python_string(point.quality.out_of_range)},'),
                f'        "model_status": {_python_string(document.device.model.status)},',
                (f'        "model_values": {_python_string_tuple(document.device.model.values)},'),
                (f'        "firmware_status": {_python_string(document.device.firmware.status)},'),
                (
                    f'        "firmware_values": '
                    f"{_python_string_tuple(document.device.firmware.values)},"
                ),
                (
                    f'        "map_revision_status": '
                    f"{_python_string(document.device.map_revision.status)},"
                ),
                (
                    f'        "map_revision_values": '
                    f"{_python_string_tuple(document.device.map_revision.values)},"
                ),
                (f'        "endpoint_status": {_python_string(document.device.endpoint_status)},'),
                (
                    f'        "command_status": '
                    f"{_python_optional_string(None if command is None else command.status)},"
                ),
                (
                    f'        "command_prerequisites": '
                    f"{_python_string_tuple(() if command is None else command.prerequisites)},"
                ),
                (
                    f'        "command_acknowledgement": '
                    f"{_python_optional_string(None if command is None else command.acknowledgement)},"
                ),
                (
                    f'        "command_timeout_ms": '
                    f"{None if command is None else command.timeout_ms!r},"
                ),
                (
                    f'        "command_safe_value": '
                    f"{_python_optional_string(None if command is None or command.safe_value is None else str(command.safe_value))},"
                ),
                (
                    f'        "command_minimum_spacing_ms": '
                    f"{None if command is None else command.minimum_spacing_ms!r},"
                ),
                (
                    f'        "command_ramp": '
                    f"{_python_optional_string(None if command is None else command.ramp)},"
                ),
                f'        "lifecycle": {_python_string(point.lifecycle)},',
                f'        "source_id": {_python_string(point.provenance.source_id)},',
                (f'        "source_sha256": {_python_string(point.provenance.source_sha256)},'),
                f'        "source_sheet": {_python_string(point.provenance.sheet)},',
                f'        "source_row": {point.provenance.row},',
                f'        "source_cells": {_python_string(point.provenance.cells)},',
                "    },",
            ]
        )
    lines.extend((")", "", "CODEC_SPECS: Final[dict[str, CodecSpec]] = {"))
    for _document, point in points:
        lines.extend(
            (
                f"    {_python_string(point.logical_id)}: CodecSpec(",
                f"        primitive={_python_string(point.wire.primitive)},",
                f"        word_count={point.wire.word_count},",
                f"        byte_order={_python_string(point.wire.byte_order)},",
                f"        word_order={_python_string(point.wire.word_order)},",
                (f"        scale=Decimal({_python_string(str(point.engineering.scale))}),"),
                (f"        offset=Decimal({_python_string(str(point.engineering.offset))}),"),
                (
                    "        minimum=None,"
                    if point.engineering.minimum is None
                    else (
                        f"        minimum=Decimal("
                        f"{_python_string(str(point.engineering.minimum))}),"
                    )
                ),
                (
                    "        maximum=None,"
                    if point.engineering.maximum is None
                    else (
                        f"        maximum=Decimal("
                        f"{_python_string(str(point.engineering.maximum))}),"
                    )
                ),
                f"        sentinels={point.engineering.sentinels!r},",
                f"        rounding={_python_string(point.engineering.rounding)},",
                f"        bit={point.address.bit!r},",
                "    ),",
            )
        )
    lines.extend(("}", ""))
    return "\n".join(lines).encode()


def _simulator_artifact(points: list[tuple[ProtocolDocument, PointSpec]], input_hash: str) -> bytes:
    devices: dict[str, list[dict[str, object]]] = {}
    for document, point in points:
        devices.setdefault(document.device.device_id, []).append(_point_record(document, point))
    return (
        json.dumps(
            {
                "_warning": WARNING,
                "devices": {
                    key: sorted(value, key=lambda item: str(item["logical_id"]))
                    for key, value in sorted(devices.items())
                },
                "generator_version": GENERATOR_VERSION,
                "input_set_sha256": input_hash,
                "profile": "review",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _logix_type(point: PointSpec) -> str:
    if point.wire.primitive == "bit":
        return "BOOL"
    if point.wire.primitive in {"uint16", "int16", "bitfield"}:
        return "INT"
    if point.wire.primitive in {"uint32", "int32"}:
        return "DINT"
    raise GenerationError(f"{point.logical_id} has no reviewed neutral Logix representation")


def _plc_artifact(points: list[tuple[ProtocolDocument, PointSpec]], input_hash: str) -> bytes:
    target = io.StringIO(newline="")
    writer = csv.writer(target, lineterminator="\n")
    writer.writerow(
        (
            "Tag Name",
            "Data Type",
            "Scope",
            "Direction",
            "Logical ID",
            "Device ID",
            "Lifecycle",
            "Service",
            "Register Space",
            "Wire Offset",
            "Bit",
            "Primitive",
            "Word Count",
            "Byte Order",
            "Word Order",
            "Scale",
            "Engineering Offset",
            "Unit",
            "Minimum",
            "Maximum",
            "Sentinels",
            "Timing Status",
            "Model Status",
            "Firmware Status",
            "Map Revision Status",
            "Source ID",
            "Source SHA-256",
            "Source Sheet",
            "Source Row",
            "Description",
            "Input Set SHA-256",
        )
    )
    for document, point in points:
        direction = {
            "read": "DEVICE_TO_PLC",
            "write": "PLC_TO_DEVICE",
            "read_write": "BIDIRECTIONAL",
        }[point.access]
        writer.writerow(
            (
                point.generated_name,
                _logix_type(point),
                "Controller",
                direction,
                point.logical_id,
                document.device.device_id,
                point.lifecycle.upper(),
                point.service,
                point.address.space,
                point.address.wire_offset,
                "" if point.address.bit is None else point.address.bit,
                point.wire.primitive,
                point.wire.word_count,
                point.wire.byte_order,
                point.wire.word_order,
                str(point.engineering.scale),
                str(point.engineering.offset),
                point.engineering.unit,
                ("" if point.engineering.minimum is None else str(point.engineering.minimum)),
                ("" if point.engineering.maximum is None else str(point.engineering.maximum)),
                ";".join(str(value) for value in point.engineering.sentinels),
                point.timing.status,
                document.device.model.status,
                document.device.firmware.status,
                document.device.map_revision.status,
                point.provenance.source_id,
                point.provenance.source_sha256,
                point.provenance.sheet,
                point.provenance.row,
                (
                    f"{WARNING} {point.vendor_name}; "
                    f"{point.provenance.source_id} row {point.provenance.row}"
                ),
                input_hash,
            )
        )
    return target.getvalue().encode()


def _contract_artifact(
    documents: tuple[ProtocolDocument, ...],
    points: list[tuple[ProtocolDocument, PointSpec]],
    input_hash: str,
) -> bytes:
    selected = {point.logical_id for _document, point in points}
    inventory = [
        {
            **_point_record(document, point),
            "review_fixture_status": (
                "candidate_testable" if point.logical_id in selected else point.lifecycle
            ),
        }
        for document in sorted(documents, key=lambda item: item.document_id)
        for point in sorted(document.points, key=lambda item: item.logical_id)
    ]
    return (
        json.dumps(
            {
                "_warning": WARNING,
                "generator_version": GENERATOR_VERSION,
                "input_set_sha256": input_hash,
                "inventory": inventory,
                "production_eligible_count": sum(
                    item["lifecycle"] == "approved" for item in inventory
                ),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _mapping_artifact(documents: tuple[ProtocolDocument, ...], input_hash: str) -> bytes:
    lines = [
        "# Generated Protocol Review Mapping",
        "",
        f"> **{WARNING}**",
        ">",
        "> Candidate rows are not approved device contracts. Skid 1 reference rows are not",
        "> the DC600 contract. Blocked interfaces contain no invented points.",
        "",
        f"- Generator: `{GENERATOR_VERSION}`",
        f"- Input set SHA-256: `{input_hash}`",
        "",
    ]
    for document in sorted(documents, key=lambda item: item.document_id):
        lines.extend(
            (
                f"## {document.device.display_name}",
                "",
                f"- Document status: **{document.lifecycle}**",
                f"- Model applicability: **{document.device.model.status}**",
                f"- Firmware applicability: **{document.device.firmware.status}**",
                f"- Endpoint status: **{document.device.endpoint_status}**",
                "",
            )
        )
        if document.document_kind == "reference_map":
            lines.extend(
                (
                    "> **REFERENCE ONLY — NOT THE DC600 PRODUCTION CONTRACT.**",
                    "",
                )
            )
        if document.points:
            lines.extend(
                (
                    "| Logical ID | Vendor name | Display | Wire offset/bit | Type/order | Scale/unit | Range/sentinels | Access | Timing/quality | Applicability | Status | Source |",
                    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
                )
            )
            for point in sorted(document.points, key=lambda item: item.logical_id):
                bit = "" if point.address.bit is None else f".{point.address.bit}"
                minimum = (
                    "unknown"
                    if point.engineering.minimum is None
                    else str(point.engineering.minimum)
                )
                maximum = (
                    "unknown"
                    if point.engineering.maximum is None
                    else str(point.engineering.maximum)
                )
                sentinels = ",".join(str(value) for value in point.engineering.sentinels) or "none"
                lines.append(
                    "| "
                    + " | ".join(
                        (
                            f"`{point.logical_id}`",
                            point.vendor_name.replace("|", "/"),
                            f"`{point.address.display}`",
                            f"`{point.address.wire_offset}{bit}`",
                            (
                                f"{point.wire.primitive}/{point.wire.word_count}w/"
                                f"{point.wire.byte_order}-{point.wire.word_order}"
                            ),
                            f"{point.engineering.scale} {point.engineering.unit}",
                            f"{minimum}..{maximum}; raw invalid={sentinels}",
                            point.access,
                            (
                                f"{point.timing.status}/{point.timing.poll_class}; "
                                f"timeout={point.timing.timeout_ms}; "
                                f"stale={point.timing.stale_after_ms}; "
                                f"quality={point.quality.sentinel}/"
                                f"{point.quality.communication_error}/"
                                f"{point.quality.out_of_range}"
                            ),
                            (
                                f"model={document.device.model.status}; "
                                f"firmware={document.device.firmware.status}; "
                                f"map={document.device.map_revision.status}; "
                                f"endpoint={document.device.endpoint_status}"
                            ),
                            point.lifecycle,
                            (
                                f"{point.provenance.source_id} "
                                f"{point.provenance.sheet.strip()} row {point.provenance.row} "
                                f"{point.provenance.cells}"
                            ),
                        )
                    )
                    + " |"
                )
            lines.append("")
        for blocker in document.blockers:
            lines.extend(
                (
                    f"### Blocker: {blocker.blocker_id}",
                    "",
                    f"- Owner: {blocker.owner}",
                    f"- Decision/source: {blocker.decision_id}; {blocker.source_id}",
                    f"- Reason: {blocker.reason}",
                    f"- Missing: {'; '.join(blocker.missing)}",
                    "",
                )
            )
    return "\n".join(lines).encode()


def _contract_report(
    documents: tuple[ProtocolDocument, ...],
    points: list[tuple[ProtocolDocument, PointSpec]],
    input_hash: str,
) -> bytes:
    selected = {point.logical_id for _document, point in points}
    rows = [
        {
            "device_id": document.device.device_id,
            "logical_id": point.logical_id,
            "result": (
                "candidate_contract_covered" if point.logical_id in selected else point.lifecycle
            ),
            "source": (
                f"{point.provenance.source_id} {point.provenance.sheet.strip()} "
                f"row {point.provenance.row}"
            ),
        }
        for document in sorted(documents, key=lambda item: item.document_id)
        for point in sorted(document.points, key=lambda item: item.logical_id)
    ]
    return (
        json.dumps(
            {
                "_warning": WARNING,
                "generator_version": GENERATOR_VERSION,
                "input_set_sha256": input_hash,
                "points": rows,
                "summary": {
                    "candidate_contract_covered": sum(
                        item["result"] == "candidate_contract_covered" for item in rows
                    ),
                    "production_pass": sum(item["result"] == "approved" for item in rows),
                    "reference_or_blocked": sum(
                        item["result"] in {"reference_only", "blocked"} for item in rows
                    ),
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _production_status(documents: tuple[ProtocolDocument, ...], input_hash: str) -> bytes:
    blockers = sorted(
        {
            f"{document.device.device_id}: {blocker.decision_id} — {blocker.reason}"
            for document in documents
            for blocker in document.blockers
        }
    )
    return (
        json.dumps(
            {
                "_warning": "No production protocol contract is generated while blockers remain.",
                "blockers": blockers,
                "eligible_point_count": sum(
                    point.lifecycle == "approved"
                    and document.lifecycle == "approved"
                    and document.device.lifecycle == "approved"
                    for document in documents
                    for point in document.points
                ),
                "generator_version": GENERATOR_VERSION,
                "input_set_sha256": input_hash,
                "status": "blocked",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _require_valid(report: ValidationReport) -> None:
    if report.accepted:
        return
    messages = "\n".join(
        f"{item.source}:{item.path}: {item.code}: {item.message}"
        for item in report.diagnostics[:20]
    )
    raise GenerationError(f"protocol validation failed:\n{messages}")


def render_artifacts(
    input_root: Path,
    schema_path: Path,
    profile: str = "review",
) -> tuple[GeneratedArtifact, ...]:
    """Render every target without touching the filesystem."""

    paths = protocol_paths(input_root)
    validation_profile = "production" if profile == "production" else "candidate"
    report = validate_paths(paths, validation_profile)
    _require_valid(report)
    input_hash, input_manifest = _input_identity(paths, input_root)
    try:
        schema_relative = schema_path.relative_to(input_root)
    except ValueError as exc:
        raise GenerationError("schema path must be inside the protocol input root") from exc
    schema_content = schema_path.read_bytes()
    generator_sha256 = _digest(Path(__file__).read_bytes())
    points = _eligible_points(report.documents, profile)
    artifacts = [
        GeneratedArtifact(
            Path("python/point_descriptors.py"),
            _python_artifact(points, input_hash),
        ),
        GeneratedArtifact(
            Path("simulator/register_banks.json"),
            _simulator_artifact(points, input_hash),
        ),
        GeneratedArtifact(
            Path("plc/candidate_tags.csv"),
            _plc_artifact(points, input_hash),
        ),
        GeneratedArtifact(
            Path("contracts/fixtures.json"),
            _contract_artifact(report.documents, points, input_hash),
        ),
        GeneratedArtifact(
            Path("contracts/report.json"),
            _contract_report(report.documents, points, input_hash),
        ),
        GeneratedArtifact(
            Path("mapping.md"),
            _mapping_artifact(report.documents, input_hash),
        ),
        GeneratedArtifact(
            Path("production/status.json"),
            _production_status(report.documents, input_hash),
        ),
    ]
    artifact_manifest = [
        {"path": item.path.as_posix(), "sha256": _digest(item.content)} for item in artifacts
    ]
    provenance = (
        json.dumps(
            {
                "_warning": WARNING,
                "artifacts": artifact_manifest,
                "generator": {
                    "sha256": generator_sha256,
                    "version": GENERATOR_VERSION,
                },
                "input_set_sha256": input_hash,
                "inputs": input_manifest,
                "reproducible_build": "No timestamps, absolute paths, locale data, or random order.",
                "schema": {
                    "path": schema_relative.as_posix(),
                    "sha256": _digest(schema_content),
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    artifacts.append(GeneratedArtifact(Path("provenance.json"), provenance))
    return tuple(sorted(artifacts, key=lambda item: item.path))


def write_artifacts(artifacts: tuple[GeneratedArtifact, ...], output_root: Path) -> None:
    """Write a complete rendered set using explicit relative targets."""

    expected = {artifact.path for artifact in artifacts}
    if output_root.exists():
        for path in sorted(output_root.rglob("*"), reverse=True):
            if path.is_file() and path.relative_to(output_root) not in expected:
                path.unlink()
        for path in sorted(output_root.rglob("*"), reverse=True):
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
    for artifact in artifacts:
        target = output_root / artifact.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(artifact.content)


def check_drift(
    artifacts: tuple[GeneratedArtifact, ...],
    output_root: Path,
    *,
    diff_lines: int = 80,
) -> tuple[str, ...]:
    """Return bounded diagnostics for missing, extra, or hand-edited outputs."""

    expected = {artifact.path: artifact.content for artifact in artifacts}
    actual_paths = (
        {
            path.relative_to(output_root)
            for path in output_root.rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        }
        if output_root.exists()
        else set()
    )
    diagnostics: list[str] = []
    diagnostics.extend(
        f"missing generated artifact: {path}" for path in sorted(set(expected) - actual_paths)
    )
    diagnostics.extend(
        f"unexpected generated artifact: {path}" for path in sorted(actual_paths - set(expected))
    )
    for path in sorted(actual_paths & set(expected)):
        actual = (output_root / path).read_bytes()
        wanted = expected[path]
        if actual == wanted:
            continue
        diff = list(
            difflib.unified_diff(
                actual.decode(errors="replace").splitlines(),
                wanted.decode(errors="replace").splitlines(),
                fromfile=f"committed/{path}",
                tofile=f"generated/{path}",
                lineterm="",
            )
        )
        diagnostics.append(f"generated artifact drift: {path}\n" + "\n".join(diff[:diff_lines]))
    return tuple(diagnostics)
