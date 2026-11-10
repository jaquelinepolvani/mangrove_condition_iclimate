"""Trends of the essential climate variables (ECV): annual series per atoll, linear trend, figures.

ECVs are context variables: they are not rescaled to scores and do not enter the index.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

from .schema import Config, EcvSpec  # noqa: E402
from .tables import ecv_annual  # noqa: E402
from .units import Units, study_atoll_names  # noqa: E402

MIN_YEARS = 8
POINT = "#1f4e78"
LINE = "#c55a11"
GREY = "#9a9a9a"
TREND_COLUMNS = ["ecv_id", "ecv", "unit", "atoll", "first_year", "last_year", "n_years", "mean",
                 "slope_per_year", "slope_per_decade", "p_value", "r_squared", "excluded_years"]


def linear_trend(years: np.ndarray, values: np.ndarray) -> dict:
    """Ordinary least squares line of the values against the years."""
    if len(years) < MIN_YEARS:
        return {"slope": np.nan, "intercept": np.nan, "p": np.nan, "r2": np.nan}
    fit = stats.linregress(years, values)
    return {"slope": fit.slope, "intercept": fit.intercept, "p": fit.pvalue, "r2": fit.rvalue**2}


def trend_table(item: EcvSpec, annual: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for atoll, part in annual.groupby("unit", sort=True):
        used = part[part["value"].notna() & ~part["excluded"]]
        fit = linear_trend(used["year"].to_numpy(float), used["value"].to_numpy(float))
        rows.append({
            "ecv_id": item.id, "ecv": item.name, "unit": item.unit, "atoll": atoll,
            "first_year": int(used["year"].min()) if len(used) else np.nan,
            "last_year": int(used["year"].max()) if len(used) else np.nan,
            "n_years": len(used), "mean": used["value"].mean() if len(used) else np.nan,
            "slope_per_year": fit["slope"], "slope_per_decade": fit["slope"] * 10,
            "p_value": fit["p"], "r_squared": fit["r2"],
            "excluded_years": ";".join(str(y) for y in item.exclude_years),
        })
    return pd.DataFrame(rows, columns=TREND_COLUMNS)


def _p_text(p: float) -> str:
    if np.isnan(p):
        return "p n/a"
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def plot_ecv(item: EcvSpec, annual: pd.DataFrame, trends: pd.DataFrame, atolls: list[str], out: Path) -> Path:
    """One panel per atoll: annual values, trend line, excluded years in grey."""
    unit = f" ({item.unit})" if item.unit else ""
    fig, axes = plt.subplots(2, 4, figsize=(16, 7.5), squeeze=False, sharex=True, sharey=True, layout="constrained")
    flat = axes.ravel()
    for ax, atoll in zip(flat, atolls, strict=False):
        part = annual[annual["unit"] == atoll]
        used = part[part["value"].notna() & ~part["excluded"]]
        dropped = part[part["value"].notna() & part["excluded"]]
        ax.scatter(used["year"], used["value"], s=16, color=POINT, zorder=3)
        if len(dropped):
            ax.scatter(dropped["year"], dropped["value"], s=22, marker="x", color=GREY, zorder=3)
        row = trends[trends["atoll"] == atoll].iloc[0]
        if not np.isnan(row["slope_per_year"]):
            fit = linear_trend(used["year"].to_numpy(float), used["value"].to_numpy(float))
            xs = np.array([used["year"].min(), used["year"].max()], dtype=float)
            ax.plot(xs, fit["intercept"] + fit["slope"] * xs, color=LINE, linewidth=1.6, zorder=4)
            label = f"{row['slope_per_decade']:+.3g} per decade, {_p_text(row['p_value'])}"
        else:
            label = "trend not computed (too few years)"
        ax.set_title(f"{atoll}\n{label}", fontsize=9)
        ax.grid(alpha=0.25, linewidth=0.5)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    for ax in flat[len(atolls):]:
        ax.axis("off")
    for ax in axes[:, 0]:
        ax.set_ylabel(f"{item.name}{unit}", fontsize=9)
    note = "Line: linear trend (least squares) of the annual values."
    if item.exclude_years:
        note += f" Grey crosses: years left out of the trend ({', '.join(str(y) for y in item.exclude_years)}), known data problem."
    fig.suptitle(f"{item.name}: annual values and linear trend by atoll", fontsize=12)
    fig.supxlabel(note, fontsize=8, color="#6e6e6e")
    path = Path(out) / f"{item.id}_trend.png"
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path


def run_ecv(config: Config, base_dir: Path, units: Units, out_dir: Path, log=print) -> list[Path]:
    """Annual tables, trend table and one figure per ECV. Returns the files written."""
    if not config.ecvs:
        return []
    folder = Path(out_dir) / "ecv"
    folder.mkdir(parents=True, exist_ok=True)
    atolls = study_atoll_names(units)
    annual_sheets, trend_parts, files = {}, [], []
    for item in config.ecvs:
        annual = ecv_annual(item, config, base_dir, atolls)
        trends = trend_table(item, annual)
        annual_sheets[item.id] = annual.rename(columns={"unit": "atoll"})
        trend_parts.append(trends)
        files.append(plot_ecv(item, annual, trends, atolls, folder))
        log(f"  {item.id}: {annual['year'].nunique()} years, {len(trends)} atolls")
    all_trends = pd.concat(trend_parts, ignore_index=True)
    with pd.ExcelWriter(folder / "ecv_trends.xlsx", engine="openpyxl") as writer:
        all_trends.to_excel(writer, sheet_name="trends", index=False)
        _style(writer.sheets["trends"], all_trends)
    files.append(folder / "ecv_trends.xlsx")
    with pd.ExcelWriter(folder / "ecv_annual.xlsx", engine="openpyxl") as writer:
        for key, frame in annual_sheets.items():
            frame.to_excel(writer, sheet_name=key[:31], index=False)
            _style(writer.sheets[key[:31]], frame)
    files.append(folder / "ecv_annual.xlsx")
    return files


def _style(sheet, frame: pd.DataFrame) -> None:
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="D9E1F2")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for number, column in enumerate(frame.columns, start=1):
        sheet.column_dimensions[get_column_letter(number)].width = min(
            max([len(str(column))] + [len(str(v)) for v in frame[column].head(200)]) + 2, 30)
        if pd.api.types.is_float_dtype(frame[column]):
            for cell in sheet[get_column_letter(number)][1:]:
                cell.number_format = "0.000"
