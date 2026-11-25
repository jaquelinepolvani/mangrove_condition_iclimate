"""Report of the condition indicators, one indicator after the other.

The module only reads what the pipeline has already computed (indicator tables, references and accounts); it does not
change any value. For every indicator in REPORT_INDICATORS it writes, in <output>/indicators:

1. the variable by atoll and year, in its original unit;
2. the rescaling (reference levels) with the condition classes, at opening and at closing;
3. the rescaled indicator (0 to 1) by atoll and year, with a linear trend;
4. the condition account by atoll (opening, closing, change) as a table image;
5. a map of the change of the rescaled indicator by atoll, on a continuous scale centred on zero.

After the indicators: both rescaled indicators together by atoll and, when the ECVs are configured, an exploratory
analysis of each indicator against the ECVs linked to it: first atoll by atoll (original values), then all atolls together.

Terms follow the SEEA EA: variable (original unit), reference levels, rescaled indicator (0 to 1), condition account.
The condition classes (good, intermediate, poor, very poor) come from thresholds from the literature (class_thresholds
in the configuration). The atoll value of a mangrove level indicator is the mean of its patches weighted by area, as in
the accounts.
"""

from __future__ import annotations

import itertools
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import colormaps  # noqa: E402
from matplotlib.colors import to_hex, to_rgb  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from scipy import stats  # noqa: E402

from .account import Accounts, IndicatorInput, native_frame  # noqa: E402
from .ecv import (  # noqa: E402
    BLUE, GRID, HEAD_IN, INK, MIN_YEARS, MUTED, REPORT_TITLE, SIGNIFICANCE, STYLE, _axis_unit, _header, _p_text, _save,
    _style, _style_axes, linear_trend, mangrove_area_km2,
)
from .ecv_maps import (  # noqa: E402
    MANGROVE_EDGE, NO_LABEL, NO_MANGROVE_EDGE, NO_MANGROVE_FILL, _frame, _label_atolls, _north_arrow, _region_lines,
    _scale_bar, axis_label, header_height, split_name, title_lines,
)
from .maps import CHANGE_CMAP, Product, _scale  # noqa: E402
from .rescale import rescale  # noqa: E402
from .schema import Config, Direction, Indicator, Level  # noqa: E402
from .tables import ecv_annual  # noqa: E402
from .units import Units, study_atoll_names  # noqa: E402

REPORT_INDICATORS = ("COND_NDVI", "COND_SAL")   # indicators shown in the report, in this order
COVER_DIFFERENCE_WARNING = 0.10   # an atoll is flagged when the area with data differs more than this between the years
CLASS_NAMES = ("good", "intermediate", "poor", "very poor")
CLASS_COLOURS = ("#d4ead3", "#fbf1c7", "#f8d9bd", "#f3bcbc")
LINE_COLOURS = (BLUE, "#c0612b", "#4d9a52")
# One colour and one marker per atoll (colour blind safe colours; the marker repeats the identity).
ATOLL_COLOURS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#6a3d9a", "#444444")
ATOLL_MARKERS = ("o", "s", "^", "D", "v", "P", "X", "h")
MIN_PAIR_YEARS = 5   # an atoll needs this many years with both values to enter the comparison with an ECV

# ECVs compared with each indicator: (ECV id in the configuration, expected sign of the association, strength).
# Sea state is left out (weak or moderate, and its data are points without a grid). Air temperature is used instead of
# sea surface temperature because temperature acts on the mangrove through the air and the leaf.
ECV_PAIRS = {
    "COND_NDVI": (("ECV_SEALEVEL", -1, "strong"), ("ECV_PRECIP", +1, "moderate"), ("ECV_TASMAX", -1, "moderate")),
    "COND_SAL": (("ECV_SEALEVEL", +1, "strong"), ("ECV_PRECIP", -1, "strong")),
}
MIN_TREND_YEARS = 6      # years with data needed for the linear trend of an indicator
TOP_ATOLLS = 5           # atolls (largest mangrove area first) that get the detailed tests
CONFIDENCE = 0.95        # level of the confidence intervals
EXACT_PERMUTATION_N = 8  # up to this number of values all permutations are used, above it random ones
RANDOM_PERMUTATIONS = 20000
REPORT_INTRO = (
    "Condition indicators of the mangroves of the Maldives, following the SEEA Ecosystem Accounting (SEEA EA) framework. "
    "Each variable is converted to a condition indicator between 0 (lowest condition) and 1 (reference condition). "
    "The condition account compares the opening and the closing year, and condition classes (good, intermediate, "
    "poor, very poor) follow thresholds from the literature."
)


# ------------------------------------------------------------------------------------------------ calculation

def _years_text(years: tuple[int, ...]) -> str:
    return ", ".join(str(y) for y in years)


def _unit_text(indicator: Indicator) -> str:
    return "" if (indicator.unit or "") in ("", "unitless") else indicator.unit


def _label(indicator: Indicator) -> str:
    unit = _unit_text(indicator)
    return f"{indicator.name} ({unit})" if unit else indicator.name


def _value_text(value: float, indicator: Indicator) -> str:
    if pd.isna(value):
        return "n/a"
    return f"{value:.2f}"


def annual_by_atoll(inp: IndicatorInput, units: Units) -> pd.DataFrame:
    """Value and rescaled indicator of an indicator for every atoll with mangroves and every year.

    Mangrove level indicators: area weighted mean of the patch values and of the rescaled patch values (the same rule
    as the accounts), the number of patches and the share of the mangrove area with a value.
    Atoll level indicators: the value of the atoll."""
    atolls = study_atoll_names(units)
    anchors = inp.reference.anchors
    table = inp.table[["unit_id", "year", "value"]].dropna(subset=["value"]).copy()
    columns = ["indicator_id", "atoll", "year", "value", "rescaled", "n_patches", "cover"]
    if inp.indicator.level is Level.MANGROVE:
        patches = units.patches.set_index("patch_id")
        table["atoll"] = table["unit_id"].map(patches["atoll"])
        table["weight"] = table["unit_id"].map(patches["area_m2"])
        table = table[table["atoll"].isin(atolls)]
        table["rescaled"] = rescale(table["value"].to_numpy(float), anchors) if anchors else np.nan
        total = patches.groupby("atoll")["area_m2"].sum()
        rows = []
        for (atoll, year), part in table.groupby(["atoll", "year"]):
            weight = part["weight"].to_numpy(float)
            rows.append({
                "atoll": atoll, "year": int(year),
                "value": float(np.average(part["value"], weights=weight)),
                "rescaled": float(np.average(part["rescaled"], weights=weight)) if anchors else np.nan,
                "n_patches": int(len(part)), "cover": float(weight.sum() / total[atoll]),
            })
        frame = pd.DataFrame(rows, columns=[c for c in columns if c != "indicator_id"])
    else:
        table = table[table["unit_id"].isin(atolls)].rename(columns={"unit_id": "atoll"})
        table["rescaled"] = rescale(table["value"].to_numpy(float), anchors) if anchors else np.nan
        frame = table[["atoll", "year", "value", "rescaled"]].assign(n_patches=np.nan, cover=np.nan)
        frame["year"] = frame["year"].astype(int)
    frame.insert(0, "indicator_id", inp.indicator.id)
    return frame.sort_values(["atoll", "year"]).reset_index(drop=True)[columns]


def same_patch_change(inp: IndicatorInput, units: Units) -> pd.DataFrame:
    """Change of the rescaled indicator by atoll using only the patches with a value in both periods."""
    empty = pd.DataFrame(columns=["same_patch_n", "same_patch_rescaled_change"], index=pd.Index([], name="atoll"))
    if inp.indicator.level is not Level.MANGROVE or not inp.reference.has_scale:
        return empty
    patches = units.patches.set_index("patch_id")
    native = native_frame(inp, patches.index.tolist()).dropna(subset=["s_open", "s_close"])
    if native.empty:
        return empty
    native = native.assign(atoll=patches["atoll"].reindex(native.index), weight=patches["area_m2"].reindex(native.index))
    rows = {}
    for atoll, part in native.groupby("atoll"):
        weight = part["weight"].to_numpy(float)
        rows[atoll] = {
            "same_patch_n": int(len(part)),
            "same_patch_rescaled_change": float(np.average(part["s_close"], weights=weight) - np.average(part["s_open"], weights=weight)),
        }
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("atoll")


def class_bands(indicator: Indicator) -> list[tuple[float, float, str]]:
    """Value ranges (lower limit included, upper limit excluded) of the condition classes, best class first, from
    class_thresholds. Empty when the limits are not given."""
    limits = list(indicator.class_thresholds)
    if not limits or indicator.direction is None:
        return []
    if indicator.direction is Direction.STANDARD:
        edges = [np.inf, *limits, -np.inf]
        return [(edges[i + 1], edges[i], CLASS_NAMES[i]) for i in range(len(edges) - 1)]
    if indicator.direction is Direction.INVERSE:
        edges = [-np.inf, *limits, np.inf]
    elif indicator.direction is Direction.TWO_SIDED and indicator.optimum_lower is not None:
        edges = [indicator.optimum_lower, *limits, np.inf]   # below the optimum no class is defined
    else:
        return []
    return [(edges[i], edges[i + 1], CLASS_NAMES[i]) for i in range(len(edges) - 1)]


