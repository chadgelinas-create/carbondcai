"""Read-only XLSX import, provenance, and staging behavior."""

from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from skid2.protocol.importer import (
    IMPORTER_VERSION,
    ColumnMapping,
    SheetAdapter,
    stage_workbook,
)
from skid2.protocol.xlsx import XlsxReadError, identify, read_sheet, sheet_names

ADAPTER = SheetAdapter(
    adapter_id="synthetic-layout-v1",
    sheet="Data",
    header_row=1,
    first_data_row=2,
    columns=(
        ColumnMapping("address", "A"),
        ColumnMapping("vendor_name", "B"),
        ColumnMapping("unit", "C"),
        ColumnMapping("definition", "D"),
    ),
    required_fields=("address", "vendor_name"),
)


def _workbook(path: Path, *, changed: bool = False) -> None:
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
  <Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"""
    root_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
  <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>
</Relationships>"""
    shared = """<?xml version="1.0" encoding="UTF-8"?>
<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="3" uniqueCount="3">
  <si><t>Address</t></si><si><t>Name</t></si><si><t>Unit</t></si>
</sst>"""
    value = "11" if changed else "10"
    worksheet = f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <dimension ref="A1:D5"/>
  <sheetData>
    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>
    <row r="2"><c r="A2"><v>{value}</v></c><c r="B2" t="inlineStr"><is><t>Voltage</t></is></c><c r="C2" t="inlineStr"><is><t>V</t></is></c><c r="D2"><f>A2*2</f><v>20</v></c></row>
    <row r="3"><c r="A3" t="inlineStr"><is><t>Footnote</t></is></c></row>
    <row r="4"><c r="A4" t="s"><v>0</v></c><c r="B4" t="s"><v>1</v></c></row>
    <row r="5"/>
  </sheetData>
  <mergeCells count="1"><mergeCell ref="A3:B3"/></mergeCells>
</worksheet>"""
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


def test_import_is_read_only_traceable_and_deterministic(tmp_path: Path) -> None:
    """Import preserves source bytes/mtime and emits stable raw staging."""

    path = tmp_path / "fixture.xlsx"
    _workbook(path)
    before = identify(path)
    first = stage_workbook(path, ADAPTER)
    second = stage_workbook(path, ADAPTER)
    after = identify(path)

    assert before == after
    assert first == second
    assert first.staging_json() == second.staging_json()
    assert first.source.sha256 == before.sha256
    assert first.dimension == "A1:D5"
    assert first.merged_range_count == 1
    assert [row.classification for row in first.rows] == [
        "imported",
        "malformed",
        "repeated_header",
    ]
    imported = first.rows[0]
    assert imported.lifecycle == "imported"
    assert imported.importer_version == IMPORTER_VERSION
    assert imported.cells[0].coordinate == "A2"
    assert imported.cells[3].formula == "A2*2"
    assert first.rows[1].cells[0].merged_range == "A3:B3"

    summary = first.safe_summary()
    assert summary["row_count"] == 3
    assert summary["malformed_row_count"] == 1
    assert summary["formula_cell_count"] == 1
    assert "Voltage" not in json.dumps(summary)


def test_changed_source_hash_and_layout_are_visible(tmp_path: Path) -> None:
    """Changed input cannot silently replace an earlier staging identity."""

    first_path = tmp_path / "first.xlsx"
    second_path = tmp_path / "second.xlsx"
    _workbook(first_path)
    _workbook(second_path, changed=True)
    first = stage_workbook(first_path, ADAPTER)
    second = stage_workbook(second_path, ADAPTER)
    assert first.source.sha256 != second.source.sha256
    assert first.staging_json() != second.staging_json()


def test_sheet_reader_exposes_cached_values_formulas_and_names(tmp_path: Path) -> None:
    """No Excel desktop application or formula evaluation is required."""

    path = tmp_path / "fixture.xlsx"
    _workbook(path)
    assert sheet_names(path) == ("Data",)
    sheet = read_sheet(path, "Data")
    row_two = dict(sheet.rows)[2]
    assert {cell.coordinate: cell.value for cell in row_two}["B2"] == "Voltage"
    assert {cell.coordinate: cell.formula for cell in row_two}["D2"] == "A2*2"
    with pytest.raises(XlsxReadError, match="does not exist"):
        read_sheet(path, "Missing")


def test_malformed_and_unsafe_archives_fail_cleanly(tmp_path: Path) -> None:
    """Corrupt or traversal-bearing XLSX input is rejected."""

    bad = tmp_path / "bad.xlsx"
    bad.write_text("not a zip", encoding="utf-8")
    with pytest.raises(XlsxReadError, match="cannot open"):
        sheet_names(bad)

    unsafe = tmp_path / "unsafe.xlsx"
    with ZipFile(unsafe, "w") as archive:
        archive.writestr("../escape.xml", "x")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    with pytest.raises(XlsxReadError, match="unsafe path"):
        sheet_names(unsafe)


def test_missing_file_identity_is_bounded(tmp_path: Path) -> None:
    """Missing sources produce an ordinary read error."""

    with pytest.raises(XlsxReadError, match="cannot read"):
        identify(tmp_path / "missing.xlsx")
