"""Writes the results workbook, the check tables, the GIS layers (shapefiles) and the run record."""

from __future__ import annotations

import json
import platform
import re
import sys
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from importlib import metadata
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from . import __version__
from .account import Accounts, IndicatorInput
from .schema import Config
from .standardized import TableInfo
from .units import Units

GIS_CRS = "EPSG:4326"
PERIOD_NOTE = {"open": "opening", "close": "closing"}


def _years_text(years: tuple[int, ...]) -> str:
    return ";".join(str(y) for y in years)


def references_table(config: Config, inputs: list[IndicatorInput], infos: dict[str, TableInfo]) -> pd.DataFrame:
    rows = []
    by_id = {inp.indicator.id: inp for inp in inputs}
    for item in config.indicators:
        inp = by_id.get(item.id)
        row = {
            "indicator_id": item.id, "name": item.name, "ect_class": item.ect_class.value, "unit": item.unit,
            "level": item.level.value if item.level else None,
            "direction": item.direction.value if item.direction else None,
            "in_index": item.in_index, "weight": item.weight,
        }
        if inp is None:
            row.update(status="placeholder (no data)", reference_source=None, reference_note=None)
        else:
            ref = inp.reference
            anchors = ";".join(f"{a.value:.6g}:{a.score:g}" for a in ref.anchors) if ref.anchors else None
            row.update(
                status="scored" if ref.has_scale else "variable only (no score)",
                reference_source=ref.source, anchors_value_score=anchors, reference_note=ref.note,
                n_values_for_reference=ref.n_values,
                opening_years=_years_text(inp.opening_years), closing_years=_years_text(inp.closing_years),
                table_origin=infos[item.id].origin, table_note=infos[item.id].reason,
            )
        rows.append(row)
    return pd.DataFrame(rows)


LEVEL_COLUMNS = {
    "atoll": ["atoll"],
    "island": ["atoll", "island"],
    "patch": ["atoll", "island", "patch_id"],
}
VALUE_COLUMNS = [
    "indicator_id", "unit", "direction", "reference_lower", "reference_upper", "source_level",
    "opening_year", "opening_value", "opening_score", "closing_year", "closing_value", "closing_score",
    "change_score",
]
INDEX_COLUMNS = [
    "level", "atoll", "island", "patch_id", "opening_index", "closing_index", "change_index",
    "n_indicators_opening", "n_indicators_closing", "indicators_used_closing", "partial", "mixed_periods",
]


def _unit_labels(accounts: Accounts) -> pd.DataFrame:
    """Atoll, island and patch id of every unit, taken from the unit names table."""
    labels = accounts.unit_names.copy()
    labels["patch_id"] = labels["unit_id"].where(labels["level"] == "patch")
    labels["island"] = labels["island"].where(labels["level"] != "atoll")
    labels["atoll"] = labels["atoll"].where(labels["level"] != "atoll", labels["unit_name"])
    labels["island"] = labels["island"].where(labels["level"] != "island", labels["unit_name"])
    return labels[["level", "unit_id", "atoll", "island", "patch_id"]]


def results_tables(accounts: Accounts, inputs: list[IndicatorInput]) -> dict[str, pd.DataFrame]:
    """One table per reporting level (one row per unit and indicator) plus the composite index."""
    meta = pd.DataFrame([{
        "indicator_id": i.indicator.id, "unit": i.indicator.unit,
        "direction": i.indicator.direction.value if i.indicator.direction else None,
        "reference_lower": i.reference.anchors[0].value if i.reference.has_scale else np.nan,
        "reference_upper": i.reference.anchors[-1].value if i.reference.has_scale else np.nan,
        "opening_year": _years_text(i.opening_years), "closing_year": _years_text(i.closing_years),
        "order": order,
    } for order, i in enumerate(inputs)])
    labels = _unit_labels(accounts)
    long = accounts.long.rename(columns={
        "v_open": "opening_value", "v_close": "closing_value", "s_open": "opening_score",
        "s_close": "closing_score", "s_change": "change_score"})
    frame = long.merge(meta, on="indicator_id").merge(labels, on=["level", "unit_id"], how="left")
    tables = {}
    for level, keys in LEVEL_COLUMNS.items():
        part = frame[frame["level"] == level].sort_values([*keys, "order"])
        tables[level] = part[[*keys, *VALUE_COLUMNS]].reset_index(drop=True)

    index = accounts.index.rename(columns={
        "index_open": "opening_index", "index_close": "closing_index", "index_change": "change_index",
        "n_used_open": "n_indicators_opening", "n_used_close": "n_indicators_closing",
        "used_close": "indicators_used_closing"})
    index = index.drop(columns=["atoll", "island"]).merge(labels, on=["level", "unit_id"], how="left")
    level_order = {"atoll": 0, "island": 1, "patch": 2}
    index = index.assign(_order=index["level"].map(level_order)).sort_values(["_order", "atoll", "island", "patch_id"])
    tables["index"] = index[INDEX_COLUMNS].reset_index(drop=True)
    return tables


