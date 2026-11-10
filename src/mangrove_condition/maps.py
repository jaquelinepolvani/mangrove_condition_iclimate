"""Maps of the scores and of the composite index.

For every scored indicator and for the composite index, three atoll maps are drawn: opening, closing and change.
Indicators marked island_map in the configuration also get island maps: a grid of panels, one per atoll,
zoomed on the atoll, with one point per island.
Atoll maps show the whole country with the atoll polygons coloured by value.
Scores use a continuous 0 to 1 scale, changes a diverging scale centred on zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib import colormaps, patheffects  # noqa: E402
from matplotlib.cm import ScalarMappable  # noqa: E402
from matplotlib.colors import Normalize  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from .account import Accounts, IndicatorInput  # noqa: E402
from .schema import Config, Level  # noqa: E402
from .units import Units, study_atoll_names  # noqa: E402

GREY_FILL = "#e6e6e6"
GREY_EDGE = "#a8a8a8"
GREY_TEXT = "#6e6e6e"
DARK_TEXT = "#1a1a1a"
NO_DATA = "#ffffff"
CHANGE_CMAP = "RdBu"
PERIODS = {"open": "opening", "close": "closing", "change": "change"}
LABEL_OFFSETS = [(0, 9, "center"), (0, -14, "center"), (9, -3, "left"), (-9, -3, "right")]
HALO = [patheffects.withStroke(linewidth=2, foreground="white")]


@dataclass(frozen=True)
class Product:
    """One mapped quantity: an indicator score or the composite index."""

    key: str
    title: str
    kind: str                       # island (one point per island) or atoll (atoll polygons)
    values: dict[str, pd.Series]    # open, close, change -> series indexed by unit id
    periods: dict[str, str]         # open, close -> text such as "2017"
    note: str = ""                  # footnote written under the figure


def _colormap(name: str, notes: list[str]):
    try:
        return colormaps[name]
    except KeyError:
        notes.append(f"Colour map '{name}' not found: viridis used.")
        return colormaps["viridis"]


def _period_text(years: tuple[int, ...]) -> str:
    return ", ".join(str(y) for y in years)


def _index_note(config: Config, inputs: list[IndicatorInput], index: pd.DataFrame) -> str:
    names = [i.indicator.name for i in inputs if i.reference.has_scale and i.indicator.in_index]
    text = f"Composite index (mean of the available indicator scores): {', '.join(names)}."
    if index["partial"].any():
        text += " Units differ in the indicators available."
    if index["mixed_periods"].any():
        text += " Some units combine indicators with different accounting periods."
    return text


def build_products(config: Config, inputs: list[IndicatorInput], accounts: Accounts) -> list[Product]:
    """Atoll maps for every scored indicator and for the index; island maps where the configuration asks for them."""
    long, index = accounts.long, accounts.index
    products = []
    for inp in inputs:
        if not inp.reference.has_scale:
            continue
        kinds = ["atoll"]
        if inp.indicator.island_map and inp.indicator.level is Level.MANGROVE:
            kinds.append("island")
        for kind in kinds:
            sub = long[(long["level"] == kind) & (long["indicator_id"] == inp.indicator.id)].set_index("unit_id")
            products.append(Product(
                key=f"{inp.indicator.id}_{kind}", title=f"{inp.indicator.name} (score), {kind} level", kind=kind,
                values={"open": sub["s_open"], "close": sub["s_close"], "change": sub["s_change"]},
                periods={"open": _period_text(inp.opening_years), "close": _period_text(inp.closing_years)},
            ))
    sub = index[index["level"] == "atoll"].set_index("unit_id")
    products.append(Product(
        key="INDEX_atoll", title="Composite index, atoll level", kind="atoll",
        values={"open": sub["index_open"], "close": sub["index_close"], "change": sub["index_change"]},
        periods={"open": _period_text(config.project.opening_years), "close": _period_text(config.project.closing_years)},
        note=_index_note(config, inputs, index[index["level"] == "atoll"]),
    ))
    return products


def _scale(product: Product, tag: str, cmap) -> tuple:
    """Colour map and normalisation: 0 to 1 for scores, symmetric around zero for changes."""
    if tag != "change":
        return cmap, Normalize(0, 1)
    changes = product.values["change"].to_numpy(dtype=float)
    largest = float(np.nanmax(np.abs(changes))) if np.isfinite(changes).any() else 0.0
    limit = max(0.05, np.ceil(largest * 20) / 20)
    return colormaps[CHANGE_CMAP], Normalize(-limit, limit)


def _heading(product: Product, tag: str) -> str:
    if tag == "change":
        return f"{product.title}, change ({product.periods['open']} to {product.periods['close']})"
    return f"{product.title}, {PERIODS[tag]}: {product.periods[tag]}"


def _colorbar(fig, axes, cmap, norm, tag: str, **kwargs) -> None:
    label = "change: closing minus opening" if tag == "change" else "score: 0 = worst, 1 = best"
    fig.colorbar(ScalarMappable(norm=norm, cmap=cmap), ax=axes, location="bottom", label=label, **kwargs)


def _footer(fig, product: Product, any_missing: bool) -> None:
    parts = [product.note] if product.note else []
    if any_missing:
        parts.append("White circle or white polygon: no data.")
    if parts:
        fig.supxlabel(" ".join(parts), fontsize=7, color=GREY_TEXT)


def _clean(ax) -> None:
    ax.set_aspect("equal")
    ax.set_xlabel("")
    ax.set_ylabel("")
    ax.set_xticks([])
    ax.set_yticks([])
    for spine in ax.spines.values():
        spine.set_visible(False)


def _base(ax, atolls: gpd.GeoDataFrame, label_all: bool = True) -> None:
    atolls.plot(ax=ax, facecolor=GREY_FILL, edgecolor=GREY_EDGE, linewidth=0.5, zorder=1)
    if not label_all:
        return
    for name, point in zip(atolls["atoll"], atolls.geometry.representative_point(), strict=True):
        ax.annotate(name, (point.x, point.y), ha="center", va="center", fontsize=6, color=DARK_TEXT, zorder=6,
                    path_effects=HALO)


def _scatter(ax, points: gpd.GeoDataFrame, values: pd.Series, cmap, norm: Normalize, size: float) -> bool:
    """Draws one point per island; returns True if some islands have no value."""
    got = values.reindex(points["island_id"]).to_numpy(dtype=float)
    has = ~np.isnan(got)
    xs, ys = points.geometry.x.to_numpy(), points.geometry.y.to_numpy()
    if (~has).any():
        ax.scatter(xs[~has], ys[~has], s=size, facecolor=NO_DATA, edgecolor=GREY_TEXT, linewidth=0.6, zorder=4)
    if has.any():
        ax.scatter(xs[has], ys[has], s=size, c=got[has], cmap=cmap, norm=norm, edgecolor=DARK_TEXT, linewidth=0.5, zorder=5)
    return bool((~has).any())


def _atoll_cell(ax, atoll: str, atolls4326: gpd.GeoDataFrame, islands4326: gpd.GeoDataFrame, values: pd.Series,
                cmap, norm: Normalize) -> bool:
    subset = islands4326[islands4326["atoll"] == atoll]
    _base(ax, atolls4326[atolls4326["atoll"] == atoll], label_all=False)
    missing = _scatter(ax, subset, values, cmap, norm, 110)
    xs, ys = subset.geometry.x.to_numpy(), subset.geometry.y.to_numpy()
    span = max(xs.max() - xs.min(), ys.max() - ys.min())
    half = span / 2 + max(0.05, 0.25 * span)
    ax.set_xlim((xs.max() + xs.min()) / 2 - half, (xs.max() + xs.min()) / 2 + half)
    ax.set_ylim((ys.max() + ys.min()) / 2 - half, (ys.max() + ys.min()) / 2 + half)
    ranked = sorted(zip(subset["island"], xs, ys, strict=True), key=lambda r: (r[1], r[2]))
    for rank, (name, x, y) in enumerate(ranked):
        dx, dy, align = LABEL_OFFSETS[rank % len(LABEL_OFFSETS)]
        ax.annotate(name, (x, y), xytext=(dx, dy), textcoords="offset points", ha=align, fontsize=7,
                    color=DARK_TEXT, zorder=6, path_effects=HALO)
    _clean(ax)
    ax.set_title(atoll, fontsize=10)
    return missing


def island_panels(product: Product, tag: str, names: list[str], atolls4326: gpd.GeoDataFrame,
                  islands4326: gpd.GeoDataFrame, cmap, out: Path) -> Path:
    """Island level results: one panel per atoll, one point per island."""
    colormap, norm = _scale(product, tag, cmap)
    fig, axes = plt.subplots(2, 4, figsize=(16, 9), squeeze=False, layout="constrained")
    flat = axes.ravel()
    any_missing = False
    for ax, atoll in zip(flat, names, strict=False):
        any_missing = _atoll_cell(ax, atoll, atolls4326, islands4326, product.values[tag], colormap, norm) or any_missing
    for ax in flat[len(names):]:
        ax.axis("off")
    _colorbar(fig, flat.tolist(), colormap, norm, tag, shrink=0.35, aspect=40)
    _footer(fig, product, any_missing)
    fig.suptitle(_heading(product, tag), fontsize=12)
    path = Path(out) / f"map_{product.key}_{PERIODS[tag]}.png"
    fig.savefig(path, dpi=170, bbox_inches="tight")
    plt.close(fig)
    return path


def atoll_map(product: Product, tag: str, atolls4326: gpd.GeoDataFrame, cmap, out: Path) -> Path:
    """Atoll level results: the whole country with the atoll polygons coloured by value."""
    colormap, norm = _scale(product, tag, cmap)
    fig, ax = plt.subplots(figsize=(6.5, 12), layout="constrained")
    xmin, ymin, xmax, ymax = atolls4326.total_bounds
    _base(ax, atolls4326)
    colored = atolls4326.copy()
    colored["v"] = colored["atoll"].map(product.values[tag])
    colored[colored["v"].notna()].plot(ax=ax, column="v", cmap=colormap, norm=norm, edgecolor=DARK_TEXT, linewidth=0.6, zorder=3)
    missing = colored[colored["has_mangrove"] & colored["v"].isna()]
    if len(missing):
        missing.plot(ax=ax, facecolor=NO_DATA, edgecolor=GREY_TEXT, linewidth=0.6, zorder=3)
    ax.set_xlim(xmin - 0.2, xmax + 0.2)
    ax.set_ylim(ymin - 0.2, ymax + 0.2)
    _clean(ax)
    _colorbar(fig, ax, colormap, norm, tag, shrink=0.7, aspect=30)
    _footer(fig, product, bool(len(missing)))
    fig.suptitle(_heading(product, tag), fontsize=11)
    path = Path(out) / f"map_{product.key}_{PERIODS[tag]}.png"
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)
    return path


def make_maps(config: Config, units: Units, inputs: list[IndicatorInput], accounts: Accounts, out_dir: Path,
              notes: list[str]) -> list[Path]:
    folder = Path(out_dir) / "maps"
    folder.mkdir(parents=True, exist_ok=True)
    cmap = _colormap(config.project.map_colormap, notes)
    atolls4326 = units.atolls.to_crs("EPSG:4326")
    islands4326 = units.islands.to_crs("EPSG:4326")
    names = study_atoll_names(units)
    written = []
    for product in build_products(config, inputs, accounts):
        for tag in ("open", "close", "change"):
            if product.kind == "island":
                written.append(island_panels(product, tag, names, atolls4326, islands4326, cmap, folder))
            else:
                written.append(atoll_map(product, tag, atolls4326, cmap, folder))
    return written
