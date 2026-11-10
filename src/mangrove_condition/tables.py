"""Spreadsheets and CSV files turned into standardized tables (one row per atoll and year)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from . import __version__
from .provenance import file_sha256
from .schema import Config, EcvSpec, TableSource
from .standardized import meta_path, table_path
from .units import Units, study_atoll_names


class SourceError(Exception):
    """Raised when a spreadsheet cannot be read or does not have the expected columns."""


def read_source(path: Path, sheet: str | None, columns: list[str]) -> pd.DataFrame:
    """Read an Excel sheet or a CSV file and check that the needed columns exist."""
    path = Path(path)
    try:
        if path.suffix.lower() in (".xlsx", ".xlsm", ".xls"):
            frame = pd.read_excel(path, sheet_name=sheet or 0)
        else:
            frame = pd.read_csv(path, encoding="utf-8-sig")
    except Exception as exc:
        raise SourceError(f"{path.name}: cannot be read ({exc}).") from exc
    frame.columns = [str(c).strip() for c in frame.columns]
    missing = [c for c in columns if c not in frame.columns]
    if missing:
        raise SourceError(f"{path.name}{f' (sheet {sheet})' if sheet else ''}: missing column(s) {', '.join(missing)}. "
                          f"Found: {', '.join(frame.columns)}.")
    return frame


def _fix_names(names: pd.Series, fixes: dict[str, str]) -> pd.Series:
    names = names.astype(str).str.strip()
    return names.replace(fixes)


def build_condition_tables(config: Config, base_dir: Path, units: Units, log=print) -> list[str]:
    """Write data/standardized/<id>.csv for every row of the Tables sheet. Returns the ids written."""
    written = []
    study = set(study_atoll_names(units))
    folder = Path(base_dir) / config.project.table_dir
    for source in config.tables:
        columns = [source.unit_column, source.year_column, source.value_column]
        if source.filter_column:
            columns.append(source.filter_column)
        path = folder / source.file
        frame = read_source(path, source.sheet, columns)
        if source.filter_column:
            frame = frame[frame[source.filter_column].astype(str).str.strip() == str(source.filter_value)]
        table = pd.DataFrame({
            "unit_id": _fix_names(frame[source.unit_column], config.project.atoll_name_fixes),
            "year": pd.to_numeric(frame[source.year_column], errors="coerce"),
            "value": pd.to_numeric(frame[source.value_column], errors="coerce"),
        }).dropna(subset=["year"])
        table["year"] = table["year"].astype(int)
        outside = sorted(set(table["unit_id"]) - study)
        table = table[table["unit_id"].isin(study)]
        if table.empty:
            raise SourceError(f"{source.id}: no rows left for the atolls with mangroves in {path.name}.")
        table["valid_share"] = 1.0
        table["n_pixels"] = float("nan")
        target = table_path(config, base_dir, source.id)
        target.parent.mkdir(parents=True, exist_ok=True)
        table.sort_values(["unit_id", "year"]).to_csv(target, index=False, float_format="%.6g")
        meta_path(target).write_text(json.dumps({
            "fingerprint": {"source_file": path.name, "source_sha256": file_sha256(path), "sheet": source.sheet,
                            "filter": [source.filter_column, source.filter_value]},
            "package_version": __version__,
            "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, indent=2, sort_keys=True), encoding="utf-8")
        note = f", {len(outside)} atoll(s) without mangroves left out" if outside else ""
        log(f"  {source.id}: table from {path.name}{note}")
        written.append(source.id)
    return written


def ecv_annual(item: EcvSpec, config: Config, base_dir: Path, atolls: list[str]) -> pd.DataFrame:
    """Annual value of an ECV for every atoll: unit, year, value, n_months, excluded."""
    path = Path(base_dir) / config.project.table_dir / item.file
    frame = read_source(path, item.sheet, [item.unit_column, item.time_column, item.value_column])
    names = _fix_names(frame[item.unit_column], config.project.atoll_name_fixes)
    values = pd.to_numeric(frame[item.value_column], errors="coerce")
    if item.aggregation == "none":
        years = pd.to_numeric(frame[item.time_column], errors="coerce")
        table = pd.DataFrame({"unit": names, "year": years, "value": values, "n_months": float("nan")})
    else:
        years = pd.to_numeric(frame[item.time_column].astype(str).str.slice(0, 4), errors="coerce")
        raw = pd.DataFrame({"unit": names, "year": years, "value": values}).dropna(subset=["year", "value"])
        grouped = raw.groupby(["unit", "year"])["value"]
        table = grouped.agg("sum" if item.aggregation == "sum" else "mean").to_frame("value")
        table["n_months"] = grouped.count()
        table = table.reset_index()
        table.loc[table["n_months"] < item.min_months, "value"] = float("nan")
    table = table.dropna(subset=["year"])
    table = table[table["unit"].isin(atolls)].copy()
    table["year"] = table["year"].astype(int)
    table["excluded"] = table["year"].isin(item.exclude_years)
    return table.sort_values(["unit", "year"]).reset_index(drop=True)