def _notes_table(config: Config) -> pd.DataFrame:
    project = config.project
    rows = [
        ("Account", project.account_name),
        ("Ecosystem type", project.ecosystem_type),
        ("atoll, island, patch", "One sheet per reporting level. Island and atoll results are area weighted means of the patch results."),
        ("index", "Composite index of the indicator scores (0 worst, 1 best), for all three levels."),
        ("Scores", "0 (worst) to 1 (best), from the reference levels shown in the same row. Empty: no score available."),
        ("source_level", "mangrove: computed on the mangrove patches. atoll: computed for the whole atoll and repeated in its islands and patches."),
        ("opening_year, closing_year", "Year or years averaged for each period."),
        ("change_score", "Closing score minus opening score."),
        ("partial", "1 if an eligible indicator is missing in the opening or the closing period."),
        ("mixed_periods", "1 if the indicators of the index have different accounting periods."),
    ]
    return pd.DataFrame(rows, columns=["item", "description"])


def _style_sheet(sheet, frame: pd.DataFrame, filtered: bool = True, max_width: int = 28) -> None:
    """Bold shaded header, frozen first row, column widths and three decimals."""
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
    sheet.freeze_panes = "A2"
    if filtered:
        sheet.auto_filter.ref = sheet.dimensions
    for number, column in enumerate(frame.columns, start=1):
        width = max([len(str(column))] + [len(str(v)) for v in frame[column].head(200)]) + 2
        sheet.column_dimensions[get_column_letter(number)].width = min(width, max_width)
        if pd.api.types.is_float_dtype(frame[column]):
            for cell in sheet[get_column_letter(number)][1:]:
                cell.number_format = "0.000"


def write_results_workbook(out_dir: Path, config: Config, tables: dict[str, pd.DataFrame]) -> Path:
    """results.xlsx: the tables the team works with, one sheet per level plus the index and short notes."""
    path = Path(out_dir) / "results.xlsx"
    sheets = {**tables, "notes": _notes_table(config)}
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)
            _style_sheet(writer.sheets[name], frame, filtered=name != "notes", max_width=60 if name == "notes" else 28)
    return path


INDICATOR_SHEET_COLUMNS = ["atoll", *[c for c in VALUE_COLUMNS if c != "source_level"]]


def write_indicator_workbooks(out_dir: Path, inputs: list[IndicatorInput], atoll_table: pd.DataFrame) -> list[Path]:
    """One simple workbook per indicator: one row per atoll with mangroves, opening, closing and change."""
    folder = Path(out_dir) / "indicators"
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for inp in inputs:
        frame = atoll_table[atoll_table["indicator_id"] == inp.indicator.id][INDICATOR_SHEET_COLUMNS].reset_index(drop=True)
        path = folder / f"{inp.indicator.id}.xlsx"
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="atoll", index=False)
            _style_sheet(writer.sheets["atoll"], frame)
        written.append(path)
    return written


def write_tables(out_dir: Path, tables: dict[str, pd.DataFrame]) -> list[Path]:
    folder = Path(out_dir) / "checks"
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, frame in tables.items():
        path = folder / f"{name}.csv"
        frame.to_csv(path, index=False, float_format="%.6g")
        written.append(path)
    return written


# ----------------------------------------------------------------- shapefiles

def short_names(indicator_ids: list[str]) -> dict[str, str]:
    """Short unique field prefixes (up to 6 characters) for the shapefile columns."""
    used: set[str] = set()
    names = {}
    for indicator_id in indicator_ids:
        base = re.sub(r"[^A-Za-z0-9]", "", indicator_id.removeprefix("COND_"))[:6].upper() or "IND"
        name, n = base, 1
        while name in used:
            n += 1
            name = f"{base[:5]}{n}"
        used.add(name)
        names[indicator_id] = name
    return names


def _wide(level: str, accounts: Accounts, shorts: dict[str, str]) -> tuple[pd.DataFrame, list[dict]]:
    long = accounts.long[accounts.long["level"] == level]
    columns, dictionary = {}, []
    for indicator_id, short in shorts.items():
        sub = long[long["indicator_id"] == indicator_id].set_index("unit_id")
        for suffix, column, meaning in (
            ("vo", "v_open", "variable, opening period"), ("vc", "v_close", "variable, closing period"),
            ("so", "s_open", "score 0 to 1, opening period"), ("sc", "s_close", "score 0 to 1, closing period"),
            ("sd", "s_change", "score change (closing minus opening)"),
        ):
            field = f"{short}_{suffix}"
            columns[field] = sub[column]
            dictionary.append({"layer": level, "field": field, "indicator_id": indicator_id, "meaning": meaning})
    wide = pd.DataFrame(columns)
    index = accounts.index[accounts.index["level"] == level].set_index("unit_id")
    for field, column, meaning in (
        ("IDX_O", "index_open", "composite index, opening period"), ("IDX_C", "index_close", "composite index, closing period"),
        ("IDX_D", "index_change", "index change (closing minus opening, indicators present in both)"),
        ("IDX_N", "n_used_close", "number of indicators in the closing index"),
    ):
        wide[field] = index[column]
        dictionary.append({"layer": level, "field": field, "indicator_id": "", "meaning": meaning})
    wide["IDX_PART"] = index["partial"].astype(int)
    wide["IDX_MIX"] = index["mixed_periods"].astype(int)
    dictionary.append({"layer": level, "field": "IDX_PART", "indicator_id": "", "meaning": "1 if indicators of the index are missing (partial index)"})
    dictionary.append({"layer": level, "field": "IDX_MIX", "indicator_id": "", "meaning": "1 if the indicators used have different accounting periods"})
    wide["IDX_USED"] = index["used_close"].map(lambda text: ",".join(shorts.get(t, t) for t in text.split(";") if t))
    dictionary.append({"layer": level, "field": "IDX_USED", "indicator_id": "", "meaning": "indicators used in the closing index (short names)"})
    return wide, dictionary