def condition_class(value: float, bands: list[tuple[float, float, str]]) -> str:
    """Name of the class that holds the value, or n/a when the value is missing or outside every class."""
    if pd.isna(value):
        return "n/a"
    for low, high, name in bands:
        if low <= value < high:
            return name
    return "n/a"


def account_rows(inputs: list[IndicatorInput], accounts: Accounts, units: Units, config: Config) -> pd.DataFrame:
    """The condition account by atoll and indicator, in the order of the report (largest mangrove area first)."""
    order = mangrove_area_km2(units).index.tolist()
    long = accounts.long[accounts.long["level"] == "atoll"]
    rows = []
    for inp in inputs:
        opening, closing = config.periods(inp.indicator.id)
        extra = same_patch_change(inp, units)
        bands = class_bands(inp.indicator)
        sub = long[long["indicator_id"] == inp.indicator.id].set_index("unit_id")
        for atoll in order:
            if atoll not in sub.index:
                continue
            r = sub.loc[atoll]
            cover_gap = abs(r["cover_close"] - r["cover_open"]) if pd.notna(r["cover_open"]) and pd.notna(r["cover_close"]) else np.nan
            rows.append({
                "indicator_id": inp.indicator.id, "indicator_name": inp.indicator.name, "atoll": atoll,
                "opening": _years_text(opening), "closing": _years_text(closing),
                "value_open": r["v_open"], "value_close": r["v_close"], "value_change": r["v_change"],
                "rescaled_open": r["s_open"], "rescaled_close": r["s_close"], "rescaled_change": r["s_change"],
                "class_open": condition_class(r["v_open"], bands) if bands else "n/a",
                "class_close": condition_class(r["v_close"], bands) if bands else "n/a",
                "patches_open": r["n_patches_open"], "patches_close": r["n_patches_close"],
                "cover_open": r["cover_open"], "cover_close": r["cover_close"],
                "cover_flag": bool(cover_gap > COVER_DIFFERENCE_WARNING) if pd.notna(cover_gap) else False,
                "same_patch_n": extra["same_patch_n"].get(atoll, np.nan),
                "same_patch_rescaled_change": extra["same_patch_rescaled_change"].get(atoll, np.nan),
            })
    return pd.DataFrame(rows)


def indicator_trends(annual: pd.DataFrame, atolls: list[str], first: int, last: int,
                     min_years: int = MIN_TREND_YEARS) -> pd.DataFrame:
    """Linear trend (least squares) of the rescaled indicator and of the variable by atoll, between two years.
    An atoll needs at least min_years years with data; n_years tells how many were used."""
    rows = []
    for atoll in atolls:
        part = annual[(annual["atoll"] == atoll) & annual["year"].between(first, last)].dropna(subset=["value"])
        years = part["year"].to_numpy(float)
        fit = linear_trend(years, part["rescaled"].to_numpy(float), min_years)
        fit_value = linear_trend(years, part["value"].to_numpy(float), min_years)
        rows.append({
            "indicator_id": annual["indicator_id"].iloc[0], "atoll": atoll, "first_year": int(part["year"].min()) if len(part) else np.nan,
            "last_year": int(part["year"].max()) if len(part) else np.nan, "n_years": len(part),
            "rescaled_slope_per_decade": fit["slope"] * 10, "rescaled_p_value": fit["p"],
            "value_slope_per_decade": fit_value["slope"] * 10, "value_p_value": fit_value["p"],
        })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------ ECV comparison

def pair_table(variable: pd.DataFrame, ecv: pd.DataFrame, first: int, last: int) -> pd.DataFrame:
    """Annual variable and annual ECV of every atoll in the same years (original units), and each as the difference
    from the mean of its atoll over the years used (anomaly). Atolls need MIN_PAIR_YEARS years."""
    used = ecv[ecv["value"].notna() & ~ecv["excluded"]].rename(columns={"unit": "atoll", "value": "ecv"})[["atoll", "year", "ecv"]]
    merged = variable[["atoll", "year", "value"]].dropna().rename(columns={"value": "variable"}).merge(used, on=["atoll", "year"])
    merged = merged[merged["year"].between(first, last)]
    merged = merged.groupby("atoll").filter(lambda g: len(g) >= MIN_PAIR_YEARS).copy()
    for column in ("variable", "ecv"):
        merged[f"{column}_anomaly"] = merged[column] - merged.groupby("atoll")[column].transform("mean")
    return merged.reset_index(drop=True)


def correlation(x, y) -> dict:
    """Pearson r and Spearman rho with their p values. Not defined (NaN) when a series is constant or too short."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    out = {"n": int(len(x)), "r": np.nan, "p_r": np.nan, "rho": np.nan, "p_rho": np.nan}
    if len(x) < 3 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return out
    r = stats.pearsonr(x, y)
    rho = stats.spearmanr(x, y)
    out.update(r=float(r.statistic), p_r=float(r.pvalue), rho=float(rho.statistic), p_rho=float(rho.pvalue))
    return out


def fisher_interval(r: float, n: int, level: float = CONFIDENCE) -> tuple[float, float]:
    """Confidence interval of a Pearson correlation (Fisher z transformation). NaN when n < 4 or |r| = 1."""
    if n < 4 or not np.isfinite(r) or abs(r) >= 1:
        return (np.nan, np.nan)
    half = stats.norm.ppf(0.5 + level / 2) / math.sqrt(n - 3)
    z = math.atanh(r)
    return (math.tanh(z - half), math.tanh(z + half))


def _permutations(n: int, seed: int = 0) -> np.ndarray:
    """All orderings of range(n) when n is small, otherwise random orderings (fixed seed)."""
    if n <= EXACT_PERMUTATION_N:
        return np.array(list(itertools.permutations(range(n))))
    rng = np.random.default_rng(seed)
    return np.array([rng.permutation(n) for _ in range(RANDOM_PERMUTATIONS)])


def permutation_p(x, y, seed: int = 0) -> float:
    """Two sided p value of the Pearson correlation from a permutation test: the y values are reordered, the share of
    orderings with |r| at least as large as the observed one is the p value. Exact for up to EXACT_PERMUTATION_N values.
    Needs no assumption about the distribution of the values, which suits series of a few years."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = len(x)
    if n < 4 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return np.nan
    xc = x - x.mean()
    ys = y[_permutations(n, seed)]
    yc = ys - ys.mean(axis=1, keepdims=True)
    r = (yc @ xc) / (math.sqrt((xc**2).sum()) * np.sqrt((yc**2).sum(axis=1)))
    observed = float(np.corrcoef(x, y)[0, 1])
    return float(np.mean(np.abs(r) >= abs(observed) - 1e-12))


def holm_adjust(p: np.ndarray) -> np.ndarray:
    """Holm adjustment of p values for multiple tests (NaN stays NaN)."""
    p = np.asarray(p, float)
    out = np.full(p.shape, np.nan)
    valid = np.flatnonzero(~np.isnan(p))
    order = valid[np.argsort(p[valid])]
    running = 0.0
    for rank, index in enumerate(order):
        running = max(running, (len(order) - rank) * p[index])
        out[index] = min(1.0, running)
    return out


