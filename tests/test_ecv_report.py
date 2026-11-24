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


def test_summary_shows_metres_as_millimetres():
    assert ecv._summary_trend(-0.0279, "m") == "-27.9 mm"
    assert ecv._summary_trend(0.179, "\u00b0C") == "+0.179 \u00b0C"
    assert ecv._summary_trend(-101.0, "mm") == "-101 mm"
    assert ecv._summary_trend(float("nan"), "m") == "n/a"


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
