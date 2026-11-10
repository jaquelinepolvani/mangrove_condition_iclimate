"""End to end tests on the synthetic project (2 atolls, 3 patches, known raster values)."""

from __future__ import annotations

import json

import geopandas as gpd
import pandas as pd
import pytest

from mangrove_condition.pipeline import run

import synthetic


@pytest.fixture
def project(tmp_path):
    config = synthetic.make_project(tmp_path)
    return tmp_path, config


def go(project, **kwargs):
    base, config = project
    return run(config, base, log=lambda *a, **k: None, draw_maps=False, **kwargs)


def test_values_and_scores_match_hand_calculation(project):
    result = go(project)
    long = result.accounts.long
    ndvi = long[(long.indicator_id == "COND_NDVI") & (long.level == "patch")]
    assert ndvi["v_open"].round(6).unique().tolist() == [0.125]          # mean of 0.10 and 0.15
    assert ndvi["v_close"].round(6).unique().tolist() == [0.475]         # mean of 0.45 and 0.50
    sal = long[(long.indicator_id == "COND_SAL") & (long.level == "island")]
    assert sal["s_open"].round(6).unique().tolist() == [round((40 - 34.05) / 35, 6)]
    assert sal["s_close"].round(6).unique().tolist() == [round((40 - 34.75) / 35, 6)]


def test_atoll_indicator_is_repeated_in_every_patch_and_island(project):
    result = go(project)
    sal = result.accounts.long[result.accounts.long.indicator_id == "COND_SAL"]
    assert set(sal["level"]) == {"patch", "island", "atoll"}
    assert set(sal["source_level"]) == {"atoll"}


def test_index_is_the_equal_weight_mean(project):
    result = go(project)
    index = result.accounts.index
    row = index[index.unit_id == "Atoll A / Isl1"].iloc[0]
    assert row["index_open"] == pytest.approx((0.125 + (40 - 34.05) / 35) / 2, abs=1e-5)
    assert row["index_close"] == pytest.approx((0.475 + (40 - 34.75) / 35) / 2, abs=1e-5)
    assert row["index_change"] == pytest.approx(row["index_close"] - row["index_open"])
    assert not row["partial"]


def test_outputs_are_written(project):
    base, _ = project
    result = go(project)
    out = base / "outputs"
    for name in ("references_used", "units_check", "quality_by_unit"):
        assert (out / "checks" / f"{name}.csv").exists(), name
    book = pd.ExcelFile(out / "results.xlsx")
    assert book.sheet_names == ["atoll", "island", "patch", "index", "notes"]
    atoll = book.parse("atoll")
    assert list(atoll.columns[:2]) == ["atoll", "indicator_id"]
    assert {"reference_lower", "reference_upper", "opening_year", "closing_score", "change_score"} <= set(atoll.columns)
    assert set(book.parse("patch").columns) >= {"atoll", "island", "patch_id"}
    assert set(book.parse("index")["level"]) == {"atoll", "island", "patch"}
    for layer in ("patches", "islands", "atolls"):
        frame = gpd.read_file(out / "gis" / f"{layer}.shp")
        assert frame.crs.to_epsg() == 4326
        assert all(len(column) <= 10 for column in frame.columns if column != "geometry")
    assert (out / "gis" / "field_dictionary.csv").exists()
    record = json.loads((out / "run_record.json").read_text(encoding="utf-8"))
    assert record
    assert "COND_NDVI" in (out / "run_summary.txt").read_text(encoding="utf-8")
    assert result.output_dir == out


def test_second_run_reuses_the_tables_and_a_changed_raster_rebuilds_them(project):
    base, _ = project
    first = go(project)
    assert first.infos["COND_NDVI"].origin == "built"
    assert go(project).infos["COND_NDVI"].origin == "reused"
    synthetic.write_raster(base / "data/rasters/NDVI.tif", 73.0, 5.2, 0.001, (200, 500),
                           [0.5] * len(synthetic.YEARS))
    third = go(project)
    assert third.infos["COND_NDVI"].origin == "built"
    long = third.accounts.long
    assert long[(long.indicator_id == "COND_NDVI") & (long.level == "patch")]["v_open"].round(6).unique().tolist() == [0.5]


def test_supplied_table_is_used_as_it_is(project):
    base, _ = project
    go(project)
    path = base / "data/standardized/COND_NDVI.csv"
    (base / "data/standardized/COND_NDVI.meta.json").unlink()
    table = pd.read_csv(path)
    table["value"] = 0.9
    table.to_csv(path, index=False)
    result = go(project)
    assert result.infos["COND_NDVI"].origin == "supplied"
    long = result.accounts.long
    assert long[(long.indicator_id == "COND_NDVI") & (long.level == "patch")]["v_close"].round(6).unique().tolist() == [0.9]


def test_missing_indicator_makes_the_index_partial(project):
    base, _ = project
    go(project)
    path = base / "data/standardized/COND_NDVI.csv"
    (base / "data/standardized/COND_NDVI.meta.json").unlink()
    table = pd.read_csv(path)
    table.loc[(table.unit_id == "P003") & (table.year.isin([2024, 2025])), "value"] = float("nan")
    table.to_csv(path, index=False)
    result = go(project)
    index = result.accounts.index
    row = index[index.unit_id == "Atoll B / Isl2"].iloc[0]
    assert row["n_used_close"] == 1 and row["n_used_open"] == 2
    assert row["partial"]
    assert row["index_close"] == pytest.approx((40 - 34.75) / 35, abs=1e-5)
    assert row["n_common"] == 1                                         # change only on indicators in both periods
    assert row["index_change"] == pytest.approx(((40 - 34.75) / 35) - ((40 - 34.05) / 35), abs=1e-5)


def test_label_in_the_wrong_atoll_is_reported_not_corrected(project):
    base, _ = project
    shp = base / "data/units/Mangrove_extent_2020_Cerri.shp"
    frame = gpd.read_file(shp)
    frame.loc[2, "Atoll"] = "Atoll A"                    # patch P003 lies in Atoll B
    frame.to_file(shp)
    result = go(project)
    assert any("outside the Atoll A polygon" in note for note in result.units.notes)
    assert result.units.patches.set_index("patch_id").loc["P003", "atoll"] == "Atoll A"