def _detrend(years: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Residuals of the values after removing their linear trend over the years."""
    fit = stats.linregress(years, values)
    return values - (fit.intercept + fit.slope * years)


def atoll_statistics(part: pd.DataFrame, ecv_by_year: pd.Series) -> dict:
    """Tests of one atoll for one ECV, on the original values of the variable and of the ECV (the correlation is the
    same on their anomalies, because the atoll mean is a constant).

    Basic: Pearson r with its confidence interval and permutation p, Spearman rho, Kendall tau, and the slope of the
    least squares line with its confidence interval. Additional: the correlation after removing the linear trend of both
    series (detrended), and with the ECV of the previous year (lag 1)."""
    x, y = part["ecv"].to_numpy(float), part["variable"].to_numpy(float)
    years = part["year"].to_numpy(float)
    keys = ("r_low", "r_high", "p_perm", "tau", "p_tau", "slope", "slope_low", "slope_high", "r_detrended", "p_detrended",
            "n_lag1", "r_lag1", "p_lag1")
    out = {**correlation(x, y), **dict.fromkeys(keys, np.nan)}
    if out["n"] < 4 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return out
    out["r_low"], out["r_high"] = fisher_interval(out["r"], out["n"])
    out["p_perm"] = permutation_p(x, y)
    tau = stats.kendalltau(x, y)
    out["tau"], out["p_tau"] = float(tau.statistic), float(tau.pvalue)
    fit = stats.linregress(x, y)
    half = stats.t.ppf(0.5 + CONFIDENCE / 2, out["n"] - 2) * fit.stderr
    out["slope"], out["slope_low"], out["slope_high"] = float(fit.slope), float(fit.slope - half), float(fit.slope + half)
    if out["n"] >= 5:
        rx, ry = _detrend(years, x), _detrend(years, y)
        if np.ptp(rx) > 1e-12 and np.ptp(ry) > 1e-12:
            out["r_detrended"] = float(np.corrcoef(rx, ry)[0, 1])
            out["p_detrended"] = permutation_p(rx, ry)
    previous = ecv_by_year.reindex(part["year"].to_numpy(int) - 1).to_numpy(float)
    keep = ~np.isnan(previous)
    if keep.sum() >= 5 and np.ptp(previous[keep]) > 0 and np.ptp(y[keep]) > 0:
        out["n_lag1"] = int(keep.sum())
        out["r_lag1"] = float(np.corrcoef(previous[keep], y[keep])[0, 1])
        out["p_lag1"] = permutation_p(previous[keep], y[keep])
    return out


def _pooled_r(x: np.ndarray, y: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Pearson r over the valid points of the last two axes (works on a stack of arrangements)."""
    axes = (-2, -1)
    n = mask.sum(axis=axes)
    xm, ym = np.where(mask, x, 0.0), np.where(mask, y, 0.0)
    sx, sy = xm.sum(axis=axes), ym.sum(axis=axes)
    sxy, sxx, syy = (xm * ym).sum(axis=axes), (xm * xm).sum(axis=axes), (ym * ym).sum(axis=axes)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (n * sxy - sx * sy) / np.sqrt((n * sxx - sx**2) * (n * syy - sy**2))


def year_permutation_p(pairs: pd.DataFrame, column: str = "anomaly") -> float:
    """p value of the correlation of all atolls together from a permutation of the years: the years of the variable are
    reordered (the same reordering in every atoll), so the atolls keep sharing their years and the points are not
    treated as independent. The ECV is left as it is."""
    years = sorted(pairs["year"].unique())
    v = pairs.pivot(index="year", columns="atoll", values=f"variable_{column}").reindex(years).to_numpy(float)
    e = pairs.pivot(index="year", columns="atoll", values=f"ecv_{column}").reindex(years).to_numpy(float)
    if len(years) < 4:
        return np.nan
    perms = _permutations(len(years))
    vp = v[perms]                         # (arrangements, years, atolls)
    mask = ~np.isnan(vp) & ~np.isnan(e)[None]
    r = _pooled_r(np.broadcast_to(e, vp.shape), vp, mask)
    observed = _pooled_r(e[None], v[None], (~np.isnan(v) & ~np.isnan(e))[None])[0]
    if not np.isfinite(observed):
        return np.nan
    r = r[np.isfinite(r)]
    return float(np.mean(np.abs(r) >= abs(observed) - 1e-12))


def pooled_statistics(pairs: pd.DataFrame) -> dict:
    """All atolls together: r and rho of the anomalies (equivalent to one intercept per atoll) with the p value of the
    year permutation, and r of the original values with a single line, which mixes the differences between atolls with
    the differences between years and is given for information only."""
    out = correlation(pairs["ecv_anomaly"], pairs["variable_anomaly"])
    out["p_perm_years"] = year_permutation_p(pairs)
    raw = correlation(pairs["ecv"], pairs["variable"])
    out["r_original_single_line"] = raw["r"]
    return out


def ecv_comparison(config: Config, base_dir: Path, annual: pd.DataFrame, indicator_id: str, atolls: list[str],
                   first: int, last: int, log=print) -> list[dict]:
    """For every ECV paired with the indicator: the paired table, the tests of each atoll and of all atolls together.
    The p values of the permutation tests of one indicator are adjusted together (Holm)."""
    by_id = {e.id: e for e in config.ecvs}
    results = []
    for ecv_id, sign, strength in ECV_PAIRS.get(indicator_id, ()):
        spec = by_id.get(ecv_id)
        if spec is None:
            log(f"  {indicator_id}: {ecv_id} is not in the ECV sheet, skipped")
            continue
        table = ecv_annual(spec, config, base_dir, atolls)
        pairs = pair_table(annual, table, first, last)
        if pairs.empty:
            log(f"  {indicator_id}: no years in common with {ecv_id}, skipped")
            continue
        usable = table[table["value"].notna() & ~table["excluded"]]
        by_atoll = {}
        for atoll, part in pairs.groupby("atoll"):
            series = usable[usable["unit"] == atoll].set_index("year")["value"]
            by_atoll[atoll] = atoll_statistics(part, series)
        results.append({"spec": spec, "sign": sign, "strength": strength, "pairs": pairs,
                        "series": usable[usable["year"].between(first, last)][["unit", "year", "value"]],
                        "pooled": pooled_statistics(pairs), "by_atoll": by_atoll,
                        "detailed": [a for a in atolls[:TOP_ATOLLS] if a in by_atoll]})
    keys = [(i, a) for i, res in enumerate(results) for a in res["by_atoll"]]
    adjusted = holm_adjust(np.array([results[i]["by_atoll"][a]["p_perm"] for i, a in keys]))
    for (i, a), value in zip(keys, adjusted, strict=True):
        results[i]["by_atoll"][a]["p_perm_holm"] = float(value)
    return results


# ------------------------------------------------------------------------------------------------ figures

def _grid(n: int, ncols: int, panel_w: float, panel_h: float):
    nrows = math.ceil(n / ncols)
    height = panel_h * nrows + 0.5 + HEAD_IN
    fig, axes = plt.subplots(nrows, ncols, figsize=(panel_w * ncols, height), squeeze=False)
    flat = list(axes.ravel())
    for extra in flat[n:]:
        extra.set_visible(False)
    return fig, flat[:n], height


def _full_years(part: pd.DataFrame, first: int, last: int) -> pd.DataFrame:
    """The rows of one atoll for every year from first to last; a year without data is a gap (NaN), not a bridge."""
    return part.set_index("year").reindex(range(first, last + 1)).rename_axis("year").reset_index()


def _lower_first(name: str) -> str:
    """Lower case first letter, unless the name starts with an acronym (NDVI)."""
    return name if name[:2].isupper() else name[:1].lower() + name[1:]


def mean_text(ind: Indicator) -> str:
    """What a point of the variable figure is, in words."""
    if ind.level is Level.MANGROVE:
        return f"Mean {_lower_first(ind.name)} of the mangrove patches of each atoll, weighted by patch area"
    return f"Annual value of {_lower_first(ind.name)} of each atoll"


def _atoll_style(atolls: list[str]) -> dict[str, tuple[str, str]]:
    return {a: (ATOLL_COLOURS[i % len(ATOLL_COLOURS)], ATOLL_MARKERS[i % len(ATOLL_MARKERS)]) for i, a in enumerate(atolls)}


def _atoll_legend(fig, style: dict[str, tuple[str, str]], ncol: int = 4) -> None:
    handles = [Line2D([], [], linestyle="", marker=m, markersize=6, markerfacecolor=c, markeredgecolor="white", color=c,
                      label=a) for a, (c, m) in style.items()]
    fig.legend(handles=handles, loc="lower center", ncol=ncol, frameon=False, fontsize=8.5)


def plot_variable_panels(inp: IndicatorInput, annual: pd.DataFrame, atolls: list[str], first: int, last: int,
                         out: Path) -> Path:
    """One panel per atoll: the annual value of the variable in its original unit."""
    ind = inp.indicator
    data = annual[annual["year"].between(first, last)]
    with plt.rc_context(STYLE):
        fig, axes, height = _grid(len(atolls), 4, 2.9, 2.2)
        low, high = data["value"].min(), data["value"].max()
        pad = (high - low) * 0.08 or 0.05
        for ax, atoll in zip(axes, atolls, strict=True):
            part = _full_years(data[data["atoll"] == atoll], first, last)
            ax.plot(part["year"], part["value"], color=BLUE, linewidth=1.4, zorder=3)
            ax.scatter(part["year"], part["value"], s=11, color=BLUE, zorder=4)
            ax.set_title(atoll, fontsize=9.5, color=INK, loc="left")
            ax.set_ylim(low - pad, high + pad)
            ax.set_xlim(first - 0.4, last + 0.4)
            _style_axes(ax)
        for ax in axes[::4]:
            ax.set_ylabel(_label(ind))
        _header(fig, f"{ind.name} by atoll", mean_text(ind), height)
        fig.tight_layout(rect=(0, 0.01, 1, 1 - HEAD_IN / height))
        return _save(fig, Path(out) / f"{ind.id}_1_variable_by_atoll.png")


def plot_condition_classes(inp: IndicatorInput, account: pd.DataFrame, out: Path) -> Path | None:
    """Condition classes: two side-by-side panels (opening and closing), each showing one dot per atoll positioned by
    the original variable value, with coloured class bands in the background."""
    ind = inp.indicator
    bands = class_bands(ind)
    rows = account.dropna(subset=["value_open", "value_close"], how="all")
    if not bands or rows.empty:
        return None
    edges = [e for lo, hi, _ in bands for e in (lo, hi) if np.isfinite(e)]
    anchors = [a.value for a in (inp.reference.anchors or ())]
    observed = pd.concat([rows["value_open"], rows["value_close"]]).dropna().tolist()
    low, high = min(edges + anchors + observed), max(edges + anchors + observed)
    pad = 0.05 * ((high - low) or 1.0)
    low, high = low - pad, high + pad
    opening, closing = rows["opening"].iloc[0], rows["closing"].iloc[0]
    n = len(rows)
    with plt.rc_context(STYLE):
        head = HEAD_IN + 0.35  # extra room for panel titles below the header
        height = 0.42 * n + 1.7 + head
        fig, (ax_open, ax_close) = plt.subplots(1, 2, figsize=(10, height), sharey=True)
        for ax, year_label, val_col, fill in ((ax_open, f"Opening ({opening})", "value_open", "white"),
                                               (ax_close, f"Closing ({closing})", "value_close", INK)):
            # Class bands with labels inside the band, near the top
            for (lo, hi, name), colour in zip(bands, CLASS_COLOURS, strict=False):
                a, b = max(lo, low), min(hi, high)
                if a < b:
                    ax.axvspan(a, b, color=colour, linewidth=0, zorder=0)
                    ax.text((a + b) / 2, -0.35, name.capitalize(), ha="center", va="center",
                            fontsize=7.5, color=MUTED, style="italic")
            # One dot per atoll
            edge_col = INK if fill == "white" else "white"
            lw = 1.4 if fill == "white" else 0.7
            for i, r in enumerate(rows.itertuples()):
                val = getattr(r, val_col)
                if pd.notna(val):
                    ax.scatter([val], [i], s=50, marker="o", facecolor=fill, edgecolor=edge_col,
                               linewidth=lw, zorder=4)
            ax.set_xlim(low, high)
            ax.set_ylim(n - 0.5, -0.5)
            ax.set_xlabel(_label(ind))
            ax.set_title(year_label, fontsize=10, color=INK, pad=10)
            ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(nbins=6))
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            ax.grid(axis="x", color=GRID, linewidth=0.6, zorder=1)
        ax_open.set_yticks(range(n), rows["atoll"].tolist())
        _header(fig, f"{ind.name}: condition classes",
                "Atoll value of the variable over the condition classes", height)
        fig.tight_layout(rect=(0, 0.01, 1, 1 - head / height))
        return _save(fig, Path(out) / f"{ind.id}_2_condition_classes.png")


