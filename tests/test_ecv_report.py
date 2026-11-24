import numpy as np
import pandas as pd

from mangrove_condition import ecv
from mangrove_condition.schema import EcvSpec


def _spec(**kw):
    base = dict(id="ECV_T", name="Test variable", unit="m", file="t.xlsx", sheet="s", unit_column="atoll",
                time_column="year", value_column="v", aggregation="none", min_months=1, exclude_years=())
    base.update(kw)
    return EcvSpec(**base)


def _annual(years=range(2000, 2020), atolls=("A", "B", "C"), excluded=()):
    rows = []
    for k, atoll in enumerate(atolls):
        for y in years:
            rows.append({"unit": atoll, "year": y, "value": 1.0 + 0.1 * k + 0.02 * (y - 2000), "n_months": np.nan,
                         "excluded": y in excluded})
    return pd.DataFrame(rows)


def test_main_atolls_reach_the_area_share():
    area = pd.Series({"A": 0.5, "B": 0.3, "C": 0.15, "D": 0.05})
    assert ecv.main_atolls(area, 0.9) == ["A", "B", "C"]
    assert ecv.main_atolls(area, 0.5) == ["A"]
    assert ecv.main_atolls(area, 1.0) == ["A", "B", "C", "D"]


def test_aggregate_keeps_only_years_with_every_atoll():
    annual = _annual()
    annual.loc[(annual.unit == "B") & (annual.year == 2005), "value"] = np.nan
    series = ecv.aggregate_series(annual, ["A", "B", "C"])
    assert 2005 not in set(series["year"])
    assert len(series) == 19


def test_aggregate_leaves_out_excluded_years():
    series = ecv.aggregate_series(_annual(excluded=(2007,)), ["A", "B", "C"])
    assert 2007 not in set(series["year"])


def test_trend_table_has_area_and_main_flag():
    area = pd.Series({"A": 0.5, "B": 0.3, "C": 0.2})
    table = ecv.trend_table(_spec(), _annual(), area, ["A", "B"])
    assert list(table["atoll"]) == ["A", "B", "C"]
    assert table["main_atoll"].tolist() == [True, True, False]
    assert np.allclose(table["slope_per_decade"], 0.2)


def test_figures_and_report_are_written(tmp_path):
    item, annual = _spec(), _annual()
    atolls, main = ["A", "B", "C"], ["A", "B"]
    files = [
        ecv.plot_panels(item, annual, main, "main", 3, tmp_path, "1_main_atolls"),
        ecv.plot_box(item, annual, main, tmp_path),
        ecv.plot_panels(item, annual, atolls, "all", 4, tmp_path, "5_all_atolls"),
    ]
    assert all(f.exists() and f.stat().st_size > 0 for f in files)
    assert len(list((tmp_path / "vector").glob("*.pdf"))) == 3
    area = pd.Series({"A": 0.5, "B": 0.3, "C": 0.2})
    trends = ecv.trend_table(item, annual, area, main)
    section = {"id": item.id, "name": "Test variable (Test source)", "anchor": "test-variable", "unit": "m",
               "period": "2000 to 2019", "unit_label": "m", "agg_trend": "**+0.2 m**",
               "table": ecv._md_table(trends, "m"), "note": ""}
    report = ecv.write_report(tmp_path, [section], atolls, main, area)
    text = report.read_text(encoding="utf-8")
    assert "figures/ECV_T_2_boxplot.png" in text and "| A | 0.500 |" in text
    assert "Aggregate" not in text and "aggregate" not in text
    assert "This page is written by the code" not in text


def _section(maps=False):
    return {"id": "ECV_T", "name": "Test variable (Test source)", "anchor": "test-variable", "unit": "m",
            "period": "2000 to 2019", "unit_label": "m", "agg_trend": "**+0.2 m**", "table": "| x |", "note": "",
            "maps": maps, "map_period": "2000 to 2019"}


def _report(tmp_path, section):
    area = pd.Series({"A": 0.5, "B": 0.3, "C": 0.2})
    path = ecv.write_report(tmp_path, [section], ["A", "B", "C"], ["A", "B"], area)
    return path.read_text(encoding="utf-8")


def test_report_starts_with_title_and_intro(tmp_path):
    text = _report(tmp_path, _section())
    assert text.startswith(f"# {ecv.REPORT_TITLE}\n\n{ecv.REPORT_INTRO}")