def write_shapefiles(out_dir: Path, units: Units, accounts: Accounts, indicator_ids: list[str]) -> tuple[list[Path], pd.DataFrame]:
    """Patches, islands and atolls as shapefiles (EPSG:4326) with short field names and a dictionary."""
    folder = Path(out_dir) / "gis"
    folder.mkdir(parents=True, exist_ok=True)
    shorts = short_names(indicator_ids)
    dictionary = [{"layer": "all", "field": "unit_id", "indicator_id": "", "meaning": "unit identifier"},
                  {"layer": "all", "field": "name", "indicator_id": "", "meaning": "unit name"},
                  {"layer": "all", "field": "area_m2", "indicator_id": "", "meaning": "mangrove area (m2)"}]
    written = []

    def save(level: str, base: gpd.GeoDataFrame, key: str, name_column: str, filename: str, extra: dict) -> None:
        wide, fields = _wide(level, accounts, shorts)
        frame = base.copy()
        frame["unit_id"] = frame[key]
        frame["name"] = frame[name_column]
        keep = ["unit_id", "name", "area_m2", *extra, "geometry"]
        frame = frame[keep].merge(wide, left_on="unit_id", right_index=True, how="left")
        frame = gpd.GeoDataFrame(frame, geometry="geometry", crs=base.crs).to_crs(GIS_CRS)
        path = folder / filename
        frame.to_file(path, driver="ESRI Shapefile", encoding="utf-8")
        written.append(path)
        dictionary.extend(fields)

    patches = units.patches.assign(island_name=units.patches["island"])
    save("patch", patches, "patch_id", "patch_id", "patches.shp", {"island": None, "atoll": None})
    islands = units.islands.copy()
    save("island", islands, "island_id", "island", "islands.shp", {"atoll": None, "n_patches": None})
    atolls = units.atolls.copy()
    save("atoll", atolls, "atoll", "atoll", "atolls.shp", {"n_patches": None})
    table = pd.DataFrame(dictionary).drop_duplicates()
    table.to_csv(folder / "field_dictionary.csv", index=False)
    written.append(folder / "field_dictionary.csv")
    return written, table


# ----------------------------------------------------------------- run record

def _jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {k: _jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if np.isnan(value) else float(value)
    return value


def write_run_record(out_dir: Path, config: Config, infos: dict[str, TableInfo], notes: list[str],
                     files: list[Path], units: Units) -> Path:
    versions = {}
    for package in ("numpy", "pandas", "geopandas", "shapely", "rasterio", "matplotlib", "openpyxl", "pyogrio"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "not installed"
    record = {
        "software": {"name": "mangrove_condition", "version": __version__},
        "run_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "packages": versions,
        "configuration_file": config.fingerprint,
        "project_settings": config.project,
        "indicators": config.indicators,
        "standardized_tables": {k: {"origin": v.origin, "reason": v.reason, "path": v.path.name} for k, v in infos.items()},
        "units": {"patches": len(units.patches), "islands": len(units.islands), "atolls_in_study": int(units.atolls["has_mangrove"].sum())},
        "method": {
            "period_value": "mean of the yearly values of the period; years without a value are skipped, nothing is filled",
            "patch_value": "area weighted mean of the pixels (each pixel counts by the share covered by the patch); no resampling",
            "atoll_value": "mean of the pixels whose centre lies inside the atoll polygon enlarged by the buffer",
            "score": "piecewise linear rescaling to 0 (worst) and 1 (best); values beyond the reference levels are set to 0 or 1",
            "aggregation": "island and atoll values and scores are area weighted means of the patch results",
            "atoll_indicators": "repeated in every island and patch of the atoll",
            "index": "weighted mean of the available scores, equal weights unless set in the configuration",
        },
        "notes": notes,
        "output_files": [str(f.name) for f in files],
    }
    path = Path(out_dir) / "run_record.json"
    path.write_text(json.dumps(_jsonable(record), indent=2, ensure_ascii=False), encoding="utf-8")
    return path
