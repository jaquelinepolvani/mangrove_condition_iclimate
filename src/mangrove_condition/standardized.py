"""Standardized tables: one table per indicator, with a record of how it was made.

Table first: if a table exists it is used. A table built by this code has a small record
(sidecar file) with the fingerprint of the raster, the units file and the settings. When
the fingerprint no longer matches, the table is rebuilt (rebuild_mode auto). A table
supplied by the team has no record: it is used as it is and the report says so.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from . import __version__
from .provenance import file_sha256
from .schema import Config, Indicator, Level, RebuildMode
from .units import Units, expected_unit_ids, hash_vector_files
from .zonal import TABLE_COLUMNS, RasterError, atoll_table, band_years, patch_table

METHOD = "area_weighted_mean_v1"


class TableError(Exception):
    """Raised when a standardized table cannot be read or built."""


@dataclass(frozen=True)
class TableInfo:
    indicator_id: str
    origin: str   # built, reused or supplied
    reason: str
    path: Path


def table_path(config: Config, base_dir: Path, indicator_id: str) -> Path:
    return Path(base_dir) / config.project.standardized_dir / f"{indicator_id}.csv"


def meta_path(path: Path) -> Path:
    return path.with_suffix(".meta.json")


def _fingerprint(indicator: Indicator, config: Config, base_dir: Path, raster: Path) -> dict:
    project = config.project
    fingerprint = {
        "method": METHOD,
        "indicator_id": indicator.id,
        "level": indicator.level.value,
        "raster_file": raster.name,
        "raster_sha256": file_sha256(raster),
        "units_sha256": hash_vector_files(Path(base_dir) / project.units_file),
        "first_band_year": indicator.first_band_year,
        "area_crs": project.area_crs,
        "island_column": project.island_column,
        "atoll_column": project.atoll_column,
        "patch_id_column": project.patch_id_column,
        "atoll_name_fixes": dict(sorted(project.atoll_name_fixes.items())),
    }
    if indicator.level is Level.ATOLL:
        fingerprint["atolls_sha256"] = hash_vector_files(Path(base_dir) / project.atolls_file)
        fingerprint["atolls_name_column"] = project.atolls_name_column
        fingerprint["atoll_buffer_km"] = project.atoll_buffer_km
    return fingerprint


def _read_table(path: Path, indicator: Indicator, units: Units) -> pd.DataFrame:
    try:
        table = pd.read_csv(path)
    except Exception as exc:
        raise TableError(f"{path.name}: cannot be read as a table ({exc}).") from exc
    missing = [c for c in ("unit_id", "year", "value") if c not in table.columns]
    if missing:
        raise TableError(f"{path.name}: missing column(s) {', '.join(missing)}. Required: unit_id, year, value (valid_share and n_pixels are optional).")
    for column in ("valid_share", "n_pixels"):
        if column not in table.columns:
            table[column] = float("nan") if column == "n_pixels" else 1.0
    table = table[TABLE_COLUMNS].copy()
    table["unit_id"] = table["unit_id"].astype(str).str.strip()
    for column in ("year", "value", "valid_share", "n_pixels"):
        table[column] = pd.to_numeric(table[column], errors="coerce")
    if table["year"].isna().any():
        raise TableError(f"{path.name}: some years are not numbers.")
    table["year"] = table["year"].astype(int)
    if table.duplicated(["unit_id", "year"]).any():
        raise TableError(f"{path.name}: repeated rows for the same unit and year.")
    known = set(expected_unit_ids(units, indicator.level.value))
    unknown = sorted(set(table["unit_id"]) - known)
    if unknown:
        raise TableError(f"{path.name}: unit ids not found among the {indicator.level.value} units: {', '.join(unknown[:8])}{' ...' if len(unknown) > 8 else ''}.")
    return table


def load_or_build(indicator: Indicator, config: Config, base_dir: Path, units: Units,
                  log=print) -> tuple[pd.DataFrame, TableInfo]:
    """Return the standardized table of an indicator, building it from the raster when needed."""
    base_dir = Path(base_dir)
    project = config.project
    if indicator.level is None:
        raise TableError(f"{indicator.id}: 'level' is required (mangrove or atoll).")
    path = table_path(config, base_dir, indicator.id)
    meta = meta_path(path)
    raster = (base_dir / project.raster_dir / indicator.raster_file) if indicator.raster_file else None
    raster_ok = raster is not None and raster.exists()
    mode = project.rebuild_mode

    if path.exists() and not meta.exists() and mode is not RebuildMode.ALWAYS:
        return _read_table(path, indicator, units), TableInfo(
            indicator.id, "supplied", "Table supplied without a record: used as it is, freshness cannot be checked.", path)

    reason = ""
    if mode is RebuildMode.ALWAYS and raster_ok:
        reason = "rebuild_mode is always"
    elif not path.exists():
        reason = "no table yet"
    elif mode is RebuildMode.AUTO and raster_ok:
        try:
            stored = json.loads(meta.read_text(encoding="utf-8")).get("fingerprint", {})
        except (OSError, ValueError):
            stored = {}
        if stored != _fingerprint(indicator, config, base_dir, raster):
            reason = "the raster, the units file or the settings changed"

    if not reason:
        return _read_table(path, indicator, units), TableInfo(indicator.id, "reused", "Table is up to date.", path)

    if mode is RebuildMode.NEVER:
        raise TableError(f"{indicator.id}: no standardized table at {path} and rebuild_mode is never.")
    if not raster_ok:
        raise TableError(f"{indicator.id}: cannot build the table, raster not found: {raster}")

    log(f"  building table for {indicator.id} from {raster.name} ({reason})")
    try:
        years = band_years(raster, indicator.first_band_year)
        if indicator.level is Level.MANGROVE:
            table = patch_table(raster, units.patches, years)
        else:
            studied = units.atolls[units.atolls["has_mangrove"]]
            table = atoll_table(raster, studied, years, project.atoll_buffer_km)
    except RasterError as exc:
        raise TableError(str(exc)) from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(path, index=False, float_format="%.6g")
    meta.write_text(json.dumps({
        "fingerprint": _fingerprint(indicator, config, base_dir, raster),
        "package_version": __version__,
        "created_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "years": years,
    }, indent=2, sort_keys=True), encoding="utf-8")
    return table, TableInfo(indicator.id, "built", reason, path)