def test_summary_table_has_variable_source_period_unit_trend_only(tmp_path):
    text = _report(tmp_path, _section())
    assert "| Variable | Source | Period | Unit | Trend per decade |" in text
    assert "| [Test variable](#test-variable) | Test source | 2000 to 2019 | m | **+0.2 m** |" in text
    assert "t.xlsx" not in text and "Source file" not in text
    assert "| p |" not in text.split("## Test variable")[0]


def test_name_is_split_into_variable_and_source():
    assert ecv._split_name("Sea level, ADT (Copernicus Marine)") == ("Sea level, ADT", "Copernicus Marine")
    assert ecv._split_name("Precipitation (CHIRPS v2.0, provisional)") == ("Precipitation", "CHIRPS v2.0, provisional")
    assert ecv._split_name("Plain name") == ("Plain name", "")


def test_how_to_read_comes_after_the_summary_and_has_no_mean_definitions(tmp_path):
    text = _report(tmp_path, _section())
    assert text.index("## Summary") < text.index("## How to read the figures") < text.index("## Test variable")
    assert "Overall mean" not in text and "Long-term mean" not in text and "**Mean:**" not in text


def test_summary_trend_keeps_the_unit_of_the_data():
    assert ecv._summary_trend(-0.0279, "m") == "-0.0279 m"
    assert ecv._summary_trend(0.179, "\u00b0C") == "+0.179 \u00b0C"
    assert ecv._summary_trend(-101.4, "mm") == "-101 mm"
    assert ecv._summary_trend(float("nan"), "m") == "n/a"


def test_decimal_places_are_fixed_by_unit():
    assert ecv._num(28.0, "\u00b0C", "mean") == "28.00"
    assert ecv._num(0.18, "\u00b0C", "trend", sign=True) == "+0.180"
    assert ecv._num(0.8231, "m", "mean") == "0.823"
    assert ecv._num(0.0406, "m", "trend", sign=True) == "+0.0406"
    assert ecv._num(-20.3, "mm", "trend", sign=True) == "-20"
    assert ecv._num(1828.4, "mm", "mean") == "1828"
    assert ecv._num(12.3456, "kg", "mean") == "12.3"


def test_axis_label_names_the_variable_and_the_unit():
    from mangrove_condition.ecv_maps import axis_label

    assert axis_label("Sea level, absolute dynamic topography (Copernicus Marine)", "m") == "Sea level (m)"
    assert axis_label("Sea surface temperature (ESA CCI)", "degC") == "Sea surface temperature (\u00b0C)"
    assert axis_label("Precipitation (CHIRPS v2.0)", "mm per year") == "Precipitation (mm per year)"


def test_captions_do_not_repeat_the_variable_name_and_area_has_a_superscript(tmp_path):
    text = _report(tmp_path, _section(maps=True))
    assert "**Figure 1.** Annual values in the 2 atolls" in text
    assert "**Table 1.** Mean of the annual values" in text
    assert "Test variable (Test source): " not in text
    area = pd.Series({"A": 0.5, "B": 0.3, "C": 0.2})
    assert "Mangrove area (km\u00b2)" in ecv._md_table(ecv.trend_table(_spec(), _annual(), area, ["A", "B"]), "m")


def test_references_are_written_only_when_given(tmp_path, monkeypatch):
    assert "## References" not in _report(tmp_path, _section())
    monkeypatch.setattr(ecv, "REPORT_REFERENCES", ["Author, A. (2020). Title. Journal."])
    text = _report(tmp_path, _section())
    assert text.rstrip().endswith("- Author, A. (2020). Title. Journal.") and "## References" in text


def test_maps_sit_side_by_side_and_all_atolls_figure_is_off_by_default(tmp_path):
    text = _report(tmp_path, _section(maps=True))
    assert "<table><tr>" in text and text.count('width="100%"') == 2
    assert "ECV_T_3_map_mean.png" in text and "ECV_T_4_map_atolls.png" in text
    assert "all_atolls" not in text


def test_all_atolls_figure_can_be_switched_on(tmp_path, monkeypatch):
    monkeypatch.setattr(ecv, "SHOW_ALL_ATOLLS", True)
    assert "ECV_T_5_all_atolls.png" in _report(tmp_path, _section())


def test_anchor_follows_the_github_rule():
    assert ecv._anchor("Sea level, absolute dynamic topography (Copernicus Marine)") == \
        "sea-level-absolute-dynamic-topography-copernicus-marine"
    assert ecv._anchor("Precipitation (CHIRPS v2.0, provisional)") == "precipitation-chirps-v20-provisional"
