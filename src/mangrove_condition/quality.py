"""Quality and missing data reports. Nothing is filled or removed: gaps are reported."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .rescale import outside_range
from .schema import Anchor, Indicator


def year_report(indicator: Indicator, table: pd.DataFrame, expected_ids: list[str], min_valid_share: float,
                anchors: tuple[Anchor, ...] | None) -> pd.DataFrame:
    """One row per year: how many units have a value, how many have low coverage, how many fall outside the reference."""
    rows = []
    for year in sorted(table["year"].unique()):
        sub = table[(table["year"] == year) & table["value"].notna()]
        below = above = np.nan
        if anchors is not None and len(sub):
            low, high = outside_range(sub["value"].to_numpy(), anchors)
            below, above = int(low.sum()), int(high.sum())
        rows.append({
            "indicator_id": indicator.id,
            "year": int(year),
            "units_expected": len(expected_ids),
            "units_with_value": int(sub["unit_id"].nunique()),
            "units_without_value": len(expected_ids) - int(sub["unit_id"].nunique()),
            "units_low_coverage": int((sub["valid_share"] < min_valid_share).sum()),
            "values_below_reference": below,
            "values_above_reference": above,
        })
    return pd.DataFrame(rows)


def unit_report(indicator: Indicator, table: pd.DataFrame, expected_ids: list[str],
                min_valid_share: float) -> pd.DataFrame:
    """One row per unit: years with a value, years without, years with low coverage."""
    years = sorted(table["year"].unique())
    rows = []
    for unit_id in expected_ids:
        sub = table[table["unit_id"] == unit_id]
        with_value = sub[sub["value"].notna()]
        have = set(with_value["year"])
        rows.append({
            "indicator_id": indicator.id,
            "unit_id": unit_id,
            "years_with_value": len(have),
            "years_without_value": ";".join(str(y) for y in years if y not in have),
            "years_low_coverage": int((with_value["valid_share"] < min_valid_share).sum()),
        })
    return pd.DataFrame(rows)
