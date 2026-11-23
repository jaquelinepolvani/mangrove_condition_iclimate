"""Reads and validates the configuration workbook.

All problems are collected first and reported together, so the user can fix the
whole workbook in one pass instead of one error at a time.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from . import spec
from .provenance import fingerprint
from .schema import (
    Anchor,
    Config,
    Direction,
    EctClass,
    Indicator,
    Issue,
    Level,
    OutputLevel,
    ProjectSettings,
    EcvSpec,
    RebuildMode,
    TableSource,
)

ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
YES = {"yes", "y", "true", "1"}
NO = {"no", "n", "false", "0"}


class ConfigError(Exception):
    """Raised when the configuration contains at least one error."""

    def __init__(self, issues: Iterable[Issue]):
        self.issues = tuple(issues)
        errors = [i for i in self.issues if i.severity == "error"]
        lines = "\n".join(f"  {i}" for i in self.issues)
        super().__init__(f"The configuration has {len(errors)} error(s):\n{lines}")


# ---------------------------------------------------------------- cell helpers

def _clean(value: Any) -> Any:
    """Strip text and turn empty cells into None."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


class _Reader:
    """Collects issues while parsing cells."""

    def __init__(self) -> None:
        self.issues: list[Issue] = []

    def error(self, sheet: str, row: int | None, message: str) -> None:
        self.issues.append(Issue("error", sheet, row, message))

    def warn(self, sheet: str, row: int | None, message: str) -> None:
        self.issues.append(Issue("warning", sheet, row, message))

    def number(self, sheet: str, row: int, column: str, value: Any) -> float | None:
        value = _clean(value)
        if value is None:
            return None
        if _is_number(value):
            return float(value)
        self.error(sheet, row, f"'{column}' must be a number, found '{value}'.")
        return None

    def enum(self, sheet: str, row: int, column: str, value: Any, enum_cls: type) -> Any:
        value = _clean(value)
        if value is None:
            return None
        key = str(value).lower().replace("-", "_").replace(" ", "_")
        for member in enum_cls:
            if member.value.lower() == key:
                return member
        allowed = ", ".join(m.value for m in enum_cls)
        self.error(sheet, row, f"'{column}' has the value '{value}'. Allowed values: {allowed}.")
        return None

    def boolean(self, sheet: str, row: int, column: str, value: Any, default: bool) -> bool:
        value = _clean(value)
        if value is None:
            return default
        key = str(value).lower()
        if key in YES:
            return True
        if key in NO:
            return False
        self.error(sheet, row, f"'{column}' must be yes or no, found '{value}'.")
        return default


def _read_table(workbook, sheet: str, columns: tuple[str, ...], reader: _Reader,
                required: tuple[str, ...]) -> list[tuple[int, dict[str, Any]]]:
    """Rows of a sheet as (excel row number, {column: value}). Row 1 is the header."""
    if sheet not in workbook.sheetnames:
        reader.error(sheet, None, f"Sheet '{sheet}' is missing.")
        return []
    rows = workbook[sheet].iter_rows(values_only=True)
    header = next(rows, None)
    if header is None:
        reader.error(sheet, 1, "The sheet is empty. Row 1 must contain the column names.")
        return []
    names = [str(h).strip().lower() if h is not None else "" for h in header]
    missing = [c for c in required if c not in names]
    if missing:
        reader.error(sheet, 1, f"Missing column(s): {', '.join(missing)}.")
        return []
    unknown = [n for n in names if n and n not in columns]
    if unknown:
        reader.warn(sheet, 1, f"Unknown column(s) ignored: {', '.join(unknown)}.")
    result = []
    for number, values in enumerate(rows, start=2):
        record = {n: _clean(v) for n, v in zip(names, values, strict=False) if n}
        if all(record.get(c) is None for c in columns):
            continue
        result.append((number, record))
    return result


# ---------------------------------------------------------------- Project sheet

def _parse_years(reader: _Reader, row: int | None, key: str, value: Any,
                 sheet: str = spec.PROJECT_SHEET) -> tuple[int, ...]:
    if _is_number(value):
        parts = [value]
    else:
        parts = [p.strip() for p in str(value).replace(",", ";").split(";") if p.strip()]
    years: list[int] = []
    for part in parts:
        try:
            number = float(part)
        except (TypeError, ValueError):
            reader.error(sheet, row, f"'{key}' must list years separated by semicolons, found '{part}'.")
            return ()
        if number != int(number) or not 1900 <= number <= 2100:
            reader.error(sheet, row, f"'{key}' has an invalid year: '{part}'.")
            return ()
        years.append(int(number))
    if len(set(years)) != len(years):
        reader.error(sheet, row, f"'{key}' repeats a year.")
    return tuple(sorted(set(years)))


