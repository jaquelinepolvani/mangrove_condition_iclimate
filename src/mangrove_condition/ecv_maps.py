"""Maps of an ECV: continuous raster map of the whole Maldives and an atoll map coloured by the atoll mean."""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.patheffects as pe  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import rasterio  # noqa: E402
from matplotlib import colormaps  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

INK = "#222222"
MUTED = "#6b6b6b"
GRID = "#d9d9d9"
NO_MANGROVE_FILL = "#ececec"
NO_MANGROVE_EDGE = "#9a9a9a"
MANGROVE_EDGE = "#222222"

# Administrative regions of the Maldives (north, central, south), as drawn in the usual atoll maps.
REGIONS = {
    "N": ["Haa Alifu", "Haa Dhaalu", "Shaviyani", "Noonu", "Raa", "Baa", "Lhaviyani"],
    "C": ["Kaafu", "Alifu Alifu", "Alifu Dhaalu", "Vaavu", "Meemu", "Faafu", "Dhaalu", "Thaa", "Laamu"],
    "S": ["Gaafu Alifu", "Gaafu Dhaalu", "Gnaviyani", "Seenu"],
}
NO_LABEL = {"Male"}  # city polygon inside Kaafu
LABEL_SIDE = {"Shaviyani": "right"}  # keeps the name away from the small island group west of it
HEAD_IN = 0.75
TITLE_CHARS_PER_INCH = 8.0   # about how many characters of the bold title fit in one inch of figure width
TITLE_LINE_IN = 0.22         # extra header height for every additional title line


def split_name(name: str) -> tuple[str, str]:
    """'Variable (source)' into variable and source. Without parentheses the source is empty."""
    found = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", name)
    return (found.group(1), found.group(2)) if found else (name, "")


def axis_label(name: str, unit: str | None) -> str:
    """Label of a value axis or colour bar: the short variable name and the unit, e.g. 'Sea level (m)'.
    A bare unit such as 'm' is easily misread when it is turned sideways."""
    variable = split_name(name)[0].split(",")[0].strip()
    return f"{variable} ({_axis_unit(unit)})"


def title_lines(title: str, width_in: float) -> list[str]:
    """The title split into lines that fit the figure width, so that a long name is not cut at the edge."""
    return textwrap.wrap(title, width=max(int(width_in * TITLE_CHARS_PER_INCH), 10)) or [title]


def header_height(lines: list[str]) -> float:
    return HEAD_IN + TITLE_LINE_IN * (len(lines) - 1)


def color_limits(vmin: float, vmax: float, min_span: float | None = None) -> tuple[float, float]:
    """Limits of the colour scale. When the values differ less than min_span, the scale is widened around their
    middle, so that small differences are not shown as if they were large."""
    if min_span and (vmax - vmin) < min_span:
        middle = (vmin + vmax) / 2
        return middle - min_span / 2, middle + min_span / 2
    return vmin, vmax


def _axis_unit(unit: str | None) -> str:
    return "°C" if (unit or "") == "degC" else (unit or "")


def _degree_label(value: float, axis: str) -> str:
    hemi = ("E" if value >= 0 else "W") if axis == "x" else ("N" if value >= 0 else "S")
    return f"{abs(value):g}°{hemi}"


def _frame(fig, ax, extent, lines: list[str], subtitle: str, height: float) -> None:
    from matplotlib.ticker import FixedLocator

    west, east, south, north = extent
    ax.set_xlim(west, east)
    ax.set_ylim(south, north)
    ax.set_aspect(1.0)
    ax.set_xlabel("")
    ax.set_ylabel("")
    xs = np.arange(np.ceil(west), east, 1.0)
    ys = np.arange(np.ceil(south), north, 1.0)
    ax.xaxis.set_major_locator(FixedLocator(xs))
    ax.yaxis.set_major_locator(FixedLocator(ys))
    ax.set_xticklabels([_degree_label(v, "x") for v in xs], fontsize=8, color=MUTED)
    ax.set_yticklabels([_degree_label(v, "y") for v in ys], fontsize=8, color=MUTED)
    ax.grid(color=GRID, linewidth=0.5, linestyle=(0, (3, 3)), zorder=0)
    ax.set_axisbelow(True)
    for side in ax.spines.values():
        side.set_color(MUTED)
        side.set_linewidth(0.8)
    ax.tick_params(length=3, width=0.8, colors=MUTED)
    fig.text(0.02, 1 - 0.14 / height, "\n".join(lines), fontsize=12.5, fontweight="bold", color=INK, ha="left", va="top",
             linespacing=1.15)
    fig.text(0.02, 1 - (0.46 + TITLE_LINE_IN * (len(lines) - 1)) / height, subtitle, fontsize=9, color=MUTED,
             ha="left", va="top")


