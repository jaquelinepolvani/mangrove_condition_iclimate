import numpy as np
import pandas as pd

from mangrove_condition.config_reader import validate_config
from mangrove_condition.ecv import linear_trend, trend_table
from mangrove_condition.pipeline import composite_table
from mangrove_condition.schema import EcvSpec
from mangrove_condition.tables import ecv_annual
from mangrove_condition.template import write_template


def _spec(**kw):
    base = dict(id="ECV_X", name="X", unit="u", file="x.csv", sheet="", unit_column="adm1_name",
                time_column="year_month", value_column="v", aggregation="mean", min_months=12, exclude_years=())
    base.update(kw)
    return EcvSpec(**base)


class _Config:
    class project:
        table_dir = "tables"
        atoll_name_fixes = {}


def test_monthly_mean_needs_enough_months(tmp_path):
    rows = [("A", f"2000-{m:02d}", 1.0) for m in range(1, 13)] + [("A", f"2001-{m:02d}", 3.0) for m in range(1, 7)]
    (tmp_path / "tables").mkdir()
    pd.DataFrame(rows, columns=["adm1_name", "year_month", "v"]).to_csv(tmp_path / "tables" / "x.csv", index=False)
    out = ecv_annual(_spec(), _Config, tmp_path, ["A"])
    assert out.loc[out.year == 2000, "value"].iloc[0] == 1.0
    assert np.isnan(out.loc[out.year == 2001, "value"].iloc[0])


def test_monthly_sum_and_excluded_year(tmp_path):
    rows = [("A", f"{y}-{m:02d}", 10.0) for y in (2000, 2001) for m in range(1, 13)]
    (tmp_path / "tables").mkdir()
    pd.DataFrame(rows, columns=["adm1_name", "year_month", "v"]).to_csv(tmp_path / "tables" / "x.csv", index=False)
    out = ecv_annual(_spec(aggregation="sum", exclude_years=(2001,)), _Config, tmp_path, ["A"])
    assert out["value"].tolist() == [120.0, 120.0]
    assert out["excluded"].tolist() == [False, True]


def test_trend_ignores_excluded_years_and_needs_enough_years():
    years = np.arange(2000, 2020, dtype=float)
    fit = linear_trend(years, 0.05 * (years - 2000) + 27)
    assert abs(fit["slope"] - 0.05) < 1e-9
    assert np.isnan(linear_trend(years[:5], years[:5])["slope"])
    annual = pd.DataFrame({"unit": "A", "year": years.astype(int), "value": 0.05 * (years - 2000), "excluded": False})
    annual.loc[annual.year == 2010, ["value", "excluded"]] = [-50.0, True]
    trends = trend_table(_spec(exclude_years=(2010,)), annual)
    assert abs(trends["slope_per_year"].iloc[0] - 0.05) < 1e-9
    assert trends["n_years"].iloc[0] == 19


def test_composite_is_the_equal_weight_mean_and_needs_all_components():
    from types import SimpleNamespace

    from mangrove_condition.schema import Level

    def part(pid, values):
        table = pd.DataFrame({"unit_id": ["A", "A"], "year": [2000, 2024], "value": values})
        return SimpleNamespace(indicator=SimpleNamespace(id=pid, level=Level.ATOLL), table=table)

    item = SimpleNamespace(id="COND_C", level=Level.ATOLL)
    out = composite_table(item, [part("P", [0.8, 0.6]), part("Q", [0.4, np.nan])])
    assert abs(out["value"].iloc[0] - 0.6) < 1e-9
    assert np.isnan(out["value"].iloc[1])


def test_tables_and_ecv_sheets_are_read(tmp_path):
    from openpyxl import load_workbook

    path = write_template(tmp_path / "c.xlsx")
    wb = load_workbook(path)
    wb["Tables"].append(["COND_SAL", "t.csv", None, "adm1_name", "year", "val", "indicator", "BHI"])
    wb["ECV"].append(["ECV_A", "A", "degC", "a.xlsx", "Monthly data", "adm1_name", "year_month", "v", "mean", 12, "2007"])
    wb.save(path)
    config, issues = validate_config(path)
    assert config is not None, [str(i) for i in issues if i.severity == "error"]
    assert config.tables[0].filter_value == "BHI"
    assert config.ecvs[0].exclude_years == (2007,)
