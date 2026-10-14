"""Cross-target generation, provenance, eligibility, and drift tests."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
from pathlib import Path
from types import ModuleType

import pytest

from skid2.protocol.cli import main
from skid2.protocol.generation import (
    GenerationError,
    check_drift,
    render_artifacts,
    write_artifacts,
)

ROOT = Path(__file__).parents[1]
INPUT = ROOT / "protocol"
SCHEMA = INPUT / "schema/protocol-v1.schema.json"
OUTPUT = ROOT / "generated/protocol"


def _artifacts() -> dict[str, bytes]:
    return {
        item.path.as_posix(): item.content for item in render_artifacts(INPUT, SCHEMA, "review")
    }


def _load_generated_python() -> ModuleType:
    target = OUTPUT / "python/point_descriptors.py"
    spec = importlib.util.spec_from_file_location("phase3_generated_points", target)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_repeated_generation_is_byte_identical_and_committed() -> None:
    """Two renders match each other and every committed generated file."""

    first = render_artifacts(INPUT, SCHEMA, "review")
    second = render_artifacts(INPUT, SCHEMA, "review")
    assert first == second
    assert not check_drift(first, OUTPUT)
    assert len(first) == 8


def test_generated_targets_have_one_input_identity_and_candidate_parity() -> None:
    """Python, simulator, PLC, and contract inventories cannot drift."""

    artifacts = _artifacts()
    provenance = json.loads(artifacts["provenance.json"])
    simulator = json.loads(artifacts["simulator/register_banks.json"])
    contracts = json.loads(artifacts["contracts/fixtures.json"])
    plc_rows = list(csv.DictReader(io.StringIO(artifacts["plc/candidate_tags.csv"].decode())))
    module = _load_generated_python()

    python_ids = {item["logical_id"] for item in module.CANDIDATE_POINTS}
    simulator_ids = {
        item["logical_id"] for points in simulator["devices"].values() for item in points
    }
    plc_ids = {item["Logical ID"] for item in plc_rows}
    contract_ids = {
        item["logical_id"]
        for item in contracts["inventory"]
        if item["review_fixture_status"] == "candidate_testable"
    }
    assert python_ids == simulator_ids == plc_ids == contract_ids
    assert set(module.CODEC_SPECS) == python_ids
    assert provenance["input_set_sha256"] == module.INPUT_SET_SHA256
    assert {row["Input Set SHA-256"] for row in plc_rows} == {provenance["input_set_sha256"]}
    assert all("DO NOT EDIT" in row["Description"] for row in plc_rows)
    for descriptor in module.CANDIDATE_POINTS:
        plc = next(row for row in plc_rows if row["Logical ID"] == descriptor["logical_id"])
        assert plc["Primitive"] == descriptor["primitive"]
        assert int(plc["Word Count"]) == descriptor["word_count"]
        assert plc["Byte Order"] == descriptor["byte_order"]
        assert plc["Word Order"] == descriptor["word_order"]
        assert plc["Scale"] == descriptor["scale"]
        assert plc["Timing Status"] == descriptor["timing_status"]
        assert plc["Model Status"] == descriptor["model_status"]
        assert plc["Firmware Status"] == descriptor["firmware_status"]
        assert plc["Source SHA-256"] == descriptor["source_sha256"]
    assert not any(logical_id.startswith("legacy.") for logical_id in python_ids)
    assert "dc600" in simulator["devices"]
    assert {item["logical_id"] for item in simulator["devices"]["dc600"]} >= {
        "dc600.evcs.demand.power",
        "dc600.pms.available.power",
    }
    demand = next(
        item
        for item in simulator["devices"]["dc600"]
        if item["logical_id"] == "dc600.evcs.demand.power"
    )
    assert demand["address"]["display"] == "40001"
    assert demand["wire"]["primitive"] == "int32"
    assert demand["wire"]["byte_order"] == "big"
    assert demand["wire"]["word_order"] == "big"
    assert demand["wire"]["word_count"] == 2
    assert not {"st-4", "st-5", "st-6"} & set(simulator["devices"])


def test_provenance_hashes_every_artifact_without_machine_data() -> None:
    """Manifest hashes are correct and reproducible-build content is stable."""

    artifacts = _artifacts()
    provenance = json.loads(artifacts["provenance.json"])
    assert provenance["generator"]["version"] == "1.0.0"
    assert len(provenance["generator"]["sha256"]) == 64
    assert len(provenance["schema"]["sha256"]) == 64
    for item in provenance["artifacts"]:
        import hashlib

        assert hashlib.sha256(artifacts[item["path"]]).hexdigest() == item["sha256"]
    rendered_text = "\n".join(content.decode(errors="replace") for content in artifacts.values())
    assert str(ROOT) not in rendered_text
    assert "generated_at" not in rendered_text
    assert "2026-" not in rendered_text


def test_reference_and_blocked_material_is_visible_but_not_generated() -> None:
    """Review docs expose blockers while production status remains fail-closed."""

    artifacts = _artifacts()
    mapping = artifacts["mapping.md"].decode()
    status = json.loads(artifacts["production/status.json"])
    assert "NOT THE DC600 PRODUCTION CONTRACT" in mapping
    assert "Siemens/EVCS confirm of exact addresses" in mapping
    assert "Stabiliti ST-4" in mapping
    assert status["status"] == "blocked"
    assert status["eligible_point_count"] == 0
    assert status["blockers"]


def test_tamper_missing_and_extra_artifacts_fail_with_bounded_diff(tmp_path: Path) -> None:
    """Hand edits and incomplete generated sets cannot pass drift checks."""

    rendered = render_artifacts(INPUT, SCHEMA, "review")
    output = tmp_path / "generated"
    write_artifacts(rendered, output)
    target = output / "mapping.md"
    target.write_text(target.read_text(encoding="utf-8") + "\nhand edit\n", encoding="utf-8")
    (output / "extra.txt").write_text("extra", encoding="utf-8")
    (output / "python/point_descriptors.py").unlink()
    diagnostics = check_drift(rendered, output, diff_lines=6)
    assert any("drift" in item and len(item.splitlines()) <= 8 for item in diagnostics)
    assert any("missing" in item for item in diagnostics)
    assert any("unexpected" in item for item in diagnostics)

    write_artifacts(rendered, output)
    assert not check_drift(rendered, output)
    assert not (output / "extra.txt").exists()


def test_production_generation_refuses_current_candidate_portfolio() -> None:
    """No current workbook row can become a production artifact."""

    with pytest.raises(GenerationError, match="validation failed"):
        render_artifacts(INPUT, SCHEMA, "production")


def test_protocol_cli_validate_generate_drift_and_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The documented CLI is useful locally and in CI."""

    output = tmp_path / "generated"
    assert main(["validate", str(INPUT / "devices/ba1-bess-v1.18.candidate.yaml")]) == 0
    assert (
        main(
            [
                "generate",
                "--input-root",
                str(INPUT),
                "--schema",
                str(SCHEMA),
                "--output-root",
                str(output),
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "check-drift",
                "--input-root",
                str(INPUT),
                "--schema",
                str(SCHEMA),
                "--output-root",
                str(output),
            ]
        )
        == 0
    )
    (output / "mapping.md").write_text("tampered", encoding="utf-8")
    assert (
        main(
            [
                "check-drift",
                "--input-root",
                str(INPUT),
                "--schema",
                str(SCHEMA),
                "--output-root",
                str(output),
            ]
        )
        == 1
    )
    assert "drift" in capsys.readouterr().out


def test_generated_python_and_contract_source_locations_are_complete() -> None:
    """Every review-testable point carries exact source identity and location."""

    contracts = json.loads((OUTPUT / "contracts/fixtures.json").read_text())
    candidates = [
        item
        for item in contracts["inventory"]
        if item["review_fixture_status"] == "candidate_testable"
    ]
    assert candidates
    for item in candidates:
        provenance = item["provenance"]
        assert provenance["source_id"].startswith("SRC-")
        assert len(provenance["source_sha256"]) == 64
        assert provenance["sheet"]
        assert provenance["row"] > 0
        assert provenance["cells"]