def _parse_pairs(reader: _Reader, row: int, key: str, value: Any) -> dict[str, str]:
    pairs: dict[str, str] = {}
    for part in str(value).split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            reader.error(spec.PROJECT_SHEET, row, f"'{key}' must use wrong=right pairs separated by semicolons, found '{part}'.")
            continue
        wrong, right = (s.strip() for s in part.split("=", 1))
        if not wrong or not right:
            reader.error(spec.PROJECT_SHEET, row, f"'{key}' has an empty side in '{part}'.")
            continue
        pairs[wrong] = right
    return pairs


def _read_project(workbook, reader: _Reader) -> ProjectSettings | None:
    rows = _read_table(workbook, spec.PROJECT_SHEET, spec.PROJECT_COLUMNS, reader, ("key", "value"))
    found: dict[str, tuple[int, Any]] = {}
    for number, record in rows:
        key = record.get("key")
        if key is None:
            reader.error(spec.PROJECT_SHEET, number, "The key is empty.")
            continue
        key = str(key).lower()
        if key not in spec.PROJECT_KEYS:
            reader.warn(spec.PROJECT_SHEET, number, f"Unknown key '{key}' ignored.")
            continue
        if key in found:
            reader.error(spec.PROJECT_SHEET, number, f"Key '{key}' is repeated (first in row {found[key][0]}).")
            continue
        found[key] = (number, record.get("value"))

    values: dict[str, Any] = {}
    for item in spec.PROJECT_SPEC:
        if item.key not in found:
            reader.warn(spec.PROJECT_SHEET, None, f"Key '{item.key}' is missing, default '{item.default}' used.")
            number = None
            raw = float(item.default) if item.kind in ("int", "float") else (item.default or None)
        else:
            number, raw = found[item.key]
        raw = _clean(raw)
        if raw is None:
            if item.required:
                reader.error(spec.PROJECT_SHEET, number, f"'{item.key}' is required but empty.")
            values[item.key] = None
            continue
        values[item.key] = _convert(reader, number, item, raw)

    _check_project_logic(reader, values, found)
    if any(i.severity == "error" and i.sheet == spec.PROJECT_SHEET for i in reader.issues):
        return None
    return ProjectSettings(
        account_name=values["account_name"],
        ecosystem_type=values["ecosystem_type"],
        units_file=values["units_file"],
        patch_id_column=values["patch_id_column"],
        island_column=values["island_column"],
        atoll_column=values["atoll_column"],
        atoll_name_fixes=values["atoll_name_fixes"] or {},
        opening_years=values["opening_years"],
        closing_years=values["closing_years"],
        output_level=values["output_level"],
        standardized_dir=values["standardized_dir"],
        raster_dir=values["raster_dir"],
        output_dir=values["output_dir"],
        rebuild_mode=values["rebuild_mode"],
        min_valid_share=values["min_valid_share"],
        fallback_percentile_low=values["fallback_percentile_low"],
        fallback_percentile_high=values["fallback_percentile_high"],
        fallback_min_sample=values["fallback_min_sample"],
        collinearity_report=values["collinearity_report"],
        collinearity_threshold=values["collinearity_threshold"],
        atolls_file=values["atolls_file"],
        atolls_name_column=values["atolls_name_column"],
        area_crs=values["area_crs"],
        atoll_buffer_km=values["atoll_buffer_km"],
        map_colormap=values["map_colormap"],
        table_dir=values["table_dir"] or "data/tables",
    )


def _convert(reader: _Reader, row: int | None, item: spec.ProjectKey, raw: Any) -> Any:
    sheet = spec.PROJECT_SHEET
    if item.kind in ("text", "path"):
        return str(raw)
    if item.kind == "years":
        return _parse_years(reader, row, item.key, raw)
    if item.kind == "pairs":
        return _parse_pairs(reader, row, item.key, raw)
    if item.kind == "bool":
        return reader.boolean(sheet, row, item.key, raw, default=True)
    if item.kind == "enum":
        enum_cls = {"output_level": OutputLevel, "rebuild_mode": RebuildMode}[item.key]
        return reader.enum(sheet, row, item.key, raw, enum_cls)
    number = reader.number(sheet, row, item.key, raw)
    if number is None:
        return None
    if item.kind == "int":
        if number != int(number):
            reader.error(sheet, row, f"'{item.key}' must be a whole number, found '{raw}'.")
            return None
        return int(number)
    return number