def _trend_title(trend: pd.Series) -> tuple[str, bool]:
    slope, p = trend["rescaled_slope_per_decade"], trend["rescaled_p_value"]
    if pd.isna(slope):
        return "trend n/a", False
    text = f"{slope:+.2f} per decade, {_p_text(p)}" if not abs(slope) < 0.005 else f"0.00 per decade, {_p_text(p)}"
    return text, bool(pd.notna(p) and p < SIGNIFICANCE)


def plot_rescaled_by_year(inp: IndicatorInput, annual: pd.DataFrame, trends: pd.DataFrame, atolls: list[str],
                          first: int, last: int, out: Path) -> Path:
    """One panel per atoll: the rescaled indicator by year with its linear trend (dashed)."""
    ind = inp.indicator
    data = annual[annual["year"].between(first, last)]
    with plt.rc_context(STYLE):
        fig, axes, height = _grid(len(atolls), 4, 2.9, 2.35)
        for ax, atoll in zip(axes, atolls, strict=True):
            part = _full_years(data[data["atoll"] == atoll], first, last)
            ax.plot(part["year"], part["rescaled"], color=BLUE, linewidth=1.4, zorder=3)
            ax.scatter(part["year"], part["rescaled"], s=11, color=BLUE, zorder=4)
            trend = trends[trends["atoll"] == atoll].iloc[0]
            seen = part.dropna(subset=["rescaled"])
            fit = linear_trend(seen["year"].to_numpy(float), seen["rescaled"].to_numpy(float), MIN_TREND_YEARS)
            if not np.isnan(fit["slope"]):
                xs = np.array([seen["year"].min(), seen["year"].max()], dtype=float)
                ax.plot(xs, fit["intercept"] + fit["slope"] * xs, color=INK, linewidth=1.1, linestyle=(0, (5, 3)), zorder=2)
            text, significant = _trend_title(trend)
            ax.set_title(atoll, fontsize=9.5, color=INK, loc="left")
            ax.text(1.0, 1.02, text, transform=ax.transAxes, ha="right", va="bottom", fontsize=7.5, color=INK if significant else MUTED,
                    fontweight="bold" if significant else "normal")
            ax.set_ylim(-0.03, 1.05)
            ax.set_xlim(first - 0.4, last + 0.4)
            _style_axes(ax)
        for ax in axes[::4]:
            ax.set_ylabel("Rescaled indicator")
        _header(fig, f"{ind.name}: rescaled indicator by atoll and year",
                f"0 worst, 1 best. Dashed: linear trend {first} to {last}, with its slope per decade and p value", height)
        fig.tight_layout(rect=(0, 0.01, 1, 1 - HEAD_IN / height))
        return _save(fig, Path(out) / f"{ind.id}_3_rescaled_by_year.png")


def plot_combined(annuals: dict[str, pd.DataFrame], names: dict[str, str], atolls: list[str], first: int, last: int,
                  marked: list[int], out: Path) -> Path:
    """One panel per atoll with the rescaled indicator of every indicator by year."""
    with plt.rc_context(STYLE):
        fig, axes, height = _grid(len(atolls), 4, 2.9, 2.2)
        for ax, atoll in zip(axes, atolls, strict=True):
            for colour, (key, frame) in zip(LINE_COLOURS, annuals.items(), strict=False):
                part = _full_years(frame[(frame["atoll"] == atoll) & frame["year"].between(first, last)], first, last)
                ax.plot(part["year"], part["rescaled"], color=colour, linewidth=1.5, label=names[key], zorder=3)
                ax.scatter(part["year"], part["rescaled"], s=9, color=colour, zorder=4)
            for year in marked:
                ax.axvline(year, color=MUTED, linewidth=0.7, linestyle=(0, (4, 3)), zorder=1)
            ax.set_ylim(0, 1.03)
            ax.set_xlim(first - 0.4, last + 0.4)
            ax.set_title(atoll, fontsize=9.5, color=INK, loc="left")
            _style_axes(ax)
        for ax in axes[::4]:
            ax.set_ylabel("Rescaled indicator")
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc="lower center", ncol=len(labels), frameon=False, fontsize=9)
        _header(fig, "Rescaled indicators by atoll", "0 worst, 1 best; dashed lines: opening and closing years", height)
        fig.tight_layout(rect=(0, 0.05, 1, 1 - HEAD_IN / height))
        return _save(fig, Path(out) / "indicators_by_atoll.png")


def _change_scale(inp: IndicatorInput, change: pd.Series):
    product = Product(key=inp.indicator.id, title=inp.indicator.name, kind="atoll", values={"change": change}, periods={})
    return _scale(product, "change", colormaps[CHANGE_CMAP])


