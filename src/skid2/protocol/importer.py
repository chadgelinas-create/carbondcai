"""Read-only vendor workbook import and traceable staging records."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from skid2.protocol.xlsx import XlsxCell, XlsxIdentity, identify, read_sheet

IMPORTER_VERSION = "1.0.0"


class ProtocolImportError(ValueError):
    """A deterministic vendor import error."""


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    """Vendor sheet column to neutral staging field."""

    field: str
    column: str


@dataclass(frozen=True, slots=True)
class SheetAdapter:
    """Small vendor-specific layout declaration."""

    adapter_id: str
    sheet: str
    header_row: int
    first_data_row: int
    columns: tuple[ColumnMapping, ...]
    required_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StagingCell:
    """Raw vendor cell content and formula/merge context."""

    field: str
    coordinate: str
    value: str
    formula: str
    merged_range: str


@dataclass(frozen=True, slots=True)
class StagingRow:
    """Unapproved workbook row; import never escalates lifecycle."""

    importer_version: str
    adapter_id: str
    source_filename: str
    source_sha256: str
    sheet: str
    row: int
    lifecycle: str
    classification: str
    cells: tuple[StagingCell, ...]


@dataclass(frozen=True, slots=True)
class ImportResult:
    """Deterministic staging result plus source-integrity evidence."""

    source: XlsxIdentity
    adapter_id: str
    sheet: str
    dimension: str
    merged_range_count: int
    rows: tuple[StagingRow, ...]

    def safe_summary(self) -> dict[str, int | str]:
        """Return non-confidential review evidence."""

        classifications: dict[str, int] = {}
        formula_cells = 0
        for row in self.rows:
            classifications[row.classification] = classifications.get(row.classification, 0) + 1
            formula_cells += sum(bool(cell.formula) for cell in row.cells)
        return {
            "adapter_id": self.adapter_id,
            "dimension": self.dimension,
            "formula_cell_count": formula_cells,
            "importer_version": IMPORTER_VERSION,
            "malformed_row_count": classifications.get("malformed", 0),
            "merged_range_count": self.merged_range_count,
            "row_count": len(self.rows),
            "source_filename": self.source.filename,
            "source_sha256": self.source.sha256,
        }

    def staging_json(self) -> str:
        """Render raw staging output only when explicitly requested."""

        return json.dumps(
            {
                "adapter_id": self.adapter_id,
                "importer_version": IMPORTER_VERSION,
                "rows": [asdict(row) for row in self.rows],
                "sheet": self.sheet,
                "source": asdict(self.source),
            },
            indent=2,
            sort_keys=True,
        )


BESS_ADAPTER = SheetAdapter(
    adapter_id="wenergy-bess-v1.18",
    sheet="Data Overview Page ",
    header_row=3,
    first_data_row=4,
    columns=(
        ColumnMapping("vendor_name_local", "B"),
        ColumnMapping("vendor_symbol", "C"),
        ColumnMapping("vendor_name", "E"),
        ColumnMapping("service", "F"),
        ColumnMapping("address", "G"),
        ColumnMapping("access", "H"),
        ColumnMapping("unit", "I"),
        ColumnMapping("raw_type", "J"),
        ColumnMapping("definition", "K"),
        ColumnMapping("remarks", "L"),
    ),
    required_fields=("address", "vendor_name"),
)
DCDC_RO_ADAPTER = SheetAdapter(
    adapter_id="sinexcel-v126-ro",
    sheet="RO-registers-M",
    header_row=2,
    first_data_row=3,
    columns=(
        ColumnMapping("address", "B"),
        ColumnMapping("size", "C"),
        ColumnMapping("access", "D"),
        ColumnMapping("raw_type", "E"),
        ColumnMapping("vendor_name", "F"),
        ColumnMapping("definition", "H"),
        ColumnMapping("comments", "J"),
    ),
    required_fields=("address", "vendor_name"),
)
DCDC_RW_ADAPTER = SheetAdapter(
    adapter_id="sinexcel-v126-rw",
    sheet="RW-registers-M",
    header_row=2,
    first_data_row=3,
    columns=DCDC_RO_ADAPTER.columns,
    required_fields=DCDC_RO_ADAPTER.required_fields,
)
ADAPTERS = {
    adapter.adapter_id: adapter for adapter in (BESS_ADAPTER, DCDC_RO_ADAPTER, DCDC_RW_ADAPTER)
}


def _column(coordinate: str) -> str:
    return "".join(character for character in coordinate if character.isalpha())


def _classification(cells: tuple[StagingCell, ...], adapter: SheetAdapter) -> str:
    values = {cell.field: cell.value.strip() for cell in cells}
    nonempty = [value for value in values.values() if value]
    if not nonempty:
        return "blank"
    if any(
        values.get(field, "").casefold() in {"address", "register name", "name"}
        for field in adapter.required_fields
    ):
        return "repeated_header"
    if any(not values.get(field, "") for field in adapter.required_fields):
        return "malformed"
    return "imported"


def stage_workbook(path: Path, adapter: SheetAdapter) -> ImportResult:
    """Stage a vendor sheet without modifying or approving its contents."""

    before = identify(path)
    sheet = read_sheet(path, adapter.sheet)
    row_lookup = dict(sheet.rows)
    rows: list[StagingRow] = []
    for row_number in sorted(row_lookup):
        if row_number < adapter.first_data_row:
            continue
        by_column: dict[str, XlsxCell] = {
            _column(cell.coordinate): cell for cell in row_lookup[row_number]
        }
        staged_cells = tuple(
            StagingCell(
                field=mapping.field,
                coordinate=f"{mapping.column}{row_number}",
                value=by_column.get(
                    mapping.column,
                    XlsxCell(
                        coordinate=f"{mapping.column}{row_number}",
                        value="",
                        formula="",
                        merged_range="",
                    ),
                ).value,
                formula=by_column.get(
                    mapping.column,
                    XlsxCell(
                        coordinate=f"{mapping.column}{row_number}",
                        value="",
                        formula="",
                        merged_range="",
                    ),
                ).formula,
                merged_range=by_column.get(
                    mapping.column,
                    XlsxCell(
                        coordinate=f"{mapping.column}{row_number}",
                        value="",
                        formula="",
                        merged_range="",
                    ),
                ).merged_range,
            )
            for mapping in adapter.columns
        )
        classification = _classification(staged_cells, adapter)
        if classification == "blank":
            continue
        rows.append(
            StagingRow(
                importer_version=IMPORTER_VERSION,
                adapter_id=adapter.adapter_id,
                source_filename=before.filename,
                source_sha256=before.sha256,
                sheet=adapter.sheet,
                row=row_number,
                lifecycle="imported",
                classification=classification,
                cells=staged_cells,
            )
        )
    after = identify(path)
    if before != after:
        raise ProtocolImportError("workbook changed while it was being imported")
    return ImportResult(
        source=before,
        adapter_id=adapter.adapter_id,
        sheet=adapter.sheet,
        dimension=sheet.dimension,
        merged_range_count=len(sheet.merged_ranges),
        rows=tuple(rows),
    )