def _check_project_logic(reader: _Reader, v: dict[str, Any], found: dict[str, tuple[int, Any]]) -> None:
    sheet = spec.PROJECT_SHEET

    def row_of(key: str) -> int | None:
        return found[key][0] if key in found else None

    opening, closing = v.get("opening_years") or (), v.get("closing_years") or ()
    if opening and closing:
        if set(opening) & set(closing):
            reader.error(sheet, row_of("closing_years"), "Opening and closing years overlap. SEEA EA needs two different accounting periods.")
        elif min(closing) <= max(opening):
            reader.error(sheet, row_of("closing_years"), "Closing years must come after the opening years.")
    for key in ("min_valid_share", "collinearity_threshold"):
        value = v.get(key)
        if value is not None and not 0 <= value <= 1:
            reader.error(sheet, row_of(key), f"'{key}' must be between 0 and 1.")
    low, high = v.get("fallback_percentile_low"), v.get("fallback_percentile_high")
    if low is not None and high is not None:
        if not 0 <= low < high <= 100:
            reader.error(sheet, row_of("fallback_percentile_high"), "Percentiles must satisfy 0 <= low < high <= 100.")
    buffer_km = v.get("atoll_buffer_km")
    if buffer_km is not None and buffer_km <= 0:
        reader.error(sheet, row_of("atoll_buffer_km"), "'atoll_buffer_km' must be greater than 0.")
    sample = v.get("fallback_min_sample")
    if sample is not None and sample < 2:
        reader.error(sheet, row_of("fallback_min_sample"), "'fallback_min_sample' must be at least 2.")


# ------------------------------------------------------------ Indicators sheet

def _read_anchors(workbook, reader: _Reader) -> dict[str, list[tuple[int, Anchor]]]:
    rows = _read_table(workbook, spec.ANCHORS_SHEET, spec.ANCHOR_COLUMNS, reader, spec.ANCHOR_COLUMNS)
    grouped: dict[str, list[tuple[int, Anchor]]] = defaultdict(list)
    for number, record in rows:
        indicator_id = record.get("id")
        if indicator_id is None:
            reader.error(spec.ANCHORS_SHEET, number, "The id is empty.")
            continue
        value = reader.number(spec.ANCHORS_SHEET, number, "value", record.get("value"))
        score = reader.number(spec.ANCHORS_SHEET, number, "score", record.get("score"))
        if value is None or score is None:
            if record.get("value") is None or record.get("score") is None:
                reader.error(spec.ANCHORS_SHEET, number, "Both 'value' and 'score' are required.")
            continue
        if not 0 <= score <= 1:
            reader.error(spec.ANCHORS_SHEET, number, f"The score must be between 0 and 1, found {score}.")
            continue
        grouped[str(indicator_id)].append((number, Anchor(value, score)))
    return grouped


def _read_indicators(workbook, reader: _Reader,
                     anchors: dict[str, list[tuple[int, Anchor]]]) -> list[Indicator]:
    sheet = spec.INDICATORS_SHEET
    rows = _read_table(workbook, sheet, spec.INDICATOR_COLUMNS, reader, spec.INDICATOR_REQUIRED)
    indicators: list[Indicator] = []
    seen: dict[str, int] = {}
    for number, rec in rows:
        indicator_id = rec.get("id")
        if indicator_id is None or not ID_PATTERN.match(str(indicator_id)):
            reader.error(sheet, number, f"'id' must start with a letter and contain only letters, digits and underscores, found '{indicator_id}'.")
            continue
        indicator_id = str(indicator_id)
        if indicator_id.lower() in seen:
            reader.error(sheet, number, f"Duplicate id '{indicator_id}' (first in row {seen[indicator_id.lower()]}).")
            continue
        seen[indicator_id.lower()] = number
        indicator = _build_indicator(reader, number, rec, indicator_id, anchors.get(indicator_id, []))
        if indicator is not None:
            indicators.append(indicator)
    for indicator_id, items in anchors.items():
        if indicator_id.lower() not in seen:
            reader.error(spec.ANCHORS_SHEET, items[0][0], f"Anchors refer to '{indicator_id}', which is not in the Indicators sheet.")
    return indicators