def _scale_bar(ax, extent, km: float = 50.0) -> None:
    west, east, south, north = extent
    lat = (south + north) / 2
    deg = km / (111.32 * np.cos(np.radians(lat)))
    x0, y0 = west + 0.08 * (east - west), south + 0.3
    half = deg / 2
    ax.add_patch(Rectangle((x0, y0), half, 0.05, facecolor="white", edgecolor=INK, linewidth=0.7, zorder=8))
    ax.add_patch(Rectangle((x0 + half, y0), half, 0.05, facecolor=INK, edgecolor=INK, linewidth=0.7, zorder=8))
    for x, label in ((x0, "0"), (x0 + half, f"{km / 2:g}"), (x0 + deg, f"{km:g} km")):
        ax.text(x, y0 + 0.09, label, ha="center", va="bottom", fontsize=7, color=INK, path_effects=_halo(), zorder=8)


def _north_arrow(ax, extent) -> None:
    west, east, south, north = extent
    x, y = east - 0.08 * (east - west), north - 0.28
    ax.annotate("", xy=(x, y), xytext=(x, y - 0.5), zorder=8,
                arrowprops={"arrowstyle": "-|>", "color": INK, "linewidth": 1.2, "mutation_scale": 12})
    ax.text(x, y + 0.05, "N", ha="center", va="bottom", fontsize=9, fontweight="bold", color=INK, zorder=8)


def _halo():
    return [pe.withStroke(linewidth=2.2, foreground="white")]


def _spread(values: list[float], gap: float) -> list[float]:
    """Moves sorted label heights apart so that neighbouring labels do not overlap."""
    order = np.argsort(values)
    out = np.array(values, dtype=float)
    for k in range(1, len(order)):
        if out[order[k]] - out[order[k - 1]] < gap:
            out[order[k]] = out[order[k - 1]] + gap
    return out.tolist()


def _largest_part(geometry):
    return max(geometry.geoms, key=lambda g: g.area) if geometry.geom_type == "MultiPolygon" else geometry


def _label_atolls(ax, polys: pd.DataFrame, names: list[str], middle_lon: float) -> None:
    """Names of the atolls, next to the largest part of each: west chain to the left, east chain to the right."""
    left, right = [], []
    for name in names:
        row = polys[polys["atoll"] == name]
        if row.empty:
            continue
        minx, miny, maxx, maxy = _largest_part(row.geometry.iloc[0]).bounds
        side = LABEL_SIDE.get(name, "left" if (minx + maxx) / 2 < middle_lon else "right")
        (left if side == "left" else right).append((name, minx, maxx, (miny + maxy) / 2))
    for side, items in (("left", left), ("right", right)):
        ys = _spread([i[3] for i in items], 0.2)
        for (name, minx, maxx, y0), y in zip(items, ys, strict=True):
            x = minx - 0.06 if side == "left" else maxx + 0.06
            ax.text(x, y, name, ha="right" if side == "left" else "left", va="center", fontsize=7, color=INK,
                    path_effects=_halo(), zorder=9)


def _region_lines(ax, polys: pd.DataFrame, west: float, east: float) -> None:
    """Dashed dividers and letters for north, central and south, placed between the regions' atolls."""
    bounds = polys.set_index("atoll").geometry.bounds
    centroids = (bounds["miny"] + bounds["maxy"]) / 2
    groups = {k: centroids.reindex(v).dropna() for k, v in REGIONS.items()}
    boundaries = [(groups["N"].min() + groups["C"].max()) / 2, (groups["C"].min() + groups["S"].max()) / 2]
    for y in boundaries:
        ax.axhline(y, color=NO_MANGROVE_EDGE, linewidth=0.8, linestyle=(0, (6, 4)), zorder=1)
    edges = [ax.get_ylim()[1], *boundaries, ax.get_ylim()[0]]
    for key, (top, bottom) in zip("NCS", zip(edges[:-1], edges[1:], strict=True), strict=True):
        ax.text(east - 0.1, (top + bottom) / 2, key, ha="right", va="center", fontsize=16,
                color=MUTED, zorder=2)


def read_stack(path: Path) -> tuple[np.ndarray, tuple[float, float, float, float], list[str]]:
    """All bands of a raster (annual bands) as a masked stack, its extent (west, east, south, north) and band names."""
    with rasterio.open(path) as src:
        if src.crs is None or src.crs.to_epsg() != 4326:
            raise ValueError(f"{Path(path).name}: the map code expects longitude and latitude (EPSG:4326).")
        data = src.read(masked=True)
        b = src.bounds
        names = [d or "" for d in src.descriptions]
    return data, (b.left, b.right, b.bottom, b.top), names


def _period_text(names: list[str]) -> str:
    years = [int(n) for n in names if n.strip().isdigit()]
    return f"{min(years)} to {max(years)}" if years else f"{len(names)} annual bands"