def plot_change_map(inp: IndicatorInput, change: pd.Series, atolls_ll: pd.DataFrame, opening: str, closing: str,
                    out: Path) -> Path:
    """Atoll polygons coloured by the change of the rescaled indicator (closing minus opening), scale centred on zero."""
    ind = inp.indicator
    cmap, norm = _change_scale(inp, change)
    west, south, east, north = atolls_ll.total_bounds
    extent = (west - 0.9, east + 0.9, south - 0.35, north + 0.35)
    lines = title_lines(f"{ind.name}: change in the rescaled indicator", 5.6)
    head = header_height(lines)
    height = 9.0 + head
    labelled = atolls_ll.copy()
    texts = {a: (f"{a} {_signed(change[a])}" if a in change.index and pd.notna(change[a]) else a) for a in labelled["atoll"]}
    labelled["atoll"] = labelled["atoll"].map(texts)
    with plt.rc_context({"font.family": "sans-serif", "font.sans-serif": ["Arial", "Liberation Sans", "DejaVu Sans"]}):
        fig, ax = plt.subplots(figsize=(5.6, height))
        for row in atolls_ll.itertuples():
            has = row.atoll in change.index and pd.notna(change[row.atoll])
            gdf = atolls_ll[atolls_ll["atoll"] == row.atoll]
            gdf.plot(ax=ax, facecolor=cmap(norm(change[row.atoll])) if has else NO_MANGROVE_FILL,
                     edgecolor=MANGROVE_EDGE if has else NO_MANGROVE_EDGE, linewidth=0.9 if has else 0.5, zorder=3)
        _frame(fig, ax, extent, lines, f"{opening} to {closing}. Blue: improved, red: worsened", height)
        _region_lines(ax, atolls_ll, extent[0], extent[1])
        _label_atolls(ax, labelled, [texts[a] for a in atolls_ll["atoll"] if a not in NO_LABEL], 73.2)
        _scale_bar(ax, (extent[1] - 1.5, extent[1], extent[2], extent[3]))   # right side, away from the Seenu label
        _north_arrow(ax, extent)
        fig.tight_layout(rect=(0, 0.03, 1, 1 - head / height))
        sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
        cbar = fig.colorbar(sm, ax=ax, shrink=0.45, aspect=22, pad=0.02)
        cbar.set_label("Change in the rescaled indicator (closing minus opening)", fontsize=9, color=INK)
        cbar.ax.tick_params(labelsize=8, colors=MUTED)
        cbar.outline.set_edgecolor(MUTED)
        return _save(fig, Path(out) / f"{ind.id}_5_change_map.png")


def _signed(value: float) -> str:
    """Two decimals with a sign; a change that rounds to zero is written without a sign."""
    text = f"{value:+.2f}"
    return "0.00" if text in ("+0.00", "-0.00") else text


def _text_colour(rgb) -> str:
    r, g, b = rgb[:3]
    return "white" if 0.299 * r + 0.587 * g + 0.114 * b < 0.45 else INK


def plot_account_table(inp: IndicatorInput, rows: pd.DataFrame, norm: tuple, out: Path) -> Path:
    """The condition account of one indicator as an image, so that the change cells can be coloured. The class stands
    right after the value of the variable it is assigned from."""
    ind = inp.indicator
    opening, closing = rows["opening"].iloc[0], rows["closing"].iloc[0]
    with_classes = bool((rows["class_open"] != "n/a").any() or (rows["class_close"] != "n/a").any())
    unit = _unit_text(ind)
    var = ind.name + (f" ({unit})" if unit else "")
    header = ["Atoll", f"{var}\n{opening}"]
    if with_classes:
        header.append(f"Class\n{opening}")
    header.append(f"{var}\n{closing}")
    if with_classes:
        header.append(f"Class\n{closing}")
    header += [f"Rescaled\n{opening}", f"Rescaled\n{closing}", "Change"]
    n_cols = len(header)
    cells, colours, text_colours = [], [], []
    for r in rows.itertuples():
        line = [r.atoll + (" *" if r.cover_flag else ""), _value_text(r.value_open, ind)]
        if with_classes:
            line.append(r.class_open.capitalize())
        line.append(_value_text(r.value_close, ind))
        if with_classes:
            line.append(r.class_close.capitalize())
        line += ["n/a" if pd.isna(r.rescaled_open) else f"{r.rescaled_open:.2f}",
                 "n/a" if pd.isna(r.rescaled_close) else f"{r.rescaled_close:.2f}",
                 "no data" if pd.isna(r.rescaled_change) else _signed(r.rescaled_change)]
        fills, inks = ["white"] * n_cols, [INK] * n_cols
        if pd.notna(r.rescaled_change):
            cmap, scale = norm
            rgb = cmap(scale(r.rescaled_change))
            fills[-1], inks[-1] = to_hex(rgb), _text_colour(rgb)
        cells.append(line)
        colours.append(fills)
        text_colours.append(inks)
    height = 0.225 * (len(cells) + 1) + 0.34 + HEAD_IN
    widths = ([0.15, 0.13, 0.11, 0.13, 0.11, 0.10, 0.10, 0.09] if with_classes else [0.18, 0.15, 0.15, 0.14, 0.14, 0.11])
    scale_w = sum(widths)
    widths = [w / scale_w for w in widths]
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(9.2 if with_classes else 6.8, height))
        ax.axis("off")
        table = ax.table(cellText=cells, colLabels=header, cellColours=colours, loc="upper center", cellLoc="center",
                         colWidths=widths)
        table.auto_set_font_size(False)
        table.set_fontsize(8.5)
        table.scale(1, 1.35)
        for (i, j), cell in table.get_celld().items():
            cell.set_edgecolor("#d0d0d0")
            cell.set_linewidth(0.6)
            if i == 0:
                cell.set_facecolor("#e8edf3")
                cell.get_text().set_fontweight("bold")
                cell.set_height(cell.get_height() * 1.7)
            else:
                cell.get_text().set_color(text_colours[i - 1][j])
            if j == 0 and i > 0:
                cell.get_text().set_ha("left")
                cell._loc = "left"
        fig.text(0.012, 1 - 0.14 / height, f"{ind.name}: condition account by atoll", fontsize=12.5, fontweight="bold",
                 color=INK, ha="left", va="top")
        fig.text(0.012, 1 - 0.46 / height,
                 "Class: from the value of the variable. Change: rescaled indicator, closing minus opening. Blue: improved, red: worsened",
                 fontsize=9, color=MUTED, ha="left", va="top")
        fig.subplots_adjust(left=0.01, right=0.99, top=1 - HEAD_IN / height, bottom=0.01)
        return _save(fig, Path(out) / f"{ind.id}_4_account_table.png")


def _short_ecv(spec) -> str:
    """Short name and unit of an ECV for axes, e.g. 'Sea level (m)'."""
    return axis_label(spec.name, spec.unit)


def _regression_band(x: np.ndarray, y: np.ndarray, xs: np.ndarray):
    """Least squares line and the confidence band of its mean (t distribution) over xs."""
    n = len(x)
    fit = stats.linregress(x, y)
    line = fit.intercept + fit.slope * xs
    resid = y - (fit.intercept + fit.slope * x)
    s = math.sqrt((resid**2).sum() / (n - 2)) if n > 2 else np.nan
    sxx = ((x - x.mean()) ** 2).sum()
    half = stats.t.ppf(0.5 + CONFIDENCE / 2, n - 2) * s * np.sqrt(1 / n + (xs - x.mean()) ** 2 / sxx)
    return line, line - half, line + half


def _stat_text(stat: dict) -> tuple[str, bool]:
    if pd.isna(stat.get("r")):
        return "n/a", False
    significant = bool(pd.notna(stat.get("p_perm_holm")) and stat["p_perm_holm"] < SIGNIFICANCE)
    return f"r = {stat['r']:+.2f}, n = {stat['n']}", significant


def plot_ecv_by_atoll(inp: IndicatorInput, res: dict, atolls: list[str], out: Path, number: int) -> Path:
    """One panel per atoll: the variable against one ECV, both in their original units, with the least squares line and
    the 95% confidence band of its mean. Every point is labelled with its year."""
    ind, spec, pairs = inp.indicator, res["spec"], res["pairs"]
    shown = [a for a in atolls if a in res["by_atoll"]]
    with plt.rc_context(STYLE):
        fig, axes, height = _grid(len(shown), 4, 3.0, 2.6)
        for ax, atoll in zip(axes, shown, strict=True):
            part = pairs[pairs["atoll"] == atoll]
            x, y = part["ecv"].to_numpy(float), part["variable"].to_numpy(float)
            if len(x) >= 4 and np.ptp(x) > 0:
                xs = np.linspace(x.min(), x.max(), 60)
                line, lo, hi = _regression_band(x, y, xs)
                ax.fill_between(xs, lo, hi, color=BLUE, alpha=0.16, linewidth=0, zorder=1)
                ax.plot(xs, line, color=INK, linewidth=1.1, zorder=3)
            ax.scatter(x, y, s=14, color=BLUE, zorder=4)
            for xv, yv, yr in zip(x, y, part["year"], strict=True):
                ax.annotate(f"{int(yr) % 100:02d}", (xv, yv), xytext=(3, 3), textcoords="offset points", fontsize=6.5, color=MUTED)
            text, significant = _stat_text(res["by_atoll"][atoll])
            ax.set_title(atoll, fontsize=9.5, color=INK, loc="left")
            ax.text(1.0, 1.02, text, transform=ax.transAxes, ha="right", va="bottom", fontsize=7.5,
                    color=INK if significant else MUTED, fontweight="bold" if significant else "normal")
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            ax.grid(color=GRID, linewidth=0.5, zorder=0)
            ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(nbins=4))
        for ax in axes[::4]:
            ax.set_ylabel(_label(ind))
        for ax in axes[-4:]:
            ax.set_xlabel(_short_ecv(spec))
        _header(fig, f"{ind.name} against {split_name(spec.name)[0].lower()}, by atoll",
                f"One point per year ({pairs['year'].min()} to {pairs['year'].max()}), labelled with the year. Line: least squares fit, "
                f"band: {CONFIDENCE:.0%} confidence interval of the line", height)
        fig.tight_layout(rect=(0, 0.01, 1, 1 - HEAD_IN / height))
        return _save(fig, Path(out) / f"{ind.id}_{number}_{spec.id}_by_atoll.png")


