"""Schema, semantic, lifecycle, and production-profile validation."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest

from skid2.protocol.loader import ProtocolInputError, load_raw, parse_document
from skid2.protocol.validation import validate_paths

ROOT = Path(__file__).parents[1]
EXAMPLE = ROOT / "protocol/examples/minimal-candidate.yaml"


def _raw() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(EXAMPLE.read_text()))


def _write(tmp_path: Path, value: object, name: str = "map.yaml") -> Path:
    target = tmp_path / name
    target.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target


def _codes(path: Path, profile: str = "candidate") -> set[str]:
    return {item.code for item in validate_paths([path], profile).diagnostics}


def test_schema_file_and_minimal_example_are_valid() -> None:
    """Versioned schema is valid JSON and its no-guess example is accepted."""

    schema = json.loads(
        (ROOT / "protocol/schema/protocol-v1.schema.json").read_text(encoding="utf-8")
    )
    assert schema["$id"].endswith("1.0.0")
    assert schema["additionalProperties"] is False
    report = validate_paths([EXAMPLE])
    assert report.accepted
    assert report.documents[0].schema_version == "1.0.0"


def test_repository_candidate_reference_and_blocker_documents_validate() -> None:
    """Every manually maintained source passes its allowed review profile."""

    candidate_paths = sorted((ROOT / "protocol/devices").glob("*.yaml"))
    candidate_paths += sorted((ROOT / "protocol/blocked").glob("*.yaml"))
    reference_paths = sorted((ROOT / "protocol/reference").glob("*.yaml"))
    candidate = validate_paths(candidate_paths, "candidate")
    reference = validate_paths(reference_paths, "reference")
    assert candidate.accepted, candidate.as_json()
    assert reference.accepted, reference.as_json()
    assert len(candidate.documents) == 7
    assert len(reference.documents) == 1


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda data: data.__setitem__("schema_version", "99"), "P3_SCHEMA_VERSION"),
        (
            lambda data: data["points"].append(copy.deepcopy(data["points"][0])),
            "P3_DUPLICATE_LOGICAL_ID",
        ),
        (
            lambda data: data["points"].append(
                {
                    **copy.deepcopy(data["points"][0]),
                    "logical_id": "example.other.value",
                }
            ),
            "P3_DUPLICATE_GENERATED_NAME",
        ),
        (
            lambda data: data["points"][0].__setitem__("access", "read_write"),
            "P3_SERVICE_ACCESS",
        ),
        (
            lambda data: data["points"][0]["wire"].__setitem__("word_count", 2),
            "P3_CODEC_SPEC",
        ),
        (
            lambda data: data["points"][0]["wire"].__setitem__("word_order", "unknown"),
            "P3_CODEC_SPEC",
        ),
        (
            lambda data: data["points"][0]["engineering"].__setitem__("scale", "0"),
            "P3_SCALE",
        ),
        (
            lambda data: data["points"][0]["engineering"].update({"minimum": "10", "maximum": "1"}),
            "P3_RANGE",
        ),
        (
            lambda data: data["points"][0]["engineering"].__setitem__("sentinels", [70000]),
            "P3_SENTINEL_RANGE",
        ),
        (
            lambda data: data["points"][0]["address"].__setitem__("wire_offset", 1),
            "P3_ADDRESS_MISMATCH",
        ),
        (
            lambda data: data["points"][0]["address"].__setitem__("bit", 16),
            "P3_ADDRESS",
        ),
        (
            lambda data: data["points"][0].update(
                {
                    "service": "modbus_holding_register_03_06",
                    "access": "write",
                    "command": None,
                }
            ),
            "P3_COMMAND_MISSING",
        ),
        (
            lambda data: data["points"][0].__setitem__(
                "command",
                {
                    "status": "blocked",
                    "prerequisites": [],
                    "acknowledgement": "unknown",
                    "timeout_ms": None,
                    "safe_value": None,
                    "minimum_spacing_ms": None,
                    "ramp": "unknown",
                },
            ),
            "P3_COMMAND_READ_ONLY",
        ),
        (
            lambda data: data["points"][0]["provenance"].__setitem__("source_sha256", "f" * 64),
            "P3_PROVENANCE",
        ),
    ],
)
def test_named_negative_validation_classes_fail(
    tmp_path: Path,
    mutation: Callable[[dict[str, Any]], object],
    expected_code: str,
) -> None:
    """Every Phase 3 semantic failure class returns a stable code."""

    data = _raw()
    mutation(data)
    assert expected_code in _codes(_write(tmp_path, data))


def test_overlap_is_rejected_but_distinct_bits_are_allowed(tmp_path: Path) -> None:
    """Word spans cannot overlap unless explicit bit positions are distinct."""

    data = _raw()
    other = copy.deepcopy(data["points"][0])
    other["logical_id"] = "example.other.value"
    other["generated_name"] = "EXAMPLE_OTHER_VALUE"
    data["points"].append(other)
    assert "P3_ADDRESS_OVERLAP" in _codes(_write(tmp_path, data))

    for index, point in enumerate(data["points"]):
        point["logical_id"] = f"example.bit.value{index}"
        point["generated_name"] = f"EXAMPLE_BIT_VALUE_{index}"
        point["address"]["bit"] = index
        point["wire"].update(
            {
                "primitive": "bit",
                "signed": False,
                "word_count": 1,
                "atomic_group": "example_bits",
            }
        )
        point["engineering"].update({"minimum": "0", "maximum": "1", "sentinels": []})
    assert validate_paths([_write(tmp_path, data, "bits.yaml")]).accepted


def test_bitfield_definition_validation(tmp_path: Path) -> None:
    """Bit definitions cannot repeat or leave the 16-bit word."""

    data = _raw()
    point = data["points"][0]
    point["wire"].update(
        {
            "primitive": "bitfield",
            "bit_definitions": {"0": "ready", "16": "invalid"},
        }
    )
    assert "P3_BIT_DEFINITION" in _codes(_write(tmp_path, data))


def test_missing_required_field_is_location_aware(tmp_path: Path) -> None:
    """Omitted fields are distinct from explicit unknown values."""

    data = _raw()
    del data["points"][0]["engineering"]["unit"]
    path = _write(tmp_path, data)
    report = validate_paths([path])
    assert not report.accepted
    assert report.diagnostics[0].code == "P3_INPUT"
    assert report.diagnostics[0].path.endswith(".engineering.unit")


def test_parser_rejects_duplicate_keys_non_object_and_bad_json(tmp_path: Path) -> None:
    """Ordinary source errors do not leak Python stack traces."""

    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text('{"schema_version":"1","schema_version":"2"}', encoding="utf-8")
    with pytest.raises(ProtocolInputError, match="duplicate key"):
        load_raw(duplicate)
    array = tmp_path / "array.yaml"
    array.write_text("[]", encoding="utf-8")
    with pytest.raises(ProtocolInputError, match="root"):
        parse_document(array)
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("{", encoding="utf-8")
    assert _codes(invalid) == {"P3_INPUT"}


def test_production_profile_fails_closed_for_every_unapproved_class() -> None:
    """Candidate, reference, and blocked sources cannot enter production."""

    all_paths = sorted((ROOT / "protocol").glob("devices/*.yaml"))
    all_paths += sorted((ROOT / "protocol").glob("reference/*.yaml"))
    all_paths += sorted((ROOT / "protocol").glob("blocked/*.yaml"))
    report = validate_paths(all_paths, "production")
    codes = {item.code for item in report.diagnostics}
    assert not report.accepted
    assert {
        "P3_PRODUCTION_APPLICABILITY",
        "P3_PRODUCTION_DOCUMENT_KIND",
        "P3_PRODUCTION_DOCUMENT_STATUS",
        "P3_PRODUCTION_POINT_STATUS",
        "P3_PRODUCTION_TIMING",
    } <= codes


def test_machine_output_and_diagnostic_order_are_deterministic(tmp_path: Path) -> None:
    """Repeated validation produces byte-identical JSON in stable order."""

    data = _raw()
    data["schema_version"] = "bad"
    data["points"][0]["engineering"]["scale"] = "0"
    path = _write(tmp_path, data)
    first = validate_paths([path]).as_json()
    second = validate_paths([path]).as_json()
    assert first == second
    parsed = json.loads(first)
    assert parsed["accepted"] is False
    assert parsed["diagnostics"] == sorted(
        parsed["diagnostics"],
        key=lambda item: (
            item["source"],
            item["path"],
            item["code"],
            item["severity"],
            item["message"],
        ),
    )


def test_unknown_profile_is_rejected() -> None:
    """Callers cannot invent a permissive validation profile."""

    with pytest.raises(ValueError, match="unsupported"):
        validate_paths([EXAMPLE], "lenient")


def test_address_offset_arithmetic_is_confined_to_normalizer() -> None:
    """Protocol production modules contain no scattered base conversion."""

    protocol_root = ROOT / "src/skid2/protocol"
    for path in protocol_root.glob("*.py"):
        if path.name == "address.py":
            continue
        content = path.read_text(encoding="utf-8")
        assert "wire_offset - 1" not in content
        assert "wire_offset + 1" not in content