def _build_indicator(reader: _Reader, row: int, rec: dict[str, Any], indicator_id: str,
                     anchor_rows: list[tuple[int, Anchor]]) -> Indicator | None:
    sheet = spec.INDICATORS_SHEET
    before = len(reader.issues)

    name = rec.get("name")
    if name is None:
        reader.error(sheet, row, f"'{indicator_id}': 'name' is required.")
    ect_class = reader.enum(sheet, row, "ect_class", rec.get("ect_class"), EctClass)
    if rec.get("ect_class") is None:
        reader.error(sheet, row, f"'{indicator_id}': 'ect_class' is required.")
    level = reader.enum(sheet, row, "level", rec.get("level"), Level)
    direction = reader.enum(sheet, row, "direction", rec.get("direction"), Direction)
    numbers = {
        col: reader.number(sheet, row, col, rec.get(col))
        for col in ("ref_lower", "ref_upper", "optimum_lower", "optimum_upper")
    }
    in_index = reader.boolean(sheet, row, "in_index", rec.get("in_index"), default=True)
    weight = reader.number(sheet, row, "weight", rec.get("weight"))
    if weight is None:
        weight = 1.0
    elif weight <= 0:
        reader.error(sheet, row, f"'{indicator_id}': 'weight' must be greater than 0.")

    raster_file = rec.get("raster_file")
    if raster_file is not None:
        raster_path = Path(str(raster_file))
        if raster_path.is_absolute() or str(raster_file).startswith(("/", "\\")) or ".." in raster_path.parts:
            reader.error(sheet, row, f"'{indicator_id}': 'raster_file' must be a file name inside the raster folder, not a full path.")
        if level is None and rec.get("level") is None:
            reader.error(sheet, row, f"'{indicator_id}': 'level' is required when 'raster_file' is filled.")

    opening = _indicator_years(reader, row, indicator_id, "opening_years", rec.get("opening_years"))
    closing = _indicator_years(reader, row, indicator_id, "closing_years", rec.get("closing_years"))
    _check_own_periods(reader, row, indicator_id, opening, closing)
    first_band_year = reader.number(sheet, row, "first_band_year", rec.get("first_band_year"))
    if first_band_year is not None and (first_band_year != int(first_band_year) or not 1900 <= first_band_year <= 2100):
        reader.error(sheet, row, f"'{indicator_id}': 'first_band_year' must be a year, found {first_band_year:g}.")
        first_band_year = None

    components = tuple(p.strip() for p in str(rec.get("components") or "").replace(",", ";").split(";") if p.strip())
    for part in components:
        if not ID_PATTERN.match(part):
            reader.error(sheet, row, f"'{indicator_id}': 'components' has an invalid id '{part}'.")
    if indicator_id in components:
        reader.error(sheet, row, f"'{indicator_id}': an indicator cannot be a component of itself.")
    if components and raster_file is not None:
        reader.error(sheet, row, f"'{indicator_id}': a composite indicator (components filled) cannot have a raster_file.")
    island_map = reader.boolean(sheet, row, "island_map", rec.get("island_map"), default=False)
    if island_map and level is Level.ATOLL:
        reader.warn(sheet, row, f"'{indicator_id}': island_map is ignored for atoll level indicators.")

    _check_direction(reader, row, indicator_id, direction, numbers, anchor_rows, rec)

    if len(reader.issues) > before and any(i.severity == "error" for i in reader.issues[before:]):
        return None
    return Indicator(
        id=indicator_id,
        name=str(name),
        ect_class=ect_class,
        unit=None if rec.get("unit") is None else str(rec["unit"]),
        raster_file=None if raster_file is None else str(raster_file),
        level=level,
        direction=direction,
        ref_lower=numbers["ref_lower"],
        ref_upper=numbers["ref_upper"],
        optimum_lower=numbers["optimum_lower"],
        optimum_upper=numbers["optimum_upper"],
        in_index=in_index,
        weight=weight,
        anchors=tuple(a for _, a in sorted(anchor_rows, key=lambda item: item[1].value)),
        opening_years=opening or None,
        closing_years=closing or None,
        first_band_year=None if first_band_year is None else int(first_band_year),
        components=components,
        island_map=island_map,
    )


