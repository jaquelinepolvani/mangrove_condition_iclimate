"""Writes an empty, ready to fill configuration workbook."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from . import __version__, spec
from .schema import Direction, EctClass, Level

FONT = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
HEADER_FONT = Font(name=FONT, bold=True, color="FFFFFF")
BODY_FONT = Font(name=FONT)
BOLD_FONT = Font(name=FONT, bold=True)
WRAP = Alignment(wrap_text=True, vertical="top")

MAX_ROWS = 200  # rows covered by the dropdowns


def _header(ws, columns: tuple[str, ...], help_text: dict[str, str] | None = None) -> None:
    for index, name in enumerate(columns, start=1):
        cell = ws.cell(row=1, column=index, value=name)
        cell.font, cell.fill = HEADER_FONT, HEADER_FILL
        cell.alignment = Alignment(vertical="center")
        if help_text and name in help_text:
            comment = Comment(help_text[name], "mangrove_condition")
            comment.width, comment.height = 320, 140
            cell.comment = comment
    ws.freeze_panes = "A2"


def _widths(ws, widths: dict[int, int]) -> None:
    for index, width in widths.items():
        ws.column_dimensions[get_column_letter(index)].width = width


def _list_validation(ws, cells: str, options: list[str], allow_blank: bool = True) -> None:
    validation = DataValidation(type="list", formula1='"' + ",".join(options) + '"', allow_blank=allow_blank)
    validation.error = "Choose a value from the list."
    validation.errorTitle = "Invalid value"
    ws.add_data_validation(validation)
    validation.add(cells)


def _indicators_sheet(ws) -> None:
    ws.title = spec.INDICATORS_SHEET
    _header(ws, spec.INDICATOR_COLUMNS, spec.INDICATOR_HELP)
    for r, row in enumerate(spec.TEMPLATE_INDICATOR_ROWS, start=2):
        for c, value in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c, value=value)
            cell.font = BODY_FONT
    for r in range(2, MAX_ROWS + 2):
        for c in range(1, len(spec.INDICATOR_COLUMNS) + 1):
            ws.cell(row=r, column=c).fill = INPUT_FILL if r <= 30 else PatternFill()
            ws.cell(row=r, column=c).font = BODY_FONT
    col = {name: get_column_letter(i) for i, name in enumerate(spec.INDICATOR_COLUMNS, start=1)}
    last = MAX_ROWS + 1
    _list_validation(ws, f"{col['ect_class']}2:{col['ect_class']}{last}", [e.value for e in EctClass])
    _list_validation(ws, f"{col['level']}2:{col['level']}{last}", [e.value for e in Level])
    _list_validation(ws, f"{col['direction']}2:{col['direction']}{last}", [e.value for e in Direction])
    _list_validation(ws, f"{col['in_index']}2:{col['in_index']}{last}", ["yes", "no"])
    _list_validation(ws, f"{col['island_map']}2:{col['island_map']}{last}", ["yes", "no"])
    _widths(ws, {1: 16, 2: 22, 3: 15, 4: 12, 5: 42, 6: 11, 7: 12, 8: 12, 9: 12, 10: 14, 11: 14, 12: 10, 13: 9,
                 14: 15, 15: 15, 16: 15, 17: 22, 18: 11})


def _anchors_sheet(ws) -> None:
    ws.title = spec.ANCHORS_SHEET
    _header(ws, spec.ANCHOR_COLUMNS, {"id": spec.ANCHOR_HELP})
    for r in range(2, MAX_ROWS + 2):
        for c in range(1, 4):
            ws.cell(row=r, column=c).font = BODY_FONT
    _widths(ws, {1: 16, 2: 12, 3: 12})


def _tables_sheet(ws) -> None:
    ws.title = spec.TABLES_SHEET
    _header(ws, spec.TABLE_COLUMNS, spec.TABLE_HELP)
    for r in range(2, 32):
        for c in range(1, len(spec.TABLE_COLUMNS) + 1):
            ws.cell(row=r, column=c).fill = INPUT_FILL
            ws.cell(row=r, column=c).font = BODY_FONT
    _widths(ws, {1: 16, 2: 40, 3: 18, 4: 16, 5: 14, 6: 16, 7: 16, 8: 16})


def _ecv_sheet(ws) -> None:
    ws.title = spec.ECV_SHEET
    _header(ws, spec.ECV_COLUMNS, spec.ECV_HELP)
    for r in range(2, 32):
        for c in range(1, len(spec.ECV_COLUMNS) + 1):
            ws.cell(row=r, column=c).fill = INPUT_FILL
            ws.cell(row=r, column=c).font = BODY_FONT
    _list_validation(ws, "I2:I31", ["mean", "sum", "none"])
    _widths(ws, {1: 16, 2: 28, 3: 10, 4: 44, 5: 18, 6: 14, 7: 14, 8: 20, 9: 13, 10: 12, 11: 14})


def _project_sheet(ws) -> None:
    ws.title = spec.PROJECT_SHEET
    _header(ws, spec.PROJECT_COLUMNS)
    for r, item in enumerate(spec.PROJECT_SPEC, start=2):
        ws.cell(row=r, column=1, value=item.key).font = BOLD_FONT
        value = ws.cell(row=r, column=2, value=item.default or None)
        value.font, value.fill = BODY_FONT, INPUT_FILL
        description = ws.cell(row=r, column=3, value=item.description)
        description.font, description.alignment = BODY_FONT, WRAP
        if item.kind in ("int", "float"):
            number = float(item.default)
            value.value = int(number) if item.kind == "int" else number
        if item.kind == "enum":
            _list_validation(ws, f"B{r}", list(item.choices), allow_blank=False)
        if item.kind == "bool":
            _list_validation(ws, f"B{r}", ["yes", "no"], allow_blank=False)
    _widths(ws, {1: 26, 2: 52, 3: 95})


def _changes_sheet(ws) -> None:
    ws.title = spec.CHANGES_SHEET
    _header(ws, spec.CHANGES_COLUMNS, {"change": "Log every change to the Indicators, Anchors or Project sheets: date, who, what and why."})
    first = (date.today().isoformat(), "", f"Template created by mangrove_condition {__version__}.")
    for c, value in enumerate(first, start=1):
        ws.cell(row=2, column=c, value=value).font = BODY_FONT
    _widths(ws, {1: 14, 2: 20, 3: 100})


def _legend_sheet(ws) -> None:
    ws.title = spec.LEGEND_SHEET
    _header(ws, ("sheet", "column or key", "meaning"))
    rows: list[tuple[str, str, str]] = [
        ("General", "yellow cells", "Cells to fill in. The first rows of Indicators contain examples: edit or replace them."),
        ("General", "empty cell", "Not decided yet. The code applies the fallback rules and reports what it did."),
        ("General", "language", "Use English in all cells."),
        ("Anchors", "example", "id COND_X, value 30, score 0 / value 60, score 1 / value 90, score 0 gives a peak at 60."),
        ("Indicators", "one row per indicator", "A row without raster_file is a placeholder: it is listed but skipped."),
    ]
    rows += [(spec.INDICATORS_SHEET, k, v) for k, v in spec.INDICATOR_HELP.items()]
    rows.append((spec.ANCHORS_SHEET, "id, value, score", spec.ANCHOR_HELP))
    rows += [(spec.TABLES_SHEET, k, v) for k, v in spec.TABLE_HELP.items()]
    rows += [(spec.ECV_SHEET, k, v) for k, v in spec.ECV_HELP.items()]
    rows += [(spec.PROJECT_SHEET, item.key, item.description + (f" Default: {item.default}." if item.default else "")) for item in spec.PROJECT_SPEC]
    rows += [
        ("Fallback rules", "no reference levels", "Reference levels are derived from the data (pooled over all years) and labelled data_derived. Scores are then relative."),
        ("Fallback rules", "too few values", "The variable is reported but stays out of the index."),
        ("Fallback rules", "no direction", "No score is computed and a warning is written."),
        ("Fallback rules", "active indicator, missing input", "The run stops with a clear message."),
    ]
    for r, (sheet, key, meaning) in enumerate(rows, start=2):
        ws.cell(row=r, column=1, value=sheet).font = BODY_FONT
        ws.cell(row=r, column=2, value=key).font = BOLD_FONT
        cell = ws.cell(row=r, column=3, value=meaning)
        cell.font, cell.alignment = BODY_FONT, WRAP
    _widths(ws, {1: 16, 2: 28, 3: 120})


def write_template(path: str | Path, overwrite: bool = False) -> Path:
    """Create the configuration workbook with examples, dropdowns and a legend."""
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists. Use overwrite to replace it.")
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    _indicators_sheet(workbook.active)
    _anchors_sheet(workbook.create_sheet())
    _tables_sheet(workbook.create_sheet())
    _ecv_sheet(workbook.create_sheet())
    _project_sheet(workbook.create_sheet())
    _changes_sheet(workbook.create_sheet())
    _legend_sheet(workbook.create_sheet())
    workbook.save(path)
    return path
