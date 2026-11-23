"""Essential climate variables (ECV): annual series per atoll, linear trend, figures and a short report.

ECVs are context variables: they are not rescaled to scores and do not enter the index.
For every ECV the module writes three figures, a trend table and a markdown report (README.md):

1. main atolls: one panel per atoll, for the atolls that hold most of the mangrove area, with a dashed linear trend;
2. box plot: distribution of the annual values of the main atolls;
3. all atolls with mangroves: one panel per atoll.

The mean of the atolls with mangroves is used in the summary table of the report.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

from .schema import Config, EcvSpec  # noqa: E402
from .tables import ecv_annual  # noqa: E402
from .units import Units, study_atoll_names  # noqa: E402

MIN_YEARS = 8
# The main atolls are the largest ones in mangrove area, added until they hold this share of the total.
MAIN_AREA_SHARE = 0.90
SIGNIFICANCE = 0.05

BLUE = "#1f6fae"
INK = "#222222"
MUTED = "#6b6b6b"
GRID = "#e6e6e6"
GREY = "#9a9a9a"

TREND_COLUMNS = ["ecv_id", "ecv", "unit", "atoll", "mangrove_area_km2", "main_atoll", "first_year", "last_year",
                 "n_years", "mean", "slope_per_year", "slope_per_decade", "p_value", "r_squared", "excluded_years"]


def linear_trend(years: np.ndarray, values: np.ndarray) -> dict:
    """Ordinary least squares line of the values against the years."""
    if len(years) < MIN_YEARS:
        return {"slope": np.nan, "intercept": np.nan, "p": np.nan, "r2": np.nan}
    fit = stats.linregress(years, values)
    return {"slope": fit.slope, "intercept": fit.intercept, "p": fit.pvalue, "r2": fit.rvalue**2}


def mangrove_area_km2(units: Units) -> pd.Series:
    """Mangrove area of each atoll with mangroves, in km2, largest first."""
    frame = units.atolls.loc[units.atolls["has_mangrove"], ["atoll", "area_m2"]]
    return (frame.set_index("atoll")["area_m2"] / 1e6).sort_values(ascending=False)


def main_atolls(area: pd.Series, share: float = MAIN_AREA_SHARE) -> list[str]:
    """Largest atolls (by mangrove area) needed to reach the given share of the total area."""
    share_so_far = area.cumsum() / area.sum()
    count = int((share_so_far.shift(fill_value=0) < share).sum())
    return area.index[:max(count, 1)].tolist()


def _used(annual: pd.DataFrame) -> pd.DataFrame:
    return annual[annual["value"].notna() & ~annual["excluded"]]


def aggregate_series(annual: pd.DataFrame, atolls: list[str]) -> pd.DataFrame:
    """Mean of the atolls for each year. Only years in which every atoll has a value are kept."""
    used = _used(annual[annual["unit"].isin(atolls)])
    count = used.groupby("year")["value"].count()
    complete = count[count == len(atolls)].index
    return used[used["year"].isin(complete)].groupby("year")["value"].mean().reset_index()


def trend_table(item: EcvSpec, annual: pd.DataFrame, area: pd.Series | None = None,
                main: list[str] | None = None) -> pd.DataFrame:
    rows = []
    for atoll, part in annual.groupby("unit", sort=True):
        used = _used(part)
        fit = linear_trend(used["year"].to_numpy(float), used["value"].to_numpy(float))
        rows.append({
            "ecv_id": item.id, "ecv": item.name, "unit": item.unit, "atoll": atoll,
            "mangrove_area_km2": float(area.get(atoll, np.nan)) if area is not None else np.nan,
            "main_atoll": bool(main and atoll in main),
            "first_year": int(used["year"].min()) if len(used) else np.nan,
            "last_year": int(used["year"].max()) if len(used) else np.nan,
            "n_years": len(used), "mean": used["value"].mean() if len(used) else np.nan,
            "slope_per_year": fit["slope"], "slope_per_decade": fit["slope"] * 10,
            "p_value": fit["p"], "r_squared": fit["r2"],
            "excluded_years": ";".join(str(y) for y in item.exclude_years),
        })
    table = pd.DataFrame(rows, columns=TREND_COLUMNS)
    return table.sort_values("mangrove_area_km2", ascending=False, na_position="last").reset_index(drop=True)


# ----------------------------------------------------------------------------------------------- figures

STYLE = {
    "font.family": "sans-serif",
    "font.sans-serif": ["Arial", "Liberation Sans", "DejaVu Sans"],
    "font.size": 9,
    "axes.linewidth": 0.8,
    "axes.edgecolor": MUTED,
    "axes.labelcolor": INK,
    "axes.labelsize": 9,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "pdf.fonttype": 42,
    "svg.fonttype": "none",
}
HEAD_IN = 0.75   # space for title and subtitle (inches)
FOOT_IN = 0.55   # space for the legend (inches)
PNG_DPI = 300


def _base_unit(unit: str) -> str:
    """Unit without the time part: 'mm per year' becomes 'mm', 'degC' becomes the degree sign with C."""
    base = (unit or "").split(" per ")[0].strip()
    return "\u00b0C" if base == "degC" else base


def _axis_unit(unit: str) -> str:
    return "\u00b0C" if (unit or "") == "degC" else (unit or "")


def _style_axes(ax) -> None:
    from matplotlib.ticker import MaxNLocator

    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color=GRID, linewidth=0.7, zorder=0)
    ax.set_axisbelow(True)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=5, integer=True))
    ax.tick_params(length=3, width=0.8)


def _trend_line(ax, years: np.ndarray, values: np.ndarray):
    fit = linear_trend(years, values)
    if not np.isnan(fit["slope"]):
        xs = np.array([years.min(), years.max()], dtype=float)
        ax.plot(xs, fit["intercept"] + fit["slope"] * xs, color=INK, linewidth=1.2, linestyle=(0, (5, 3)),
                label="Linear trend", zorder=2)
    return fit


def _p_text(p: float) -> str:
    if np.isnan(p):
        return "p n/a"
    return "p < 0.001" if p < 0.001 else f"p = {p:.3f}"


def _header(fig, title: str, subtitle: str, height: float) -> None:
    """Same title block in every figure: ECV name in bold, then one grey line describing the panels."""
    fig.text(0.012, 1 - 0.14 / height, title, fontsize=12.5, fontweight="bold", color=INK, ha="left", va="top")
    fig.text(0.012, 1 - 0.46 / height, subtitle, fontsize=9, color=MUTED, ha="left", va="top")


def _save(fig, path: Path) -> Path:
    """PNG at 300 dpi next to the report and a vector PDF in the vector folder."""
    path = Path(path)
    fig.savefig(path, dpi=PNG_DPI, facecolor="white")
    vector = path.parent / "vector"
    vector.mkdir(exist_ok=True)
    fig.savefig(vector / f"{path.stem}.pdf", facecolor="white")
    plt.close(fig)
    return path


def plot_panels(item: EcvSpec, annual: pd.DataFrame, atolls: list[str], subtitle: str, ncols: int, out: Path,
                suffix: str) -> Path:
    """One panel per atoll: annual values as a line and the dashed linear trend."""
    unit, base = _axis_unit(item.unit), _base_unit(item.unit)
    nrows = int(np.ceil(len(atolls) / ncols))
    height = 2.3 * nrows + HEAD_IN + FOOT_IN
    with plt.rc_context(STYLE):
        fig, axes = plt.subplots(nrows, ncols, figsize=(3.2 * ncols, height), sharex=True, sharey=True, squeeze=False)
        flat = axes.ravel()
        for ax in flat:
            ax.set_visible(False)
        for ax, atoll in zip(flat, atolls, strict=False):
            ax.set_visible(True)
            part = annual[annual["unit"] == atoll].sort_values("year")
            used, dropped = _used(part), part[part["value"].notna() & part["excluded"]]
            ax.plot(used["year"], used["value"], color=BLUE, linewidth=1.3, label="Annual value", zorder=3)
            if len(dropped):
                ax.scatter(dropped["year"], dropped["value"], s=16, marker="x", color=GREY, zorder=3,
                           label="Year left out of the trend")
            fit = _trend_line(ax, used["year"].to_numpy(float), used["value"].to_numpy(float))
            _style_axes(ax)
            ax.set_title(atoll, loc="left", fontsize=9.5, fontweight="bold", color=INK, pad=6)
            if not np.isnan(fit["slope"]):
                significant = fit["p"] < SIGNIFICANCE
                ax.set_title(f"{fit['slope'] * 10:+.3g} {base} per decade" + ("" if significant else " (n.s.)"),
                             loc="right", fontsize=7.5, color=INK if significant else MUTED, pad=6)
        for ax in axes[:, 0]:
            ax.set_ylabel(unit)
        for i, ax in enumerate(flat[:len(atolls)]):
            if i + ncols >= len(atolls):  # last panel of its column: it carries the year labels
                ax.tick_params(labelbottom=True)
        handles, labels = [], []
        for ax in flat:
            for h, label in zip(*ax.get_legend_handles_labels(), strict=True):
                if label not in labels:
                    handles.append(h)
                    labels.append(label)
        fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False, fontsize=8.5,
                   bbox_to_anchor=(0.5, 0.0))
        _header(fig, item.name, subtitle, height)
        fig.tight_layout(rect=(0, FOOT_IN / height, 1, 1 - HEAD_IN / height))
        return _save(fig, Path(out) / f"{item.id}_{suffix}.png")


def plot_box(item: EcvSpec, annual: pd.DataFrame, atolls: list[str], out: Path) -> Path:
    """One box per atoll: the distribution of its annual values over the whole period."""
    unit = _axis_unit(item.unit)
    data = [_used(annual[annual["unit"] == a])["value"].to_numpy(float) for a in atolls]
    height = 3.4 + HEAD_IN
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(max(5.6, 1.5 * len(atolls) + 1.4), height))
        ax.boxplot(data, widths=0.5, showfliers=False, patch_artist=True,
                   boxprops={"facecolor": "#cfe1f1", "edgecolor": BLUE, "linewidth": 1.1},
                   medianprops={"color": INK, "linewidth": 1.5}, whiskerprops={"color": BLUE, "linewidth": 1.1},
                   capprops={"color": BLUE, "linewidth": 1.1})
        rng = np.random.default_rng(1)
        for i, values in enumerate(data, start=1):
            ax.scatter(i + rng.uniform(-0.12, 0.12, len(values)), values, s=8, color=BLUE, alpha=0.6,
                       edgecolor="white", linewidth=0.3, zorder=3)
        ax.set_xticks(range(1, len(atolls) + 1), atolls)
        _style_axes(ax)
        ax.xaxis.set_major_locator(matplotlib.ticker.FixedLocator(range(1, len(atolls) + 1)))
        ax.set_ylabel(unit)
        _header(fig, item.name, f"Distribution of annual values in the {len(atolls)} atolls with the most mangrove area", height)
        fig.tight_layout(rect=(0, 0.02, 1, 1 - HEAD_IN / height))
        return _save(fig, Path(out) / f"{item.id}_2_boxplot.png")


# ------------------------------------------------------------------------------------------------ report

def _md_table(trends: pd.DataFrame, unit: str) -> str:
    base = _base_unit(unit)
    head = (f"| Atoll | Mangrove area (km2) | Period | Mean ({_axis_unit(unit)}) | Trend ({base} per decade) | p |\n"
            "|---|---|---|---|---|---|\n")
    rows = []
    for r in trends.itertuples():
        p = "n/a" if np.isnan(r.p_value) else ("< 0.001" if r.p_value < 0.001 else f"{r.p_value:.3f}")
        slope = "n/a" if np.isnan(r.slope_per_decade) else f"{r.slope_per_decade:+.3g}"
        bold = "**" if (not np.isnan(r.p_value) and r.p_value < SIGNIFICANCE) else ""
        rows.append(f"| {r.atoll} | {r.mangrove_area_km2:.3f} | {r.first_year} to {r.last_year} | {r.mean:.4g} | "
                    f"{bold}{slope}{bold} | {p} |")
    return head + "\n".join(rows)


def write_report(folder: Path, sections: list[dict], atolls: list[str], main: list[str], area: pd.Series) -> Path:
    share = area[main].sum() / area.sum()
    lines = [
        "# Essential climate variables (ECV)",
        "",
        "## How to read the figures",
        "",
        f"- **Atolls with mangroves:** {len(atolls)} ({', '.join(atolls)}). Mangrove extent: Cerri 2020.",
        f"- **Main atolls:** {', '.join(main)}. Together they hold {share:.0%} of the mangrove area.",
        "- **Mean:** simple mean of the atolls with mangroves, in years where all of them have a value.",
        "- **Dashed line:** linear trend (least squares) of the annual values. A trend is called significant when p < 0.05.",
        "",
        "## Summary",
        "",
        "| ECV | Source file | Period of the mean | Unit | Trend of the mean (per decade) | p |",
        "|---|---|---|---|---|---|",
    ]
    for s in sections:
        lines.append(f"| [{s['name']}](#{s['anchor']}) | {s['file']} | {s['period']} | {s['unit_label']} | "
                     f"{s['agg_trend']} | {s['agg_p']} |")
    lines.append("")
    figure, table = 0, 0
    for s in sections:
        lines += [f"## {s['name']}", ""]
        captions = [
            ("1_main_atolls", f"annual values in the {len(main)} atolls with the most mangrove area, with linear trend "
                              "(dashed) and trend per decade."),
            ("2_boxplot", f"distribution of the annual values in the {len(main)} atolls with the most mangrove area. "
                          "Box: middle half of the years; line: median; whiskers: up to 1.5 times the box; dots: single years."),
            ("3_all_atolls", f"annual values in all {len(atolls)} atolls with mangroves, with linear trend (dashed) and "
                             "trend per decade."),
        ]
        if s.get("maps"):
            captions += [
                ("4_map_mean", f"mean of the annual values for the whole Maldives ({s['map_period']}). Outlines: atolls; "
                               "dark outline: atolls with mangroves."),
                ("5_map_atolls", f"mean of the annual values by atoll ({s['map_period']}). Colour: atolls with mangroves; "
                                 "grey: atolls without mangroves in the Cerri 2020 extent."),
            ]
        for suffix, text in captions:
            figure += 1
            lines += [f"![{s['name']}, figure {figure}](figures/{s['id']}_{suffix}.png)", "",
                      f"**Figure {figure}.** {s['name']}: {text}", ""]
        table += 1
        lines += [f"**Table {table}.** {s['name']}: mean and linear trend by atoll, largest mangrove area first. "
                  "Bold: significant (p < 0.05).", "", s["table"], ""]
        if s["note"]:
            lines += [s["note"], ""]
    path = Path(folder) / "README.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def read_ecv_rasters(config_path: Path | None) -> dict[str, str]:
    """Optional column raster_file of the ECV sheet: file name (in the raster folder) of the annual raster of each ECV."""
    if config_path is None or not Path(config_path).exists():
        return {}
    import openpyxl

    book = openpyxl.load_workbook(config_path, read_only=True, data_only=True)
    try:
        if "ECV" not in book.sheetnames:
            return {}
        rows = list(book["ECV"].iter_rows(values_only=True))
    finally:
        book.close()
    if not rows:
        return {}
    header = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
    if "raster_file" not in header or "id" not in header:
        return {}
    i_id, i_raster = header.index("id"), header.index("raster_file")
    return {str(r[i_id]): str(r[i_raster]).strip() for r in rows[1:]
            if len(r) > max(i_id, i_raster) and r[i_id] and r[i_raster]}


def _anchor(name: str) -> str:
    keep = "".join(c.lower() if c.isalnum() else "-" for c in name)
    return "-".join(part for part in keep.split("-") if part)


def run_ecv(config: Config, base_dir: Path, units: Units, out_dir: Path, log=print,
            config_path: Path | None = None) -> list[Path]:
    """Annual tables, trend table, figures and report for every ECV. Returns the files written."""
    if not config.ecvs:
        return []
    folder = Path(out_dir) / "ecv"
    figures = folder / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    for old in list(figures.glob("*.png")) + list(figures.glob("vector/*.pdf")):
        old.unlink()  # figures of earlier runs, so no outdated file stays in the folder
    atolls = study_atoll_names(units)
    area = mangrove_area_km2(units).reindex(atolls).sort_values(ascending=False)
    main = main_atolls(area)
    annual_sheets, trend_parts, sections, files = {}, [], [], []
    rasters = read_ecv_rasters(config_path)
    atolls_ll = None
    for item in config.ecvs:
        annual = ecv_annual(item, config, base_dir, atolls)
        trends = trend_table(item, annual, area, main)
        annual_sheets[item.id] = annual.rename(columns={"unit": "atoll"})
        trend_parts.append(trends)
        series = aggregate_series(annual, atolls)
        files.append(plot_panels(item, annual, main, f"Annual values in the {len(main)} atolls with the most mangrove area, "
                                 "with linear trend", 3, figures, "1_main_atolls"))
        files.append(plot_box(item, annual, main, figures))
        files.append(plot_panels(item, annual, atolls, f"Annual values in the {len(atolls)} atolls with mangroves, "
                                 "with linear trend", 4, figures, "3_all_atolls"))
        fit = linear_trend(series["year"].to_numpy(float), series["value"].to_numpy(float))
        dropped = sorted(set(_used(annual)["year"]) - set(series["year"]))
        note = ""
        if item.exclude_years:
            note = f"Years left out of the trends: {', '.join(str(y) for y in item.exclude_years)}."
        significant = not np.isnan(fit["p"]) and fit["p"] < SIGNIFICANCE
        trend_text = "n/a" if np.isnan(fit["slope"]) else f"{fit['slope'] * 10:+.3g} {_base_unit(item.unit or '')}".strip()
        section = {
            "id": item.id, "name": item.name, "anchor": _anchor(item.name), "unit_label": _axis_unit(item.unit), "file": item.file,
            "period": f"{int(series['year'].min())} to {int(series['year'].max())}" if len(series) else "n/a",
            "agg_trend": f"**{trend_text}**" if significant else trend_text,
            "agg_p": _p_text(fit["p"]).replace("p = ", "").replace("p < ", "< ").replace("p n/a", "n/a"),
            "table": _md_table(trends, item.unit or ""), "note": note, "maps": False,
        }
        if item.id in rasters:
            from . import ecv_maps

            raster_path = base_dir / config.project.raster_dir / rasters[item.id]
            if not raster_path.exists():
                log(f"  {item.id}: raster {rasters[item.id]} not found in {raster_path.parent}, maps skipped")
            else:
                if atolls_ll is None:
                    atolls_ll = units.atolls[["atoll", "geometry"]].to_crs(4326)
                cmap_name = getattr(config.project, "map_colormap", "viridis")
                means = _used(annual[annual["unit"].isin(atolls)]).groupby("unit")["value"].mean()
                _, _, names = ecv_maps.read_stack(raster_path)
                period = ecv_maps._period_text(names)
                files.append(ecv_maps.plot_value_map(item, raster_path, atolls_ll, atolls, cmap_name, figures))
                files.append(ecv_maps.plot_atoll_map(item, means, atolls_ll, section["period"], cmap_name, figures))
                section["maps"], section["map_period"] = True, period
        sections.append(section)
        log(f"  {item.id}: {series['year'].nunique()} years in the aggregate"
            + (f" ({len(dropped)} years without all atolls skipped)" if dropped else ""))
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
    files.append(write_report(folder, sections, atolls, main, area))
    return files


def run_ecv_report(config_path: str | Path, project_dir: str | Path | None = None, log=print) -> list[Path]:
    """Only the ECV part: reads the configuration and the units, then writes outputs/ecv."""
    from .config_reader import ConfigError, default_base_dir, validate_config
    from .units import read_units

    config_path = Path(config_path)
    base_dir = Path(project_dir) if project_dir else default_base_dir(config_path)
    config, issues = validate_config(config_path, base_dir, check_files=False)
    if config is None:
        raise ConfigError(issues)
    units = read_units(config.project, base_dir)
    out_dir = base_dir / config.project.output_dir
    log(f"ECV report: {len(config.ecvs)} ECVs, {int(units.atolls['has_mangrove'].sum())} atolls with mangroves")
    files = run_ecv(config, base_dir, units, out_dir, log, config_path)
    log(f"Done. Files in {out_dir / 'ecv'}")
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
