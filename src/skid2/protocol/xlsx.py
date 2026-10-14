"""Small read-only XLSX boundary for deterministic vendor staging.

This is intentionally not a spreadsheet calculation engine. It reads cached
cell values, formulas, shared strings, and merged-range context from authorized
XLSX files without opening them for write access.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from posixpath import join, normpath
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"m": MAIN_NS, "r": REL_NS, "p": PACKAGE_REL_NS}
MAX_ARCHIVE_ENTRIES = 2_000
MAX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
CELL_REF = re.compile(r"^([A-Z]{1,3})([1-9][0-9]*)$")


class XlsxReadError(ValueError):
    """A bounded, user-facing XLSX read failure."""


@dataclass(frozen=True, slots=True)
class XlsxIdentity:
    """Immutable file identity used by staging provenance."""

    filename: str
    sha256: str
    size: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class XlsxCell:
    """One cached cell value with formula and merged-range context."""

    coordinate: str
    value: str
    formula: str
    merged_range: str


@dataclass(frozen=True, slots=True)
class XlsxSheet:
    """Read-only sheet snapshot."""

    name: str
    dimension: str
    merged_ranges: tuple[str, ...]
    rows: tuple[tuple[int, tuple[XlsxCell, ...]], ...]


def identify(path: Path) -> XlsxIdentity:
    """Hash a workbook without changing its bytes or timestamps."""

    try:
        stat = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise XlsxReadError(f"cannot read workbook {path.name!r}: {exc}") from exc
    return XlsxIdentity(path.name, digest, stat.st_size, stat.st_mtime_ns)


def _safe_archive(archive: ZipFile) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise XlsxReadError("workbook archive contains too many entries")
    total = 0
    for info in infos:
        name = PurePosixPath(info.filename)
        if name.is_absolute() or ".." in name.parts:
            raise XlsxReadError("workbook archive contains an unsafe path")
        total += info.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise XlsxReadError("workbook archive exceeds the uncompressed-size limit")


def _xml(archive: ZipFile, name: str) -> ElementTree.Element:
    try:
        # XLSX parts are size/entry bounded before this call, and DTD/entity
        # declarations are rejected so the standard parser cannot expand them.
        content = archive.read(name)
        if b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
            raise XlsxReadError(f"workbook XML part {name!r} contains a prohibited declaration")
        return ElementTree.fromstring(content)  # noqa: S314
    except (KeyError, ElementTree.ParseError) as exc:
        raise XlsxReadError(f"workbook XML part {name!r} is missing or malformed") from exc


def _shared_strings(archive: ZipFile) -> tuple[str, ...]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return ()
    root = _xml(archive, "xl/sharedStrings.xml")
    return tuple(
        "".join(text.text or "" for text in item.iterfind(".//m:t", NS))
        for item in root.findall("m:si", NS)
    )


def _sheet_path(archive: ZipFile, sheet_name: str) -> str:
    relationships = _xml(archive, "xl/_rels/workbook.xml.rels")
    targets = {
        item.attrib["Id"]: item.attrib["Target"]
        for item in relationships.findall("p:Relationship", NS)
    }
    workbook = _xml(archive, "xl/workbook.xml")
    for sheet in workbook.findall(".//m:sheets/m:sheet", NS):
        if sheet.attrib["name"] == sheet_name:
            relationship_id = sheet.attrib[f"{{{REL_NS}}}id"]
            target = targets[relationship_id]
            resolved = (
                target.lstrip("/") if target.startswith("/") else normpath(join("xl", target))
            )
            if ".." in PurePosixPath(resolved).parts:
                raise XlsxReadError("worksheet relationship escapes the workbook")
            return resolved
    raise XlsxReadError(f"sheet {sheet_name!r} does not exist")


def sheet_names(path: Path) -> tuple[str, ...]:
    """Return sheet names in workbook order."""

    try:
        with ZipFile(path) as archive:
            _safe_archive(archive)
            workbook = _xml(archive, "xl/workbook.xml")
            return tuple(
                sheet.attrib["name"] for sheet in workbook.findall(".//m:sheets/m:sheet", NS)
            )
    except (OSError, BadZipFile) as exc:
        raise XlsxReadError(f"cannot open workbook {path.name!r}") from exc


def _cell_column(coordinate: str) -> str:
    matched = CELL_REF.fullmatch(coordinate)
    if matched is None:
        raise XlsxReadError(f"invalid cell coordinate {coordinate!r}")
    return matched.group(1)


def _range_rows(value: str) -> tuple[int, int]:
    start, _, end = value.partition(":")
    start_match = CELL_REF.fullmatch(start)
    end_match = CELL_REF.fullmatch(end or start)
    if start_match is None or end_match is None:
        raise XlsxReadError(f"invalid merged range {value!r}")
    return int(start_match.group(2)), int(end_match.group(2))


def read_sheet(path: Path, sheet_name: str) -> XlsxSheet:
    """Read one sheet's cached values and review context."""

    try:
        with ZipFile(path) as archive:
            _safe_archive(archive)
            shared = _shared_strings(archive)
            root = _xml(archive, _sheet_path(archive, sheet_name))
    except (OSError, BadZipFile) as exc:
        raise XlsxReadError(f"cannot open workbook {path.name!r}") from exc

    dimension_element = root.find("m:dimension", NS)
    dimension = "unknown" if dimension_element is None else dimension_element.attrib["ref"]
    merges_element = root.find("m:mergeCells", NS)
    merged_ranges = (
        ()
        if merges_element is None
        else tuple(item.attrib["ref"] for item in merges_element.findall("m:mergeCell", NS))
    )
    merged_by_row: dict[int, list[str]] = {}
    for merged in merged_ranges:
        start_row, end_row = _range_rows(merged)
        for row_number in range(start_row, end_row + 1):
            merged_by_row.setdefault(row_number, []).append(merged)

    rows: list[tuple[int, tuple[XlsxCell, ...]]] = []
    sheet_data = root.find("m:sheetData", NS)
    if sheet_data is not None:
        for row in sheet_data.findall("m:row", NS):
            row_number = int(row.attrib["r"])
            cells: list[XlsxCell] = []
            for cell in row.findall("m:c", NS):
                coordinate = cell.attrib["r"]
                value_element = cell.find("m:v", NS)
                formula_element = cell.find("m:f", NS)
                inline_element = cell.find("m:is", NS)
                value = "" if value_element is None else value_element.text or ""
                cell_type = cell.attrib.get("t")
                if cell_type == "s" and value:
                    try:
                        value = shared[int(value)]
                    except (IndexError, ValueError) as exc:
                        raise XlsxReadError(f"invalid shared-string index at {coordinate}") from exc
                elif cell_type == "inlineStr" and inline_element is not None:
                    value = "".join(
                        item.text or "" for item in inline_element.iterfind(".//m:t", NS)
                    )
                column = _cell_column(coordinate)
                merged_context = next(
                    (
                        merged
                        for merged in merged_by_row.get(row_number, [])
                        if merged.startswith(f"{column}{row_number}:") or merged == coordinate
                    ),
                    "",
                )
                cells.append(
                    XlsxCell(
                        coordinate=coordinate,
                        value=value,
                        formula=("" if formula_element is None else formula_element.text or ""),
                        merged_range=merged_context,
                    )
                )
            rows.append((row_number, tuple(cells)))
    return XlsxSheet(sheet_name, dimension, merged_ranges, tuple(rows))