def plot_ecv_forest(inp: IndicatorInput, results: list[dict], atolls: list[str], out: Path, number: int) -> Path | None:
    """Pearson r of every atoll with its 95% confidence interval, one panel per ECV. A filled dot has the expected sign."""
    if not results:
        return None
    ind = inp.indicator
    shown = [a for a in atolls if any(a in r["by_atoll"] for r in results)]
    with plt.rc_context(STYLE):
        height = 0.34 * len(shown) + 2.3 + HEAD_IN
        fig, axes = plt.subplots(1, len(results), figsize=(3.6 * len(results) + 0.9, height), sharey=True, squeeze=False)
        for ax, res in zip(axes[0], results, strict=True):
            for row, atoll in enumerate(shown):
                stat = res["by_atoll"].get(atoll)
                if stat is None or pd.isna(stat["r"]):
                    continue
                expected = np.sign(stat["r"]) == res["sign"]
                lo, hi = stat["r_low"], stat["r_high"]
                if pd.notna(lo):
                    ax.plot([lo, hi], [row, row], color=MUTED, linewidth=1.2, zorder=2)
                ax.scatter([stat["r"]], [row], s=34, zorder=3, color=BLUE if expected else "white", edgecolor=BLUE, linewidth=1.3)
            ax.axvline(0, color=INK, linewidth=0.9, zorder=1)
            ax.set_xlim(-1, 1)
            ax.set_ylim(len(shown) - 0.5, -0.5)
            ax.set_yticks(range(len(shown)), shown)
            ax.set_title(f"{split_name(res['spec'].name)[0]}\nexpected: {_sign_text(res['sign'])} ({res['strength']})",
                         fontsize=9, color=INK, loc="left")
            ax.set_xlabel("Pearson r")
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            ax.grid(axis="x", color=GRID, linewidth=0.6, zorder=0)
        handles = [Line2D([], [], linestyle="", marker="o", markersize=6, markerfacecolor=BLUE, markeredgecolor=BLUE, label="expected sign"),
                   Line2D([], [], linestyle="", marker="o", markersize=6, markerfacecolor="white", markeredgecolor=BLUE, label="opposite sign")]
        fig.legend(handles=handles, loc="lower center", ncol=2, frameon=False, fontsize=8.5)
        _header(fig, f"{ind.name}: correlation with each ECV, by atoll",
                f"Dot: Pearson r of the original values. Line: {CONFIDENCE:.0%} confidence interval", height)
        fig.tight_layout(rect=(0, 0.07, 1, 1 - HEAD_IN / height))
        return _save(fig, Path(out) / f"{ind.id}_{number}_ecv_forest.png")


def plot_ecv_all_atolls(inp: IndicatorInput, results: list[dict], style: dict[str, tuple[str, str]], out: Path,
                        number: int) -> Path | None:
    """All atolls together: differences from the atoll mean, with a dashed line fitted to all points."""
    if not results:
        return None
    ind = inp.indicator
    with plt.rc_context(STYLE):
        height = 3.9 + HEAD_IN
        fig, axes = plt.subplots(1, len(results), figsize=(3.7 * len(results) + 0.6, height), squeeze=False)
        axes = axes[0]
        for ax, res in zip(axes, results, strict=True):
            spec, pairs, pooled = res["spec"], res["pairs"], res["pooled"]
            for atoll, part in pairs.groupby("atoll"):
                colour, marker = style[atoll]
                ax.scatter(part["ecv_anomaly"], part["variable_anomaly"], s=22, color=colour, marker=marker,
                           edgecolor="white", linewidth=0.5, zorder=4)
            ax.axhline(0, color=GRID, linewidth=0.9, zorder=1)
            ax.axvline(0, color=GRID, linewidth=0.9, zorder=1)
            if np.ptp(pairs["ecv_anomaly"]) > 0:
                slope, intercept = np.polyfit(pairs["ecv_anomaly"], pairs["variable_anomaly"], 1)
                xs = np.array([pairs["ecv_anomaly"].min(), pairs["ecv_anomaly"].max()])
                ax.plot(xs, intercept + slope * xs, color=INK, linewidth=1.1, linestyle=(0, (5, 3)), zorder=3)
            ax.set_title(f"{split_name(spec.name)[0]}\nexpected: {_sign_text(res['sign'])} ({res['strength']})\n"
                         f"r = {pooled['r']:+.2f}, rho = {pooled['rho']:+.2f}, n = {pooled['n']}, {_p_text(pooled['p_perm_years'])}",
                         fontsize=9, color=INK, loc="left")
            unit = _axis_unit(spec.unit)
            ax.set_xlabel(f"{_short_ecv(spec).rsplit(' (', 1)[0]}, difference from atoll mean ({unit})")
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            ax.grid(color=GRID, linewidth=0.5, zorder=0)
            ax.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(nbins=4))
        unit = f" ({_unit_text(ind)})" if _unit_text(ind) else ""
        axes[0].set_ylabel(f"{ind.name}, difference from atoll mean{unit}")
        _header(fig, f"{ind.name} against the ECVs, all atolls",
                "Differences from the atoll mean, dashed line fitted to all points", height)
        _atoll_legend(fig, {a: style[a] for a in sorted({a for r in results for a in r["pairs"]["atoll"].unique()}, key=list(style).index)})
        fig.tight_layout(rect=(0, 0.1, 1, 1 - HEAD_IN / height))
        return _save(fig, Path(out) / f"{ind.id}_{number}_ecv_all_atolls.png")


def _sign_text(sign: int) -> str:
    return "positive" if sign > 0 else "negative"


# ------------------------------------------------------------------------------------------------ files

def write_workbooks(folder: Path, annual: pd.DataFrame, trends: pd.DataFrame, account: pd.DataFrame,
                    norms: dict[str, tuple], comparison: pd.DataFrame, by_atoll: pd.DataFrame) -> list[Path]:
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter

    paths = []
    with pd.ExcelWriter(folder / "indicators_annual.xlsx", engine="openpyxl") as writer:
        for name, frame in (("annual", annual), ("trends", trends)):
            frame.to_excel(writer, sheet_name=name, index=False)
            _style(writer.sheets[name], frame)
    paths.append(folder / "indicators_annual.xlsx")
    with pd.ExcelWriter(folder / "indicators_account.xlsx", engine="openpyxl") as writer:
        account.to_excel(writer, sheet_name="account", index=False)
        sheet = writer.sheets["account"]
        _style(sheet, account)
        column = get_column_letter(list(account.columns).index("rescaled_change") + 1)
        for number, row in enumerate(account.itertuples(), start=2):
            if pd.notna(row.rescaled_change):
                cmap, norm = norms[row.indicator_id]
                colour = to_hex(cmap(norm(row.rescaled_change))).lstrip("#").upper()
                sheet[f"{column}{number}"].fill = PatternFill("solid", fgColor=colour)
                sheet[f"{column}{number}"].font = Font(color="FFFFFF" if _text_colour(to_rgb("#" + colour)) == "white" else "222222")
    paths.append(folder / "indicators_account.xlsx")
    if not comparison.empty:
        with pd.ExcelWriter(folder / "indicators_vs_ecv.xlsx", engine="openpyxl") as writer:
            for name, frame in (("all_atolls", comparison), ("by_atoll", by_atoll)):
                frame.to_excel(writer, sheet_name=name, index=False)
                _style(writer.sheets[name], frame)
        paths.append(folder / "indicators_vs_ecv.xlsx")
    return paths


