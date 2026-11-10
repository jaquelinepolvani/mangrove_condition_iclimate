"""Spatial units: mangrove patches, islands and atolls."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd

from .schema import ProjectSettings


class UnitsError(Exception):
    """Raised when the units or atolls file cannot be used."""


@dataclass(frozen=True)
class Units:
    """Patches (finest unit), islands (points) and atolls (polygons), all in the area CRS."""

    patches: gpd.GeoDataFrame  # patch_id, island_id, island, atoll, area_m2, geometry
    islands: gpd.GeoDataFrame  # island_id, island, atoll, area_m2, n_patches, geometry (point)
    atolls: gpd.GeoDataFrame   # atoll, area_m2 (mangrove), n_patches, has_mangrove, geometry
    notes: tuple[str, ...]
    checks: pd.DataFrame       # per patch: distance to its labelled atoll and nearest atoll


def _key(name: str) -> str:
    return re.sub(r"\s+", " ", str(name)).strip().casefold()


def _read_vector(path: Path, label: str) -> gpd.GeoDataFrame:
    if not path.exists():
        raise UnitsError(f"{label} not found: {path}")
    frame = gpd.read_file(path)
    if frame.crs is None:
        raise UnitsError(f"{label} has no coordinate system: {path}")
    if frame.geometry.isna().any() or frame.geometry.is_empty.any():
        raise UnitsError(f"{label} has empty geometries: {path}")
    return frame


def read_units(project: ProjectSettings, base_dir: Path) -> Units:
    """Read the mangrove patches and the atolls and build the three unit layers."""
    base_dir = Path(base_dir)
    notes: list[str] = []
    raw = _read_vector(base_dir / project.units_file, "Units file")
    atolls_raw = _read_vector(base_dir / project.atolls_file, "Atolls file")

    for column in (project.island_column, project.atoll_column):
        if column not in raw.columns:
            raise UnitsError(f"Column '{column}' not found in the units file. Columns: {', '.join(raw.columns)}.")
    if project.atolls_name_column not in atolls_raw.columns:
        raise UnitsError(f"Column '{project.atolls_name_column}' not found in the atolls file. Columns: {', '.join(atolls_raw.columns)}.")

    patches = raw[[project.island_column, project.atoll_column, "geometry"]].copy()
    patches.columns = ["island", "atoll", "geometry"]
    patches["geometry"] = patches.geometry.make_valid()
    patches = patches.to_crs(project.area_crs)

    if patches["island"].isna().any() or patches["atoll"].isna().any():
        raise UnitsError("The units file has patches without an island or atoll name.")
    patches["island"] = patches["island"].astype(str).str.strip()
    patches["atoll"] = patches["atoll"].astype(str).str.strip().replace(project.atoll_name_fixes)

    atolls = atolls_raw[[project.atolls_name_column, "geometry"]].copy()
    atolls.columns = ["atoll", "geometry"]
    atolls["geometry"] = atolls.geometry.make_valid()
    atolls = atolls.to_crs(project.area_crs)
    atolls["atoll"] = atolls["atoll"].astype(str).str.strip()
    if atolls["atoll"].map(_key).duplicated().any():
        raise UnitsError("The atolls file has repeated atoll names.")

    canonical = {_key(name): name for name in atolls["atoll"]}
    unknown = sorted({name for name in patches["atoll"] if _key(name) not in canonical})
    if unknown:
        raise UnitsError(
            f"Atoll name(s) of the units file not found in the atolls file: {', '.join(unknown)}. "
            "Fix the spelling with 'atoll_name_fixes' in the Project sheet."
        )
    patches["atoll"] = patches["atoll"].map(lambda name: canonical[_key(name)])

    if project.patch_id_column:
        if project.patch_id_column not in raw.columns:
            raise UnitsError(f"Column '{project.patch_id_column}' (patch ids) not found in the units file.")
        ids = raw[project.patch_id_column].astype(str).str.strip()
        if ids.duplicated().any() or ids.isna().any():
            raise UnitsError(f"Column '{project.patch_id_column}' does not hold unique patch ids.")
        patches["patch_id"] = ids.to_numpy()
    else:
        patches["patch_id"] = [f"P{n:03d}" for n in range(1, len(patches) + 1)]
        notes.append(
            "The units file has no unique patch id: ids P001, P002, ... were generated in file order. "
            "Do not reorder the file, or ids change."
        )

    patches["island_id"] = patches["atoll"] + " / " + patches["island"]
    patches["area_m2"] = patches.geometry.area
    patches = patches[["patch_id", "island_id", "island", "atoll", "area_m2", "geometry"]].reset_index(drop=True)

    islands = []
    for island_id, group in patches.groupby("island_id", sort=True):
        islands.append({
            "island_id": island_id,
            "island": group["island"].iloc[0],
            "atoll": group["atoll"].iloc[0],
            "area_m2": group["area_m2"].sum(),
            "n_patches": len(group),
            "geometry": group.geometry.union_all().centroid,
        })
    islands = gpd.GeoDataFrame(islands, geometry="geometry", crs=patches.crs)

    summary = patches.groupby("atoll").agg(area_m2=("area_m2", "sum"), n_patches=("patch_id", "size")).reset_index()
    atolls = atolls.merge(summary, on="atoll", how="left")
    atolls["has_mangrove"] = atolls["n_patches"].notna()
    atolls["n_patches"] = atolls["n_patches"].fillna(0).astype(int)
    atolls["area_m2"] = atolls["area_m2"].fillna(0.0)
    checks = spatial_checks(patches, atolls)
    notes += check_notes(patches, checks)
    return Units(patches, islands, atolls, tuple(notes), checks)


MAX_OUTSIDE_KM = 1.0       # a patch further than this from its labelled atoll is flagged
MAX_ISLAND_SPREAD_KM = 15  # patches of one island spread over more than this are flagged


def spatial_checks(patches: gpd.GeoDataFrame, atolls: gpd.GeoDataFrame) -> pd.DataFrame:
    """Does every patch lie in the atoll named in the units file? Nothing is changed, only reported."""
    polygons = atolls.set_index("atoll").geometry
    rows = []
    for patch_id, atoll, geometry in zip(patches["patch_id"], patches["atoll"], patches.geometry, strict=True):
        distances = polygons.distance(geometry) / 1000.0
        nearest = distances.idxmin()
        rows.append({
            "patch_id": patch_id,
            "labelled_atoll": atoll,
            "distance_to_labelled_atoll_km": float(distances[atoll]),
            "nearest_atoll": nearest,
            "distance_to_nearest_atoll_km": float(distances[nearest]),
            "label_ok": bool(distances[atoll] <= MAX_OUTSIDE_KM),
        })
    return pd.DataFrame(rows)


def check_notes(patches: gpd.GeoDataFrame, checks: pd.DataFrame) -> list[str]:
    """Plain language notes about labels that do not match the geometry."""
    notes = []
    merged = patches[["patch_id", "island_id", "atoll"]].merge(checks, on="patch_id")
    for island_id, group in merged.groupby("island_id"):
        bad = group[~group["label_ok"]]
        if len(bad):
            where = ", ".join(f"{n} in {a}" for a, n in bad["nearest_atoll"].value_counts().items())
            notes.append(
                f"{island_id}: {len(bad)} of {len(group)} patches lie outside the {group['atoll'].iloc[0]} polygon "
                f"(up to {bad['distance_to_labelled_atoll_km'].max():.0f} km away; nearest atoll: {where}). "
                "The Island and Atoll labels of the units file may be wrong. Results are reported with the labels as they are."
            )
    for island_id, group in patches.groupby("island_id"):
        minx, miny, maxx, maxy = group.total_bounds
        spread = ((maxx - minx) ** 2 + (maxy - miny) ** 2) ** 0.5 / 1000.0
        if spread > MAX_ISLAND_SPREAD_KM:
            notes.append(f"{island_id}: its {len(group)} patches are spread over {spread:.0f} km, too much for one island. Check the Island labels.")
    return notes


def study_atoll_names(units: Units) -> list[str]:
    """Atolls that hold mangrove patches, in the order of the atolls file."""
    return units.atolls.loc[units.atolls["has_mangrove"], "atoll"].tolist()


def expected_unit_ids(units: Units, level: str) -> list[str]:
    """Unit ids that an indicator table of the given level should cover."""
    if level == "mangrove":
        return units.patches["patch_id"].tolist()
    return study_atoll_names(units)


def hash_vector_files(path: Path) -> str:
    """Hash of a vector dataset: the main file and, for shapefiles, the files next to it."""
    from .provenance import file_sha256
    import hashlib

    path = Path(path)
    parts = [path]
    if path.suffix.lower() == ".shp":
        parts += [path.with_suffix(ext) for ext in (".dbf", ".shx", ".prj", ".cpg") if path.with_suffix(ext).exists()]
    digest = hashlib.sha256()
    for part in parts:
        digest.update(part.suffix.lower().encode())
        digest.update(file_sha256(part).encode())
    return digest.hexdigest()


def empty_table() -> pd.DataFrame:
    return pd.DataFrame(columns=["unit_id", "year", "value", "valid_share", "n_pixels"])