def plot_value_map(item, raster: Path, atolls_ll: pd.DataFrame, mangrove_atolls: list[str], cmap_name: str,
                   out: Path, min_span: float | None = None) -> Path:
    """Continuous map: mean of the annual bands for the whole raster, atoll outlines on top.

    min_span: smallest range of the colour scale (see color_limits)."""
    data, (west, east, south, north), names = read_stack(raster)
    mean = np.ma.masked_invalid(data.mean(axis=0))
    extent = (west - 0.1, east + 0.1, south - 0.1, north + 0.1)
    lines = title_lines(item.name, 4.4)
    head = header_height(lines)
    height = 9.0 + head
    vmin, vmax = color_limits(float(mean.min()), float(mean.max()), min_span)
    cmap = colormaps.get(cmap_name, colormaps["viridis"]).copy()
    with plt.rc_context({"font.family": "sans-serif", "font.sans-serif": ["Arial", "Liberation Sans", "DejaVu Sans"]}):
        fig, ax = plt.subplots(figsize=(4.4, height))
        image = ax.imshow(mean, extent=(west, east, south, north), origin="upper", cmap=cmap, interpolation="nearest",
                          vmin=vmin, vmax=vmax, zorder=2)
        for row in atolls_ll.itertuples():
            has = row.atoll in mangrove_atolls
            gdf = atolls_ll[atolls_ll["atoll"] == row.atoll]
            gdf.boundary.plot(ax=ax, color=MANGROVE_EDGE if has else "#ffffff", linewidth=1.1 if has else 0.5, zorder=4,
                              alpha=1.0 if has else 0.8)
        _frame(fig, ax, extent, lines, f"Mean of annual values, {_period_text(names)}", height)
        _scale_bar(ax, extent)
        _north_arrow(ax, extent)
        ax.set_anchor("W")
        fig.tight_layout(rect=(0, 0.03, 1, 1 - head / height))
        cbar = fig.colorbar(image, ax=ax, shrink=0.45, aspect=22, pad=0.02)
        cbar.set_label(axis_label(item.name, item.unit), fontsize=9, color=INK)
        cbar.ax.tick_params(labelsize=8, colors=MUTED)
        cbar.outline.set_edgecolor(MUTED)
        path = Path(out) / f"{item.id}_3_map_mean.png"
        fig.savefig(path, dpi=300, facecolor="white")
        (Path(out) / "vector").mkdir(exist_ok=True)
        fig.savefig(Path(out) / "vector" / f"{path.stem}.pdf", facecolor="white")
        plt.close(fig)
    return path


def plot_atoll_map(item, atoll_means: pd.Series, atolls_ll: pd.DataFrame, period: str, cmap_name: str, out: Path,
                   min_span: float | None = None) -> Path:
    """Atoll map: atolls with mangroves painted by the mean of the ECV, the other atolls in grey.

    min_span: smallest range of the colour scale (see color_limits)."""
    cmap = colormaps.get(cmap_name, colormaps["viridis"])
    norm = Normalize(*color_limits(float(atoll_means.min()), float(atoll_means.max()), min_span))
    west, south, east, north = atolls_ll.total_bounds
    extent = (west - 0.9, east + 0.9, south - 0.35, north + 0.35)
    lines = title_lines(item.name, 5.6)
    head = header_height(lines)
    height = 9.0 + head
    with plt.rc_context({"font.family": "sans-serif", "font.sans-serif": ["Arial", "Liberation Sans", "DejaVu Sans"]}):
        fig, ax = plt.subplots(figsize=(5.6, height))
        for row in atolls_ll.itertuples():
            has = row.atoll in atoll_means.index
            gdf = atolls_ll[atolls_ll["atoll"] == row.atoll]
            gdf.plot(ax=ax, facecolor=cmap(norm(atoll_means[row.atoll])) if has else NO_MANGROVE_FILL,
                     edgecolor=MANGROVE_EDGE if has else NO_MANGROVE_EDGE, linewidth=0.9 if has else 0.5, zorder=3)
        _frame(fig, ax, extent, lines, f"Atolls with mangroves coloured by the mean of annual values, {period}", height)
        _region_lines(ax, atolls_ll, extent[0], extent[1])
        _label_atolls(ax, atolls_ll, [a for a in atolls_ll["atoll"] if a not in NO_LABEL], 73.2)
        _scale_bar(ax, extent)
        _north_arrow(ax, extent)
        fig.tight_layout(rect=(0, 0.03, 1, 1 - head / height))
        sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
        cbar = fig.colorbar(sm, ax=ax, shrink=0.45, aspect=22, pad=0.02)
        cbar.set_label(axis_label(item.name, item.unit), fontsize=9, color=INK)
        cbar.ax.tick_params(labelsize=8, colors=MUTED)
        cbar.outline.set_edgecolor(MUTED)
        path = Path(out) / f"{item.id}_4_map_atolls.png"
        fig.savefig(path, dpi=300, facecolor="white")
        (Path(out) / "vector").mkdir(exist_ok=True)
        fig.savefig(Path(out) / "vector" / f"{path.stem}.pdf", facecolor="white")
        plt.close(fig)
    return path