def comparison_tables(indicator_id: str, results: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Statistics of every indicator and ECV pair: all atolls together, and each atoll (original values)."""
    total, atolls = [], []
    for res in results:
        pooled = res["pooled"]
        per = [v["r"] for v in res["by_atoll"].values() if pd.notna(v["r"])]
        total.append({"indicator_id": indicator_id, "ecv_id": res["spec"].id, "ecv": res["spec"].name,
                      "expected_sign": _sign_text(res["sign"]), "strength": res["strength"], **pooled,
                      "same_sign_as_expected": bool(pd.notna(pooled["r"]) and np.sign(pooled["r"]) == res["sign"]),
                      "atolls_tested": len(per), "atolls_with_expected_sign": int(sum(np.sign(r) == res["sign"] for r in per)),
                      "median_r_of_atolls": float(np.median(per)) if per else np.nan})
        for atoll, values in res["by_atoll"].items():
            atolls.append({"indicator_id": indicator_id, "ecv_id": res["spec"].id, "atoll": atoll,
                           "detailed": atoll in res["detailed"], **values})
    return pd.DataFrame(total), pd.DataFrame(atolls)


def _p_cell(p: float) -> str:
    return "n/a" if pd.isna(p) else ("< 0.001" if p < 0.001 else f"{p:.3f}")


def _class_text(inp: IndicatorInput) -> dict[str, str]:
    """Class limits as text, built from class_thresholds. The lower limit belongs to the class."""
    text = {name: "n/a" for name in CLASS_NAMES}
    for low, high, name in class_bands(inp.indicator):
        if np.isinf(high):
            text[name] = f"{low:g} or more"
        elif np.isinf(low):
            text[name] = f"less than {high:g}"
        else:
            text[name] = f"{low:g} to less than {high:g}"
    return text


def _g(value: float) -> str:
    return "n/a" if pd.isna(value) else f"{value:+.2g}"


def _ci(value: float, low: float, high: float, fmt: str = "+.2f") -> str:
    if pd.isna(value):
        return "n/a"
    if pd.isna(low):
        return format(value, fmt)
    return f"{format(value, fmt)} ({format(low, fmt)} to {format(high, fmt)})"


def _missing_years(annual: pd.DataFrame, atolls: list[str], first: int, last: int) -> str:
    parts = []
    for atoll in atolls:
        have = set(annual.loc[annual["atoll"] == atoll, "year"].astype(int))
        gone = [y for y in range(first, last + 1) if y not in have]
        if gone and have:
            parts.append(f"{atoll} ({', '.join(str(y) for y in gone)})")
    return ", ".join(parts)


def write_report(folder: Path, selected: list[IndicatorInput], rows: pd.DataFrame, trends: pd.DataFrame,
                 config: Config, files: dict[str, dict[str, bool]], comparison: pd.DataFrame, by_atoll: pd.DataFrame,
                 compared: dict[str, list[dict]], first: int, last: int, workbooks: list[str],
                 annual: pd.DataFrame | None = None) -> Path:
    lines = [f"# {REPORT_TITLE}", "", "## Condition indicators", "", REPORT_INTRO, ""]
    table = 1
    head = ("| Indicator | Unit | Level | Opening | Closing | Good | Intermediate | Poor | Very poor |\n"
            "|---|---|---|---|---|---|---|---|---|")
    body = []
    for inp in selected:
        ind = inp.indicator
        op, cl = config.periods(ind.id)
        level = "mangrove patches, mean by atoll weighted by area" if ind.level is Level.MANGROVE else "atoll"
        text = _class_text(inp)
        body.append(f"| {ind.name} | {_unit_text(ind) or 'unitless'} | {level} | {_years_text(op)} | {_years_text(cl)} | "
                    f"{text['good']} | {text['intermediate']} | {text['poor']} | {text['very poor']} |")
    lines += [head, *body, "",
              f"**Table {table}.** Condition indicators and condition classes. The limits of the classes are thresholds from the "
              "literature, in the unit of the variable.", ""]
    lines += ["**Rescaling.** Following the SEEA EA framework, each variable (V, in its original unit) is converted to a condition "
              "indicator (I) between 0 (lowest condition) and 1 (reference condition) by linear rescaling between reference levels "
              "(VL, VH).", ""]
    lines += ["## How to read the figures", "",
              "- **Rescaled indicator:** the condition indicator, from 0 (lowest condition) to 1 (reference condition), obtained from the "
              "variable as described under Rescaling. For indicators measured in the mangrove patches, the atoll value is the mean of the "
              "patch values weighted by patch area.",
              "- **Class:** the condition class of the atoll (good, intermediate, poor, very poor), assigned from the value of the variable "
              "(not from the rescaled indicator) with the thresholds in Table 1.",
              "- **Change:** the rescaled indicator at closing minus the rescaled indicator at opening. A positive change is an improvement "
              "in condition, a negative change a decline.",
              "- **Atolls:** only atolls with mangroves, the one with the largest mangrove area first.",
              f"- **Bold:** a significant trend (p < {SIGNIFICANCE}), in the figures and in the tables of trends.",
              "- **Rounding:** values are rounded to two decimals; the workbooks have the full values.",
              ""]
    figure = 0
    for inp in selected:
        ind = inp.indicator
        mine = files[ind.id]
        part = rows[rows["indicator_id"] == ind.id]
        op, cl = part["opening"].iloc[0], part["closing"].iloc[0]
        lines += [f"## {ind.name}", "", "### Variable by atoll", ""]
        figure += 1
        lines += [f"![{ind.name}, figure {figure}](figures/{ind.id}_1_variable_by_atoll.png)", "",
                  f"**Figure {figure}.** {mean_text(ind)}, {first} to {last}.", ""]
        if mine["classes"]:
            figure += 1
            lines += ["### Condition classes", "",
                      f"![{ind.name}, figure {figure}](figures/{ind.id}_2_condition_classes.png)", "",
                      f"**Figure {figure}.** Condition classes of {ind.name}. Atoll value of the variable at opening ({op}, open symbol) "
                      f"and closing ({cl}, filled symbol).", ""]
        figure += 1
        lines += ["### Rescaled indicator by year", "",
                  f"![{ind.name}, figure {figure}](figures/{ind.id}_3_rescaled_by_year.png)", "",
                  f"**Figure {figure}.** Rescaled indicator of {ind.name} by atoll and year, {first} to {last}. Dashed line: linear trend "
                  f"(least squares).", ""]
        trend = trends[trends["indicator_id"] == ind.id]
        table += 1
        rows_md = []
        for r in trend.itertuples():
            bold = "**" if pd.notna(r.rescaled_p_value) and r.rescaled_p_value < SIGNIFICANCE else ""
            slope = "n/a" if pd.isna(r.rescaled_slope_per_decade) else _signed(r.rescaled_slope_per_decade)
            rows_md.append(f"| {r.atoll} | {r.first_year:g} to {r.last_year:g} | {int(r.n_years)} | {bold}{slope}{bold} | {_p_cell(r.rescaled_p_value)} |"
                           if pd.notna(r.first_year) else f"| {r.atoll} | n/a | 0 | n/a | n/a |")
        gaps = _missing_years(annual[annual["indicator_id"] == ind.id], trend["atoll"].tolist(), first, last) if annual is not None else ""
        gap_text = f" Years without data: {gaps}." if gaps else ""
        lines += ["| Atoll | Period | Years with data | Trend of the rescaled indicator (per decade) | p |", "|---|---|---|---|---|", *rows_md, "",
                  f"**Table {table}.** Linear trend of the rescaled indicator of {ind.name}. n/a: fewer than {MIN_TREND_YEARS} years with data.{gap_text}", ""]
        table += 1
        flagged = bool(part["cover_flag"].any())
        note = (f" * The share of the mangrove area with data differs by more than {COVER_DIFFERENCE_WARNING:.0%} points between the "
                "two years, so part of the change may come from patches that have a value in only one of them.") if flagged else ""
        lines += ["### Condition account", "", f"![{ind.name} condition account](figures/{ind.id}_4_account_table.png)", "",
                  f"**Table {table}.** Condition account of {ind.name} by atoll, {op} and {cl}. Class: from the value of the variable. "
                  f"Change: rescaled indicator at closing minus at opening.{note}", ""]
        if mine["map"]:
            figure += 1
            lines += ["### Change in condition", "",
                      f'<img src="figures/{ind.id}_5_change_map.png" alt="{ind.name}, figure {figure}" width="50%">', "",
                      f"**Figure {figure}.** Change in the rescaled indicator of {ind.name} between {op} and {cl}. "
                      "Blue: improved; red: worsened; grey: atolls without mangroves. The scale is centred on zero.", ""]
    figure += 1
    lines += ["## Rescaled indicators by atoll", "", f"![Rescaled indicators by atoll, figure {figure}](figures/indicators_by_atoll.png)", "",
              f"**Figure {figure}.** Rescaled {' and '.join(i.indicator.name for i in selected)} by atoll and year, {first} to {last}. Dashed lines: opening and closing years.", ""]
    if not comparison.empty:
        lines += ["## Indicators and ECVs (exploratory analysis)", "",
                  "This section explores whether the annual variation of each condition indicator is associated with Essential Climate "
                  f"Variables (ECVs) that act on mangroves, {first} to {last}. Each atoll is analysed separately, with the original values "
                  "of the variable and of the ECV, and then all atolls (differences from the atoll mean). Statistics: Pearson r "
                  "with its 95% confidence interval and a permutation p value (adjusted for multiple tests with the Holm method), "
                  "Spearman rho, Kendall tau and the slope of a least squares line. r detrended: r after removing the linear trend of both "
                  "series. r lag 1: r with the ECV of the previous year. Expected: expected sign of the association. Results for all "
                  "atolls are in indicators_vs_ecv.xlsx.", ""]
        for inp in selected:
            ind = inp.indicator
            results = compared.get(ind.id, [])
            if not results:
                continue
            lines += [f"### {ind.name}", ""]
            figure += 1
            lines += ["#### By atoll", "",
                      f"![{ind.name} correlation with the ECVs, figure {figure}](figures/{ind.id}_7_ecv_forest.png)", "",
                      f"**Figure {figure}.** Pearson r of {ind.name} with each ECV in each atoll, with its 95% confidence interval. "
                      "A filled dot has the expected sign.", ""]
            change_in = f"{ind.name} ({_unit_text(ind)})" if _unit_text(ind) else ind.name
            for res in results:
                spec = res["spec"]
                sd = float(res["pairs"]["ecv_anomaly"].std(ddof=1))
                figure += 1
                lines += [f"![{ind.name} against {spec.name}, figure {figure}](figures/{ind.id}_6_{spec.id}_by_atoll.png)", "",
                          f"**Figure {figure}.** {_label(ind)} against {split_name(spec.name)[0].lower()} ({_axis_unit(spec.unit)}) in each atoll, "
                          "original values. Labels: year. Line: least squares fit with its 95% confidence band. "
                          f"A bold r marks an adjusted p < {SIGNIFICANCE}.", ""]
                table += 1
                lines += ["| Atoll | n | r (95% CI) | rho | tau | Slope (95% CI) | p (perm.) | p (Holm) | r detrended | r lag 1 |",
                          "|---|---|---|---|---|---|---|---|---|---|"]
                for atoll in res["detailed"]:
                    v = res["by_atoll"][atoll]
                    lines.append(
                        f"| {atoll} | {v['n']} | {_ci(v['r'], v['r_low'], v['r_high'])} | {_signed(v['rho']) if pd.notna(v['rho']) else 'n/a'} | "
                        f"{_signed(v['tau']) if pd.notna(v['tau']) else 'n/a'} | "
                        f"{_g(v['slope'] * sd)} ({_g(v['slope_low'] * sd)} to {_g(v['slope_high'] * sd)}) | {_p_cell(v['p_perm'])} | {_p_cell(v['p_perm_holm'])} | "
                        f"{_signed(v['r_detrended']) if pd.notna(v['r_detrended']) else 'n/a'} | "
                        f"{_signed(v['r_lag1']) if pd.notna(v['r_lag1']) else 'n/a'} |" if pd.notna(v["r"]) else f"| {atoll} | {v['n']} | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a |")
                lines += ["", f"**Table {table}.** {ind.name} and {split_name(spec.name)[0].lower()}: the {len(res['detailed'])} atolls with the "
                          f"largest mangrove area. Slope: change in {change_in} per standard deviation of the ECV "
                          f"({sd:.3g} {_axis_unit(spec.unit)}).", ""]
            figure += 1
            summary = comparison[comparison["indicator_id"] == ind.id]
            lines += ["#### All atolls", "",
                      f"![{ind.name} against the ECVs, all atolls, figure {figure}](figures/{ind.id}_8_ecv_all_atolls.png)", "",
                      f"**Figure {figure}.** {ind.name} against the ECVs, all atolls. Differences from the atoll mean, with a "
                      "dashed line fitted to all points.", ""]
            table += 1
            lines += ["| ECV | Expected | n | r | rho | p (year permutation) | Atolls with the expected sign | Median r of the atolls |",
                      "|---|---|---|---|---|---|---|---|"]
            for r in summary.itertuples():
                if pd.isna(r.r):
                    lines.append(f"| {split_name(r.ecv)[0]} | {r.expected_sign} ({r.strength}) | {r.n} | n/a | n/a | n/a | n/a | n/a |")
                else:
                    lines.append(f"| {split_name(r.ecv)[0]} | {r.expected_sign} ({r.strength}) | {r.n} | {r.r:+.2f} | {r.rho:+.2f} | "
                                 f"{_p_cell(r.p_perm_years)} | {r.atolls_with_expected_sign} of {r.atolls_tested} | {r.median_r_of_atolls:+.2f} |")
            lines += ["", f"**Table {table}.** Correlation of {ind.name} with each ECV, all atolls (differences from the atoll mean).", ""]
    lines += ["## Files for download", "",
              "- [indicators_account.xlsx](indicators_account.xlsx): the condition account by atoll, with values, rescaled indicators, classes, patches and, for the patch based indicators, the change using only the patches with data in both years.",
              "- [indicators_annual.xlsx](indicators_annual.xlsx): variable and rescaled indicator of every atoll and year (sheet annual) and the linear trends (sheet trends)."]
    if "indicators_vs_ecv.xlsx" in workbooks:
        lines.append("- [indicators_vs_ecv.xlsx](indicators_vs_ecv.xlsx): all tests of each indicator with the ECVs, by atoll (sheet by_atoll) and all atolls (sheet all_atolls).")
    lines += ["- [figures/vector](figures/vector): all figures as PDF.", ""]
    path = Path(folder) / "README.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_indicator_report(config: Config, units: Units, inputs: list[IndicatorInput], accounts: Accounts,
                         out_dir: Path, log=print, base_dir: Path | None = None) -> list[Path]:
    """Figures, tables and README of the indicators in REPORT_INDICATORS. Returns the files written.
    The comparison with the ECVs needs base_dir (the folder of the ECV spreadsheets) and ECVs in the configuration."""
    selected = [i for i in inputs if i.indicator.id in REPORT_INDICATORS and i.reference.has_scale]
    selected.sort(key=lambda i: REPORT_INDICATORS.index(i.indicator.id))
    if not selected:
        log("  no indicator of the report has data and a reference, report skipped")
        return []
    folder = Path(out_dir) / "indicators"
    figures = folder / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    for old in list(figures.glob("*.png")) + list(figures.glob("vector/*.pdf")):
        old.unlink()
    atolls = mangrove_area_km2(units).index.tolist()
    style = _atoll_style(atolls)
    annuals = {i.indicator.id: annual_by_atoll(i, units) for i in selected}
    rows = account_rows(selected, accounts, units, config)
    norms = {i.indicator.id: _change_scale(i, rows[rows["indicator_id"] == i.indicator.id].set_index("atoll")["rescaled_change"])
             for i in selected}
    atolls_ll = units.atolls.to_crs(4326)[["atoll", "geometry"]]
    periods = [config.periods(i.indicator.id) for i in selected]
    first = min(min(op) for op, _ in periods)
    last = max(max(cl) for _, cl in periods)
    files, produced, compared, trend_parts, totals, per_atoll = [], {}, {}, [], [], []
    for inp in selected:
        ind = inp.indicator
        part = rows[rows["indicator_id"] == ind.id]
        annual = annuals[ind.id]
        trends = indicator_trends(annual, atolls, first, last)
        trend_parts.append(trends)
        files.append(plot_variable_panels(inp, annual, atolls, first, last, figures))
        classes = plot_condition_classes(inp, part, figures)
        if classes is not None:
            files.append(classes)
        files.append(plot_rescaled_by_year(inp, annual, trends, atolls, first, last, figures))
        files.append(plot_account_table(inp, part, norms[ind.id], figures))
        change = part.set_index("atoll")["rescaled_change"]
        has_map = bool(change.notna().any())
        if has_map:
            files.append(plot_change_map(inp, change, atolls_ll, part["opening"].iloc[0], part["closing"].iloc[0], figures))
        produced[ind.id] = {"classes": classes is not None, "map": has_map}
        if base_dir is not None and config.ecvs:
            results = ecv_comparison(config, Path(base_dir), annual, ind.id, atolls, first, last, log)
            if results:
                for res in results:
                    files.append(plot_ecv_by_atoll(inp, res, atolls, figures, 6))
                files.append(plot_ecv_forest(inp, results, atolls, figures, 7))
                files.append(plot_ecv_all_atolls(inp, results, style, figures, 8))
                compared[ind.id] = results
                total, by_atoll_part = comparison_tables(ind.id, results)
                totals.append(total)
                per_atoll.append(by_atoll_part)
        log(f"  {ind.id}: figures written")
    marked = sorted({y for op, cl in periods for y in (*op, *cl)})
    files.append(plot_combined(annuals, {i.indicator.id: i.indicator.name for i in selected}, atolls, first, last, marked, figures))
    annual_all = pd.concat(annuals.values(), ignore_index=True)
    comparison = pd.concat(totals, ignore_index=True) if totals else pd.DataFrame()
    by_atoll = pd.concat(per_atoll, ignore_index=True) if per_atoll else pd.DataFrame()
    workbooks = write_workbooks(folder, annual_all, pd.concat(trend_parts, ignore_index=True), rows, norms, comparison, by_atoll)
    files += workbooks
    files.append(write_report(folder, selected, rows, pd.concat(trend_parts, ignore_index=True), config, produced, comparison,
                              by_atoll, compared, first, last, [w.name for w in workbooks], annual_all))
    return files
