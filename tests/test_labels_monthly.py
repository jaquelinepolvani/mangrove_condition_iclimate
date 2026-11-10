"""Tests of the label correction and of the monthly table route."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import box

from mangrove_condition.labels import relabel_patches
from mangrove_condition.monthly_table import season_table


def islands():
    return gpd.GeoDataFrame(
        {"adm2_name": ["Alpha", "Beta"], "adm1_name": ["Atoll A", "Atoll B"]},
        geometry=[box(73.00, 5.00, 73.01, 5.01), box(73.50, 0.00, 73.51, 0.01)], crs="EPSG:4326")


def patches(rows):
    return gpd.GeoDataFrame(
        {"Island": [r[0] for r in rows], "Atoll": [r[1] for r in rows]},
        geometry=[r[2] for r in rows], crs="EPSG:4326")


def test_patch_inside_another_island_takes_its_labels_and_keeps_the_originals():
    data = patches([("Alpha", "Atoll A", box(73.002, 5.002, 73.004, 5.004)),
                    ("Alpha", "Atoll A", box(73.502, 0.002, 73.504, 0.004))])
    result = relabel_patches(data, islands(), "EPSG:32643")
    assert result.patches[["patch_id", "Island", "Atoll"]].values.tolist() == [
        ["P001", "Alpha", "Atoll A"], ["P002", "Beta", "Atoll B"]]
    assert result.patches["Island_org"].tolist() == ["Alpha", "Alpha"]
    assert len(result.changes) == 1 and result.changes.iloc[0]["patch_id"] == "P002"
    assert result.changes.iloc[0]["kind"] == "different island or atoll"


def test_spelling_difference_is_reported_as_spelling():
    data = patches([("Alfa", "Atoll A", box(73.002, 5.002, 73.004, 5.004))])
    islands_ = islands().copy()
    islands_.loc[0, "adm2_name"] = "Alfaa"
    result = relabel_patches(data, islands_, "EPSG:32643")
    assert result.changes.iloc[0]["kind"] == "spelling"


def test_patch_far_from_every_island_keeps_its_label():
    data = patches([("Gamma", "Atoll C", box(80.0, 2.0, 80.001, 2.001))])
    result = relabel_patches(data, islands(), "EPSG:32643")
    assert result.patches.iloc[0]["Island"] == "Gamma" and result.patches.iloc[0]["label_src"] == "kept"
    assert result.changes.empty


def test_season_table_uses_reliable_months_and_needs_two(tmp_path):
    monthly = pd.DataFrame({
        "atoll": ["A"] * 8 + ["B"] * 4, "year": [2020] * 4 + [2021] * 4 + [2020] * 4, "month": [1, 2, 3, 4] * 3,
        "ndvi_median": [0.5, 0.6, 0.7, 0.9] + [0.5, 0.6, 0.7, 0.9] + [0.4, 0.4, 0.4, 0.4],
        "coverage": [1, 1, 1, 0.5] + [1, 0.2, 0.1, 0.3] + [1, 1, 1, 1],
        "mangrove_pixels": [10] * 8 + [20] * 4,
    })
    path = tmp_path / "monthly.csv"
    monthly.to_csv(path, index=False)
    table = season_table(path, tmp_path / "out.csv").set_index(["unit_id", "year"])
    assert table.loc[("A", 2020), "value"] == pytest.approx(0.6)        # median of 0.5, 0.6, 0.7 (April has low coverage)
    assert np.isnan(table.loc[("A", 2021), "value"])                    # only one reliable month
    assert table.loc[("B", 2020), "n_pixels"] == 20
    assert (tmp_path / "out.csv").exists()