def _indicator_years(reader: _Reader, row: int, indicator_id: str, column: str, value: Any) -> tuple[int, ...]:
    value = _clean(value)
    if value is None:
        return ()
    return _parse_years(reader, row, f"{indicator_id}.{column}", value, spec.INDICATORS_SHEET)


def _check_own_periods(reader: _Reader, row: int, indicator_id: str,
                       opening: tuple[int, ...], closing: tuple[int, ...]) -> None:
    sheet = spec.INDICATORS_SHEET
    if not opening and not closing:
        return
    if bool(opening) != bool(closing):
        reader.error(sheet, row, f"'{indicator_id}': fill both 'opening_years' and 'closing_years', or leave both empty.")
        return
    if set(opening) & set(closing):
        reader.error(sheet, row, f"'{indicator_id}': opening and closing years overlap.")
    elif min(closing) <= max(opening):
        reader.error(sheet, row, f"'{indicator_id}': closing years must come after the opening years.")
    else:
        reader.warn(sheet, row, f"'{indicator_id}' uses its own periods (opening {list(opening)}, closing {list(closing)}). An index that mixes periods is not directly comparable across indicators.")


def _check_direction(reader: _Reader, row: int, indicator_id: str, direction: Direction | None,
                     n: dict[str, float | None], anchor_rows: list[tuple[int, Anchor]],
                     rec: dict[str, Any]) -> None:
    sheet = spec.INDICATORS_SHEET
    filled = {k: v for k, v in n.items() if v is not None}
    label = f"'{indicator_id}'"

    if direction is None:
        if filled:
            reader.error(sheet, row, f"{label}: reference levels are filled but 'direction' is empty.")
        if anchor_rows:
            reader.error(sheet, anchor_rows[0][0], f"{label}: anchors are given but 'direction' is not custom.")
        if rec.get("raster_file") is not None:
            reader.warn(sheet, row, f"{label}: no direction, so no score will be computed.")
        return

    if direction is not Direction.CUSTOM and anchor_rows:
        reader.error(sheet, anchor_rows[0][0], f"{label}: anchors are only used when direction is custom (direction is {direction.value}).")

    if direction in (Direction.STANDARD, Direction.INVERSE):
        for col in ("optimum_lower", "optimum_upper"):
            if n[col] is not None:
                reader.warn(sheet, row, f"{label}: '{col}' is only used by two_sided and will be ignored.")
        low, high = n["ref_lower"], n["ref_upper"]
        if (low is None) != (high is None):
            reader.error(sheet, row, f"{label}: fill both 'ref_lower' and 'ref_upper', or leave both empty to derive them from the data.")
        elif low is not None and not low < high:
            reader.error(sheet, row, f"{label}: 'ref_lower' ({low:g}) must be smaller than 'ref_upper' ({high:g}).")
        elif low is None:
            reader.warn(sheet, row, f"{label}: no reference levels, they will be derived from the data (relative scores, not an ecological reference).")
        return

    if direction is Direction.TWO_SIDED:
        order = [n["ref_lower"], n["optimum_lower"], n["optimum_upper"], n["ref_upper"]]
        if all(v is None for v in order):
            reader.warn(sheet, row, f"{label}: two_sided needs ref_lower, optimum_lower, optimum_upper and ref_upper. They cannot be derived from the data, so no score will be computed until they are given.")
        elif any(v is None for v in order):
            reader.error(sheet, row, f"{label}: two_sided needs all four levels (ref_lower, optimum_lower, optimum_upper, ref_upper).")
        elif not (order[0] < order[1] <= order[2] < order[3]):
            reader.error(sheet, row, f"{label}: the levels must satisfy ref_lower < optimum_lower <= optimum_upper < ref_upper, found {order}.")
        return

    # custom
    for col in ("ref_lower", "ref_upper", "optimum_lower", "optimum_upper"):
        if n[col] is not None:
            reader.warn(sheet, row, f"{label}: '{col}' is ignored when direction is custom. Use the Anchors sheet.")
    if len(anchor_rows) < 2:
        reader.error(sheet, row, f"{label}: direction is custom, so the Anchors sheet needs at least two rows for this id.")
        return
    values = [a.value for _, a in anchor_rows]
    if len(set(values)) != len(values):
        reader.error(sheet, anchor_rows[0][0], f"{label}: the anchors repeat a value. Use one score per value.")


