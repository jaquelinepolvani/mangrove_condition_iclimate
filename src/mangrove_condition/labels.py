"""Check and correct the Island and Atoll labels of the mangrove patches with the official island polygons (admin 2)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import pandas as pd

MAX_NEAREST_M = 100.0   # a patch with no overlap takes the nearest island only if it is this close (metres)


@dataclass(frozen=True)
class LabelResult:
    patches: gpd.GeoDataFrame   # original columns plus patch_id, Island_org, Atoll_org, label_src, isl_share
    changes: pd.DataFrame       # one row per patch whose Island or Atoll label changed


def _key(name: object) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalpha())


def relabel_patches(patches: gpd.GeoDataFrame, islands: gpd.GeoDataFrame, area_crs: str,
                    island_col: str = "Island", atoll_col: str = "Atoll",
                    islands_name_col: str = "adm2_name", islands_atoll_col: str = "adm1_name") -> LabelResult:
    """Label every patch with the island polygon that holds most of it.

    A patch with no overlap takes the nearest island within MAX_NEAREST_M. Other patches keep their label.
    The original labels are kept in Island_org and Atoll_org. Patch ids P001, P002, ... follow the file order.
    """
    for column in (island_col, atoll_col):
        if column not in patches.columns:
            raise ValueError(f"Column '{column}' not found in the units file.")
    for column in (islands_name_col, islands_atoll_col):
        if column not in islands.columns:
            raise ValueError(f"Column '{column}' not found in the islands file.")

    original = patches.copy()
    work = patches.to_crs(area_crs)
    polygons = islands[[islands_name_col, islands_atoll_col, "geometry"]].to_crs(area_crs).reset_index(drop=True)
    index = polygons.sindex

    records = []
    for geometry in work.geometry:
        found = polygons.iloc[index.query(geometry, predicate="intersects")]
        if len(found):
            overlap = found.geometry.intersection(geometry).area
            best = overlap.idxmax()
            records.append((best, "overlap", float(overlap.max() / geometry.area)))
            continue
        distance = polygons.geometry.distance(geometry)
        best = distance.idxmin()
        if distance.min() <= MAX_NEAREST_M:
            records.append((best, "nearest", 0.0))
        else:
            records.append((None, "kept", 0.0))

    result = original.copy()
    result["patch_id"] = [f"P{n:03d}" for n in range(1, len(result) + 1)]
    result["Island_org"] = original[island_col].astype(str).str.strip()
    result["Atoll_org"] = original[atoll_col].astype(str).str.strip()
    result["label_src"] = [source for _, source, _ in records]
    result["isl_share"] = [round(share, 2) for _, _, share in records]
    result[island_col] = [polygons.loc[i, islands_name_col] if i is not None else old
                          for (i, _, _), old in zip(records, result["Island_org"], strict=True)]
    result[atoll_col] = [polygons.loc[i, islands_atoll_col] if i is not None else old
                         for (i, _, _), old in zip(records, result["Atoll_org"], strict=True)]

    pairs = list(zip(result[island_col], result["Island_org"], result[atoll_col], result["Atoll_org"], strict=True))
    changed = [(i != io) or (a != ao) for i, io, a, ao in pairs]
    result["label_chg"] = [int(c) for c in changed]
    def same_name(new: str, old: str) -> bool:
        a, b = _key(new), _key(old)
        return a == b or a.startswith(b) or b.startswith(a)

    kind = ["spelling" if (same_name(i, io) and same_name(a, ao)) else "different island or atoll"
            for i, io, a, ao in pairs]
    result["chg_kind"] = ["" if not c else k for c, k in zip(changed, kind, strict=True)]
    changes = result.loc[changed, ["patch_id", "Atoll_org", "Island_org", atoll_col, island_col, "chg_kind", "label_src", "isl_share"]].copy()
    changes["area_m2"] = work.geometry.area[changed].round(1).to_numpy()
    changes.columns = ["patch_id", "atoll_original", "island_original", "atoll_corrected", "island_corrected",
                       "kind", "label_source", "share_inside_island", "area_m2"]
    return LabelResult(result, changes.reset_index(drop=True))


def fix_labels(units_file: str | Path, islands_file: str | Path, out_file: str | Path,
               area_crs: str = "EPSG:32643", island_col: str = "Island", atoll_col: str = "Atoll",
               islands_name_col: str = "adm2_name", islands_atoll_col: str = "adm1_name") -> LabelResult:
    """Write a copy of the units file with corrected labels and a table with every change."""
    patches = gpd.read_file(units_file)
    islands = gpd.read_file(islands_file)
    result = relabel_patches(patches, islands, area_crs, island_col, atoll_col, islands_name_col, islands_atoll_col)
    out_file = Path(out_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    result.patches.to_file(out_file)
    result.changes.to_csv(out_file.with_name(out_file.stem + "_changes.csv"), index=False)
    return result
