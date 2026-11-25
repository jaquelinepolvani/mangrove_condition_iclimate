"""Indicator report: annual series, account tables, classes, trends, ECV comparison and the written files."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from openpyxl import load_workbook

from mangrove_condition import indicators
from mangrove_condition.config_reader import validate_config
from mangrove_condition.pipeline import run

import synthetic

ECV_ROWS = (("ECV_SEALEVEL", "Sea level (Test)", "m", "sl", "none"), ("ECV_PRECIP", "Precipitation (Test)", "mm per year", "pr", "none"),
            ("ECV_TASMAX", "Air temperature, daily maximum (Test)", "degC", "tmax", "none"),
            ("ECV_TAS", "Air temperature, daily mean (Test)", "degC", "tas", "none"))


def _with_thresholds(config_path):
    workbook = load_workbook(config_path)
    sheet = workbook["Indicators"]
    sheet.cell(row=1, column=19).value = "class_thresholds"
    sheet.cell(row=2, column=19).value = "0.66;0.33"
    sheet.cell(row=3, column=19).value = "20;34;40"
    workbook.save(config_path)


def _with_ecvs(config_path, root):
    """Four ECVs with annual values: sea level and tasmax fall with the years, precipitation and tas rise."""
    years = list(range(2015, 2026))
    rows = []
    for k, atoll in enumerate(("Atoll A", "Atoll B")):
        for i, year in enumerate(years):
            rows.append({"atoll": atoll, "year": year, "sl": 1.0 - 0.01 * i + 0.1 * k, "pr": 1500 + 20 * i + 100 * k,
                         "tmax": 31.0 - 0.02 * i + 0.3 * k + (0.05 if i % 2 else 0), "tas": 28.0 + 0.02 * i + 0.2 * k})
    folder = root / "data" / "tables"
    folder.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_excel(folder / "ecv.xlsx", sheet_name="Yearly", index=False)
    workbook = load_workbook(config_path)
    sheet = workbook["ECV"]
    header = [c.value for c in sheet[1]]
    for r, (ecv_id, name, unit, column, aggregation) in enumerate(ECV_ROWS, start=2):
        values = {"id": ecv_id, "name": name, "unit": unit, "file": "ecv.xlsx", "sheet": "Yearly", "unit_column": "atoll",
                  "time_column": "year", "value_column": column, "aggregation": aggregation, "min_months": 1}
        for key, value in values.items():
            sheet.cell(row=r, column=header.index(key) + 1).value = value
    workbook.save(config_path)


@pytest.fixture
def result(tmp_path, monkeypatch):
    config = synthetic.make_project(tmp_path)
    _with_thresholds(config)
    _with_ecvs(config, tmp_path)
    monkeypatch.setattr(indicators, "REPORT_INDICATORS", ("COND_NDVI", "COND_SAL"))
    return run(config, tmp_path, log=lambda *a, **k: None, draw_maps=True)


def _readme(result):
    return (result.output_dir / "indicators" / "README.md").read_text(encoding="utf-8")


def test_report_files_are_written(result):
    folder = result.output_dir / "indicators"
    for name in ("README.md", "indicators_account.xlsx", "indicators_annual.xlsx", "indicators_vs_ecv.xlsx",
                 "figures/indicators_by_atoll.png"):
        assert (folder / name).exists(), name
    for ind in ("COND_NDVI", "COND_SAL"):
        ecvs = [e for e, _, _ in indicators.ECV_PAIRS[ind]]
        names = ["1_variable_by_atoll", "3_rescaled_by_year", "4_account_table", "5_change_map",
                 "7_ecv_forest", "8_ecv_all_atolls", *[f"6_{e}_by_atoll" for e in ecvs]]
        if ind == "COND_NDVI":                       # only NDVI has class limits in the test configuration
            names.append("2_condition_classes")
        for name in names:
            assert (folder / "figures" / f"{ind}_{name}.png").exists(), f"{ind}_{name}"
            assert (folder / "figures" / "vector" / f"{ind}_{name}.pdf").exists(), f"{ind}_{name}"


def test_annual_series_match_the_account_and_never_exceed_one(result):
    ndvi = next(i for i in result.inputs if i.indicator.id == "COND_NDVI")
    annual = indicators.annual_by_atoll(ndvi, result.units)
    row = annual[(annual["atoll"] == "Atoll A") & (annual["year"] == 2017)].iloc[0]
    assert row["value"] == pytest.approx(0.10)
    assert row["n_patches"] == 2 and row["cover"] == pytest.approx(1.0)
    assert annual["year"].nunique() == 9
    assert {"q25", "q75", "score"}.isdisjoint(annual.columns)
    assert annual["rescaled"].between(0, 1).all()
    big = ndvi.table.assign(value=5.0)
    tall = type(ndvi)(ndvi.indicator, big, ndvi.reference, ndvi.opening_years, ndvi.closing_years)
    assert indicators.annual_by_atoll(tall, result.units)["rescaled"].max() == pytest.approx(1.0)


def test_account_rows_follow_the_account_and_mark_the_direction_of_change(result):
    rows = indicators.account_rows(result.inputs, result.accounts, result.units, result.config)
    ndvi = rows[(rows["indicator_id"] == "COND_NDVI") & (rows["atoll"] == "Atoll A")].iloc[0]
    assert ndvi["rescaled_change"] == pytest.approx(ndvi["rescaled_close"] - ndvi["rescaled_open"])
    assert ndvi["rescaled_change"] > 0
    assert ndvi["class_open"] == "poor" and ndvi["class_close"] == "intermediate"
    assert rows["cover_flag"].sum() == 0
    assert not any("score" in c for c in rows.columns)


def test_class_bands_for_each_direction():
    base = dict(id="X", name="X", ect_class=None, unit=None, raster_file=None, level=None, ref_lower=0, ref_upper=1,
                optimum_lower=None, optimum_upper=None, in_index=True, weight=1.0)
    from mangrove_condition.schema import Direction, Indicator

    standard = Indicator(direction=Direction.STANDARD, class_thresholds=(0.66, 0.33), **base)
    bands = indicators.class_bands(standard)
    assert [b[2] for b in bands] == ["good", "intermediate", "poor"]
    assert bands[0][0] == 0.66 and bands[2][1] == 0.33
    two = Indicator(direction=Direction.TWO_SIDED, class_thresholds=(20, 34, 40), **{**base, "optimum_lower": 5, "optimum_upper": 20})
    bands = indicators.class_bands(two)
    assert [b[2] for b in bands] == ["good", "intermediate", "poor", "very poor"]
    assert bands[0][:2] == (5, 20) and bands[3][0] == 40
    assert indicators.class_bands(Indicator(direction=Direction.STANDARD, **base)) == []


def test_condition_class_uses_lower_limit_included_and_leaves_gaps_empty():
    standard = [(0.66, np.inf, "good"), (0.33, 0.66, "intermediate"), (-np.inf, 0.33, "poor")]
    assert indicators.condition_class(0.66, standard) == "good"
    assert indicators.condition_class(0.659, standard) == "intermediate"
    assert indicators.condition_class(-0.1, standard) == "poor"
    two_sided = [(5, 20, "good"), (20, 34, "intermediate"), (34, 40, "poor"), (40, np.inf, "very poor")]
    assert indicators.condition_class(33.5, two_sided) == "intermediate"   # the gap of the team sheet
    assert indicators.condition_class(3.0, two_sided) == "n/a"             # below the optimum no class is defined
    assert indicators.condition_class(np.nan, two_sided) == "n/a"


def test_same_patch_change_uses_patches_with_data_in_both_periods(result):
    ndvi = next(i for i in result.inputs if i.indicator.id == "COND_NDVI")
    table = ndvi.table[~((ndvi.table["unit_id"] == ndvi.table["unit_id"].iloc[0]) & ndvi.table["year"].isin([2024, 2025]))]
    trimmed = type(ndvi)(ndvi.indicator, table, ndvi.reference, ndvi.opening_years, ndvi.closing_years)
    extra = indicators.same_patch_change(trimmed, result.units)
    assert extra["same_patch_n"].sum() == len(result.units.patches) - 1


def test_signed_text_never_shows_minus_zero():
    assert indicators._signed(-0.001) == "0.00" and indicators._signed(0.004) == "0.00"
    assert indicators._signed(-0.04) == "-0.04" and indicators._signed(0.13) == "+0.13"


def test_trends_use_only_the_years_asked(result):
    ndvi = next(i for i in result.inputs if i.indicator.id == "COND_NDVI")
    annual = indicators.annual_by_atoll(ndvi, result.units)
    full = indicators.indicator_trends(annual, ["Atoll A", "Atoll B"], 2017, 2025)
    six = indicators.indicator_trends(annual, ["Atoll A", "Atoll B"], 2017, 2022)
    five = indicators.indicator_trends(annual, ["Atoll A", "Atoll B"], 2017, 2021)
    assert full["n_years"].tolist() == [9, 9] and six["n_years"].tolist() == [6, 6] and five["n_years"].tolist() == [5, 5]
    assert full["value_slope_per_decade"].iloc[0] == pytest.approx(0.5)       # 0.05 per year
    assert six["rescaled_slope_per_decade"].notna().all()                      # six years are enough for an indicator trend
    assert five["rescaled_slope_per_decade"].isna().all()                      # fewer years than the minimum for a trend


def test_readme_goes_indicator_by_indicator_and_ends_with_the_downloads(result):
    text = _readme(result)
    order = ["## Condition indicators", "## How to read the figures", "## NDVI", "### Variable by atoll",
             "### Condition classes", "### Rescaled indicator by year", "### Condition account",
             "### Change in condition", "## Salinity", "## Rescaled indicators by atoll", "## Indicators and ECVs",
             "## Files for download"]
    positions = [text.index(h) for h in order]
    assert positions == sorted(positions)
    assert text.rindex("## ") == text.index("## Files for download")
    assert text.count("Bold:") == 1
    for name in ("indicators_account.xlsx", "indicators_annual.xlsx", "indicators_vs_ecv.xlsx", "figures/vector"):
        assert f"]({name})" in text
    for gone in ("middle half", "shaded", "Larger dots", "Scoring", "score", " gives "):
        assert gone not in text, gone


def test_class_table_and_method_text(result):
    text = _readme(result)
    assert "| NDVI | unitless |" in text and "| 0.66 or more | 0.33 to less than 0.66 | less than 0.33 | n/a |" in text
    assert "Source of the classes" not in text and "of the team" not in text and "team" not in text
    assert "Following the SEEA EA framework" in text and "reference levels (VL, VH)" in text
    assert "The thresholds leave a gap" not in text


def test_class_limits_leave_no_gap_between_classes():
    from mangrove_condition.schema import Direction, Indicator

    base = dict(id="X", name="X", ect_class=None, unit=None, raster_file=None, level=None, ref_lower=0, ref_upper=1,
                optimum_lower=None, optimum_upper=None, in_index=True, weight=1.0)
    bands = indicators.class_bands(Indicator(direction=Direction.STANDARD, class_thresholds=(0.66, 0.33), **base))
    assert indicators.condition_class(0.659, bands) == "intermediate" and indicators.condition_class(0.66, bands) == "good"
    assert indicators.condition_class(0.329, bands) == "poor" and indicators.condition_class(0.33, bands) == "intermediate"


def test_pair_table_compares_years_inside_each_atoll():
    variable = pd.DataFrame({"atoll": ["A"] * 6 + ["B"] * 3, "year": list(range(2017, 2023)) + [2017, 2018, 2019],
                             "value": [1, 2, 3, 4, 5, 6, 10, 11, 12]})
    ecv = pd.DataFrame({"unit": ["A"] * 6 + ["B"] * 3, "year": list(range(2017, 2023)) + [2017, 2018, 2019],
                        "value": [2, 4, 6, 8, 10, 12, 1, 2, 3], "excluded": False})
    pairs = indicators.pair_table(variable, ecv, 2017, 2022)
    assert set(pairs["atoll"]) == {"A"}                                        # B has fewer years than the minimum
    assert pairs["variable_anomaly"].sum() == pytest.approx(0)
    assert pairs["ecv_anomaly"].tolist() == pytest.approx([-5, -3, -1, 1, 3, 5])
    ecv.loc[ecv["year"] == 2020, "excluded"] = True
    assert 2020 not in set(indicators.pair_table(variable, ecv, 2017, 2022)["year"])


def test_correlation_is_undefined_for_constant_series():
    assert np.isnan(indicators.correlation([1, 1, 1, 1], [1, 2, 3, 4])["r"])
    full = indicators.correlation([1, 2, 3, 4, 5], [2, 4, 6, 8, 10])
    assert full["r"] == pytest.approx(1.0) and full["rho"] == pytest.approx(1.0) and full["n"] == 5


def test_ecv_comparison_flags_the_sign_of_the_relationship(result):
    summary = pd.read_excel(result.output_dir / "indicators" / "indicators_vs_ecv.xlsx", sheet_name="all_atolls")
    # In the test data NDVI and salinity rise with the years; sea level and tasmax fall, precipitation and tas rise
    ndvi = summary[summary["indicator_id"] == "COND_NDVI"].set_index("ecv_id")
    assert ndvi["same_sign_as_expected"].all()                        # falls with sea level and tasmax, rises with precipitation
    sal = summary[summary["indicator_id"] == "COND_SAL"].set_index("ecv_id")
    assert not sal.loc["ECV_SEALEVEL", "same_sign_as_expected"]       # expected positive, found negative
    assert not sal.loc["ECV_PRECIP", "same_sign_as_expected"]         # expected negative, found positive
    assert list(sal.index) == ["ECV_SEALEVEL", "ECV_PRECIP"]           # air temperature is not paired with salinity
    assert ndvi["n"].tolist() == [18, 18, 18]
    assert {"p_perm_years", "r_original_single_line", "atolls_with_expected_sign"} <= set(summary.columns)
    by_atoll = pd.read_excel(result.output_dir / "indicators" / "indicators_vs_ecv.xlsx", sheet_name="by_atoll")
    assert set(by_atoll["atoll"]) == {"Atoll A", "Atoll B"}
    assert {"r_low", "r_high", "p_perm", "p_perm_holm", "tau", "slope_low", "r_detrended", "r_lag1"} <= set(by_atoll.columns)
    text = _readme(result)
    assert "Sea level" in text and "exploratory analysis" in text and "All atolls" in text
    assert text.index("#### By atoll") < text.index("#### All atolls")


def test_fisher_interval_and_permutation_p():
    low, high = indicators.fisher_interval(0.5, 20)
    assert low < 0.5 < high and 0.05 < low < 0.1 and 0.76 < high < 0.8
    assert all(np.isnan(v) for v in indicators.fisher_interval(0.5, 3))
    x = [1, 2, 3, 4, 5, 6]
    assert indicators.permutation_p(x, [2, 4, 5, 8, 9, 12]) == pytest.approx(2 / 720)   # exact: only the two extreme orders
    assert indicators.permutation_p(x, [1, 3, 2, 3.5, 3.4, 2]) > 0.2
    assert np.isnan(indicators.permutation_p(x, [1] * 6))


def test_holm_adjustment():
    adjusted = indicators.holm_adjust(np.array([0.01, 0.04, np.nan, 0.03]))
    assert adjusted[0] == pytest.approx(0.03) and adjusted[3] == pytest.approx(0.06) and adjusted[1] == pytest.approx(0.06)
    assert np.isnan(adjusted[2])


def test_atoll_statistics_on_a_known_line():
    years = np.arange(2017, 2025)
    part = pd.DataFrame({"year": years, "ecv": [3, 1, 4, 1.5, 5, 9, 2.5, 6.0], "variable": 0.0})
    part["variable"] = 2 * part["ecv"] + 1
    previous = pd.Series(np.arange(2016, 2025, dtype=float), index=np.arange(2016, 2025))
    out = indicators.atoll_statistics(part, previous)
    assert out["r"] == pytest.approx(1.0) and out["slope"] == pytest.approx(2.0) and out["tau"] == pytest.approx(1.0)
    assert out["slope_low"] == pytest.approx(2.0) and out["n"] == 8
    assert out["n_lag1"] == 8 and np.isfinite(out["r_lag1"]) and np.isfinite(out["r_detrended"])


def test_year_permutation_p_keeps_atolls_together():
    years = list(range(2017, 2025))
    ecv = np.array([3, 1, 4, 1.5, 5, 9, 2.5, 6.0])
    rows = []
    for atoll, shift in (("A", 0.0), ("B", 10.0)):
        for y, e in zip(years, ecv, strict=True):
            rows.append({"atoll": atoll, "year": y, "ecv": e + shift, "variable": 2 * e + shift})
    pairs = pd.DataFrame(rows)
    for column in ("variable", "ecv"):
        pairs[f"{column}_anomaly"] = pairs[column] - pairs.groupby("atoll")[column].transform("mean")
    pooled = indicators.pooled_statistics(pairs)
    assert pooled["r"] == pytest.approx(1.0) and pooled["n"] == 16
    assert pooled["p_perm_years"] == pytest.approx(1 / 40320)        # the identity is the only arrangement with r = 1


def test_comparison_is_skipped_without_ecvs(tmp_path, monkeypatch):
    config = synthetic.make_project(tmp_path)
    _with_thresholds(config)
    monkeypatch.setattr(indicators, "REPORT_INDICATORS", ("COND_NDVI", "COND_SAL"))
    out = run(config, tmp_path, log=lambda *a, **k: None, draw_maps=True)
    text = (out.output_dir / "indicators" / "README.md").read_text(encoding="utf-8")
    assert "## Indicators and ECVs" not in text and "## Files for download" in text
    assert not (out.output_dir / "indicators" / "indicators_vs_ecv.xlsx").exists()


def test_account_workbook_colours_the_change_cells(result):
    sheet = load_workbook(result.output_dir / "indicators" / "indicators_account.xlsx")["account"]
    header = [c.value for c in sheet[1]]
    column = header.index("rescaled_change") + 1
    fills = {sheet.cell(row=r, column=column).fill.fgColor.rgb for r in range(2, sheet.max_row + 1)}
    assert len(fills) >= 1 and all(f not in (None, "00000000") for f in fills)


def test_class_thresholds_are_validated(tmp_path):
    config = synthetic.make_project(tmp_path)
    workbook = load_workbook(config)
    sheet = workbook["Indicators"]
    sheet.cell(row=1, column=19).value = "class_thresholds"
    sheet.cell(row=2, column=19).value = "0.33;0.66"      # standard indicators must go down
    workbook.save(config)
    parsed, issues = validate_config(config, tmp_path, check_files=False)
    assert any("class_thresholds" in str(i) for i in issues if i.severity == "error")