# ------------------------------------------------------- Tables and ECV sheets

def _optional_sheet(workbook, sheet: str, columns: tuple[str, ...], required: tuple[str, ...],
                    reader: _Reader) -> list[tuple[int, dict[str, Any]]]:
    """Rows of an optional sheet. A missing sheet is not an error."""
    if sheet not in workbook.sheetnames:
        return []
    return _read_table(workbook, sheet, columns, reader, required)


def _text(rec: dict[str, Any], key: str) -> str | None:
    value = rec.get(key)
    return None if value is None else str(value)


def _read_tables(workbook, reader: _Reader) -> list[TableSource]:
    sheet = spec.TABLES_SHEET
    items, seen = [], set()
    for number, rec in _optional_sheet(workbook, sheet, spec.TABLE_COLUMNS, spec.TABLE_REQUIRED, reader):
        missing = [c for c in spec.TABLE_REQUIRED if rec.get(c) is None]
        if missing:
            reader.error(sheet, number, f"Missing value(s): {', '.join(missing)}.")
            continue
        if (rec.get("filter_column") is None) != (rec.get("filter_value") is None):
            reader.error(sheet, number, "Fill both 'filter_column' and 'filter_value', or neither.")
            continue
        key = str(rec["id"])
        if key in seen:
            reader.error(sheet, number, f"Duplicate id '{key}'.")
            continue
        seen.add(key)
        items.append(TableSource(key, str(rec["file"]), _text(rec, "sheet"), str(rec["unit_column"]),
                                 str(rec["year_column"]), str(rec["value_column"]),
                                 _text(rec, "filter_column"), _text(rec, "filter_value")))
    return items


def _read_ecvs(workbook, reader: _Reader) -> list[EcvSpec]:
    sheet = spec.ECV_SHEET
    items, seen = [], set()
    for number, rec in _optional_sheet(workbook, sheet, spec.ECV_COLUMNS, spec.ECV_REQUIRED, reader):
        missing = [c for c in spec.ECV_REQUIRED if rec.get(c) is None]
        if missing:
            reader.error(sheet, number, f"Missing value(s): {', '.join(missing)}.")
            continue
        key = str(rec["id"])
        if not ID_PATTERN.match(key) or key in seen:
            reader.error(sheet, number, f"'id' must be unique, start with a letter and use letters, digits and underscores, found '{key}'.")
            continue
        seen.add(key)
        aggregation = str(rec.get("aggregation") or "mean").lower()
        if aggregation not in ("mean", "sum", "none"):
            reader.error(sheet, number, f"'aggregation' must be mean, sum or none, found '{aggregation}'.")
            continue
        months = reader.number(sheet, number, "min_months", rec.get("min_months"))
        months = 1 if months is None else int(months)
        if not 1 <= months <= 12:
            reader.error(sheet, number, "'min_months' must be between 1 and 12.")
            continue
        excluded = _indicator_years(reader, number, key, "exclude_years", rec.get("exclude_years"))
        items.append(EcvSpec(key, str(rec["name"]), _text(rec, "unit"), str(rec["file"]), str(rec["sheet"]),
                             str(rec["unit_column"]), str(rec["time_column"]), str(rec["value_column"]),
                             aggregation, months, excluded))
    return items


# --------------------------------------------------------------- cross checks

