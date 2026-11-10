"""Standardized atoll table from a monthly NDVI table (temporary route while the raster has gaps)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

REQUIRED = ("atoll", "year", "month", "ndvi_median", "coverage", "mangrove_pixels")


def season_table(monthly_csv: str | Path, out_csv: str | Path, months: tuple[int, ...] = (1, 2, 3, 4),
                 value_column: str = "ndvi_median", min_coverage: float = 0.8, min_months: int = 2) -> pd.DataFrame:
    """Season value of every atoll and year from the monthly values.

    A month is used when at least `min_coverage` of the mangrove pixels of the atoll have a value in it.
    The season value is the median of the monthly values and needs at least `min_months` months
    (the same rule as the raster of the Colab notebook, applied here to atoll values, not to pixels).
    Columns written: unit_id (atoll), year, value, valid_share (mean coverage of the used months), n_pixels.
    """
    monthly = pd.read_csv(monthly_csv)
    missing = [c for c in (*REQUIRED[:3], value_column, *REQUIRED[4:]) if c not in monthly.columns]
    if missing:
        raise ValueError(f"Monthly table: missing column(s) {', '.join(missing)}.")
    monthly = monthly[monthly["month"].isin(months)]
    rows = []
    for (atoll, year), group in monthly.groupby(["atoll", "year"]):
        used = group[(group["coverage"] >= min_coverage) & group[value_column].notna()]
        value = float(np.median(used[value_column])) if len(used) >= min_months else np.nan
        rows.append({
            "unit_id": atoll, "year": int(year), "value": value,
            "valid_share": round(float(used["coverage"].mean()), 3) if len(used) >= min_months else 0.0,
            "n_pixels": float(group["mangrove_pixels"].max()),
        })
    table = pd.DataFrame(rows).sort_values(["unit_id", "year"]).reset_index(drop=True)
    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(out_csv, index=False)
    return table