def _cross_checks(reader: _Reader, indicators: list[Indicator], tables: list[TableSource]) -> None:
    sheet = spec.INDICATORS_SHEET
    ids = {i.id for i in indicators}
    fed = {t.id for t in tables}
    active = [i for i in indicators if i.raster_file or i.components or i.id in fed]
    for item in indicators:
        for part in item.components:
            if part not in ids:
                reader.error(sheet, None, f"'{item.id}': component '{part}' is not in the Indicators sheet.")
            elif part in {c for other in indicators for c in other.components if other.id == part}:
                reader.error(sheet, None, f"'{item.id}': component '{part}' is itself a composite. Only one level is supported.")
        if item.components and (item.level is None or item.direction is None):
            reader.error(sheet, None, f"'{item.id}': a composite indicator needs 'level' and 'direction' (and reference levels).")
    for table in tables:
        if table.id not in ids:
            reader.error(spec.TABLES_SHEET, None, f"Tables refers to '{table.id}', which is not in the Indicators sheet.")
    for item in indicators:
        if not (item.raster_file or item.components or item.id in fed):
            reader.warn(sheet, None, f"'{item.id}' has no raster_file or table: placeholder, skipped unless a standardized table named {item.id}.csv exists.")
    in_index = [i for i in active if i.in_index and i.direction is not None]
    if not in_index:
        reader.warn(sheet, None, "No active indicator with a direction is in the index. Nothing can be aggregated yet.")
    weights = {i.weight for i in indicators if i.in_index}
    if len(weights) > 1:
        reader.warn(sheet, None, "Weights are not all equal. The team agreed on equal weights; SEEA EA asks for a rationale for any other choice.")
    covered = {i.ect_class for i in indicators if i.in_index and i.ect_class is not None}
    missing = [c.value for c in EctClass if c not in covered]
    if missing:
        reader.warn(sheet, None, f"No indicator in the index for ECT class(es): {', '.join(missing)}. SEEA EA recommends covering all classes where possible.")


def _check_files(reader: _Reader, indicators: list[Indicator], project: ProjectSettings,
                 base_dir: Path, tables: list[TableSource], ecvs: list[EcvSpec]) -> None:
    units = base_dir / project.units_file
    if not units.exists():
        reader.error(spec.PROJECT_SHEET, None, f"Units file not found: {units}")
    raster_dir = base_dir / project.raster_dir
    table_dir = base_dir / project.standardized_dir
    atolls = base_dir / project.atolls_file
    if not atolls.exists():
        reader.error(spec.PROJECT_SHEET, None, f"Atolls file not found: {atolls}")
    folder = base_dir / project.table_dir
    for source in [*tables, *ecvs]:
        if not (folder / source.file).exists():
            reader.error(spec.TABLES_SHEET if isinstance(source, TableSource) else spec.ECV_SHEET, None,
                         f"'{source.id}': file not found: {folder / source.file}")
    for item in indicators:
        has_table = (table_dir / f"{item.id}.csv").exists() or any(t.id == item.id for t in tables)
        if item.raster_file and not (raster_dir / item.raster_file).exists() and not has_table:
            reader.error(spec.INDICATORS_SHEET, None, f"'{item.id}': raster not found: {raster_dir / item.raster_file}")


# ------------------------------------------------------------------ public API

def default_base_dir(config_path: Path) -> Path:
    """Project folder: the parent of a folder called 'config', otherwise the config folder itself."""
    folder = Path(config_path).resolve().parent
    return folder.parent if folder.name.lower() == "config" else folder


def validate_config(path: str | Path, base_dir: str | Path | None = None,
                    check_files: bool = False) -> tuple[Config | None, list[Issue]]:
    """Read and validate the workbook. Returns (config or None if errors, all issues)."""
    path = Path(path)
    reader = _Reader()
    if not path.exists():
        reader.error(path.name, None, f"File not found: {path}")
        return None, reader.issues
    try:
        workbook = load_workbook(path, data_only=True)
    except Exception as exc:  # unreadable or not an xlsx file
        reader.error(path.name, None, f"The file cannot be opened as an Excel workbook: {exc}")
        return None, reader.issues

    project = _read_project(workbook, reader)
    anchors = _read_anchors(workbook, reader)
    indicators = _read_indicators(workbook, reader, anchors)
    tables = _read_tables(workbook, reader)
    ecvs = _read_ecvs(workbook, reader)
    _cross_checks(reader, indicators, tables)
    if check_files and project is not None:
        root = Path(base_dir) if base_dir is not None else default_base_dir(path)
        _check_files(reader, indicators, project, root, tables, ecvs)
    workbook.close()

    if any(i.severity == "error" for i in reader.issues) or project is None:
        return None, reader.issues
    config = Config(
        indicators=tuple(indicators),
        project=project,
        fingerprint=fingerprint(path),
        warnings=tuple(i for i in reader.issues if i.severity == "warning"),
        tables=tuple(tables),
        ecvs=tuple(ecvs),
    )
    return config, reader.issues


def load_config(path: str | Path, base_dir: str | Path | None = None,
                check_files: bool = False) -> Config:
    """Return the validated configuration or raise ConfigError listing every problem."""
    config, issues = validate_config(path, base_dir, check_files)
    if config is None:
        raise ConfigError(issues)
    return config
