"""Condition accounts: variable account, indicator account and index account.

Design choices, written to the run record:
 * Period value = mean of the yearly values of the period (years without a value are skipped, not filled).
 * Score = rescaled period value, computed at the finest unit where the variable exists
   (patch, or atoll for atoll indicators).
 * Island and atoll values and scores = area weighted means of the patch results.
 * Atoll indicators are repeated in every island and patch of the atoll (source_level = atoll).
 * Index = weighted mean of the available scores (equal weights by default). It is labelled partial
   when indicators of the index are missing.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .reference import ReferenceResult
from .rescale import rescale
from .schema import Config, Indicator, Level
from .units import Units, study_atoll_names

LEVELS = ("patch", "island", "atoll")
METRICS = ["v_open", "v_close", "s_open", "s_close"]


@dataclass
class IndicatorInput:
    indicator: Indicator
    table: pd.DataFrame          # unit_id, year, value, valid_share, n_pixels
    reference: ReferenceResult
    opening_years: tuple[int, ...]
    closing_years: tuple[int, ...]


@dataclass
class Accounts:
    long: pd.DataFrame           # one row per level, unit and indicator
    index: pd.DataFrame          # one row per level and unit
    unit_names: pd.DataFrame


def _period(table: pd.DataFrame, years: tuple[int, ...]) -> pd.DataFrame:
    sub = table[table["year"].isin(years) & table["value"].notna()]
    return sub.groupby("unit_id")["value"].agg(mean="mean", n="count")


def native_frame(inp: IndicatorInput, native_ids: list[str]) -> pd.DataFrame:
    """Opening and closing values and scores at the unit where the variable exists."""
    frame = pd.DataFrame(index=pd.Index(native_ids, name="unit_id"))
    for tag, years in (("open", inp.opening_years), ("close", inp.closing_years)):
        period = _period(inp.table, years)
        frame[f"v_{tag}"] = period["mean"].reindex(frame.index)
        frame[f"n_years_{tag}"] = period["n"].reindex(frame.index)
        if inp.reference.has_scale:
            frame[f"s_{tag}"] = rescale(frame[f"v_{tag}"].to_numpy(), inp.reference.anchors)
        else:
            frame[f"s_{tag}"] = np.nan
    return frame


def _aggregate(native: pd.DataFrame, area: pd.Series, group: pd.Series) -> pd.DataFrame:
    """Area weighted mean of the patch results by group, with the share of area that has a value."""
    out = {}
    total = area.groupby(group).sum()
    for column in METRICS:
        ok = native[column].notna()
        numerator = (native[column] * area).where(ok).groupby(group).sum(min_count=1)
        denominator = area.where(ok).groupby(group).sum(min_count=1)
        out[column] = numerator / denominator
        tag = column.split("_", 1)[1]
        if column.startswith("v_"):
            out[f"cover_{tag}"] = (denominator.reindex(total.index) / total).fillna(0.0)
            out[f"n_patches_{tag}"] = ok.groupby(group).sum()
    frame = pd.DataFrame(out)
    frame.index.name = "unit_id"
    return frame


def unit_names(units: Units) -> pd.DataFrame:
    """Names and parents of every unit of every level."""
    patches = units.patches.assign(level="patch", unit_id=units.patches["patch_id"], unit_name=units.patches["patch_id"])
    islands = units.islands.assign(level="island", unit_id=units.islands["island_id"], unit_name=units.islands["island"])
    atolls = units.atolls[units.atolls["has_mangrove"]].assign(
        level="atoll", unit_id=lambda d: d["atoll"], unit_name=lambda d: d["atoll"], island=np.nan)
    columns = ["level", "unit_id", "unit_name", "atoll", "island"]
    return pd.concat([patches[columns], islands[columns], atolls[columns]], ignore_index=True)


def build_long(inputs: list[IndicatorInput], units: Units) -> pd.DataFrame:
    """Values and scores of every indicator at patch, island and atoll level."""
    area = units.patches.set_index("patch_id")["area_m2"]
    patch_island = units.patches.set_index("patch_id")["island_id"]
    patch_atoll = units.patches.set_index("patch_id")["atoll"]
    island_atoll = units.islands.set_index("island_id")["atoll"]
    atolls = study_atoll_names(units)
    frames = []
    for inp in inputs:
        indicator = inp.indicator
        if indicator.level is Level.MANGROVE:
            native = native_frame(inp, units.patches["patch_id"].tolist())
            by_level = {
                "patch": native,
                "island": _aggregate(native, area, patch_island),
                "atoll": _aggregate(native, area, patch_atoll),
            }
            source = "patch"
        else:
            native = native_frame(inp, atolls)
            by_level = {
                "atoll": native,
                "island": native.reindex(island_atoll.to_numpy()).set_axis(island_atoll.index),
                "patch": native.reindex(patch_atoll.to_numpy()).set_axis(patch_atoll.index),
            }
            for frame in by_level.values():
                frame.index.name = "unit_id"
            source = "atoll"
        for level, frame in by_level.items():
            part = frame.reset_index()
            part.insert(0, "level", level)
            part.insert(2, "indicator_id", indicator.id)
            part.insert(3, "source_level", source)
            frames.append(part)
    long = pd.concat(frames, ignore_index=True)
    for column in ("n_years_open", "n_years_close", "cover_open", "cover_close", "n_patches_open", "n_patches_close"):
        if column not in long.columns:
            long[column] = np.nan
    long["v_change"] = long["v_close"] - long["v_open"]
    long["s_change"] = long["s_close"] - long["s_open"]
    return long


def build_index(config: Config, inputs: list[IndicatorInput], long: pd.DataFrame, units: Units) -> pd.DataFrame:
    """Weighted mean of the available scores for every unit and level."""
    eligible = [i.id for i in config.indicators if i.in_index]
    weights = {i.id: i.weight for i in config.indicators}
    periods = {inp.indicator.id: (inp.opening_years, inp.closing_years) for inp in inputs}
    names = unit_names(units)
    results = []
    for level in LEVELS:
        ids = names.loc[names["level"] == level, "unit_id"].tolist()
        part = long[(long["level"] == level) & long["indicator_id"].isin(eligible)]
        cols = [i for i in eligible if i in set(part["indicator_id"])]
        wide = {tag: part.pivot(index="unit_id", columns="indicator_id", values=f"s_{tag}").reindex(index=ids, columns=cols)
                for tag in ("open", "close")}
        w = pd.Series({c: weights[c] for c in cols}, dtype=float)
        frame = pd.DataFrame(index=pd.Index(ids, name="unit_id"))
        used = {}
        for tag in ("open", "close"):
            scores = wide[tag]
            mask = scores.notna()
            den = (mask * w).sum(axis=1)
            frame[f"index_{tag}"] = ((scores.fillna(0.0) * w).sum(axis=1) / den).where(den > 0)
            frame[f"n_used_{tag}"] = mask.sum(axis=1)
            used[tag] = mask
            frame[f"used_{tag}"] = mask.apply(lambda row: ";".join(c for c in cols if row[c]), axis=1)
        common = used["open"] & used["close"]
        den_c = (common * w).sum(axis=1)
        open_c = ((wide["open"].fillna(0.0) * common * w).sum(axis=1) / den_c).where(den_c > 0)
        close_c = ((wide["close"].fillna(0.0) * common * w).sum(axis=1) / den_c).where(den_c > 0)
        frame["index_change"] = close_c - open_c
        frame["n_common"] = common.sum(axis=1)
        either = used["open"] | used["close"]
        # partial: an eligible indicator is missing in the opening or in the closing period
        frame["not_used"] = common.apply(lambda row: ";".join(c for c in eligible if c not in cols or not row.get(c, False)), axis=1)
        frame["partial"] = frame["not_used"] != ""
        frame["mixed_periods"] = either.apply(
            lambda row: len({periods[c] for c in cols if row[c]}) > 1, axis=1)
        frame.insert(0, "level", level)
        results.append(frame.reset_index())
    index = pd.concat(results, ignore_index=True)
    return index.merge(names[["level", "unit_id", "unit_name", "atoll", "island"]], on=["level", "unit_id"], how="left")


def build_accounts(config: Config, inputs: list[IndicatorInput], units: Units) -> Accounts:
    long = build_long(inputs, units)
    index = build_index(config, inputs, long, units)
    return Accounts(long=long, index=index, unit_names=unit_names(units))


def collinearity(config: Config, inputs: list[IndicatorInput], units: Units) -> pd.DataFrame:
    """Spearman correlation between the variables of different indicators. Nothing is removed."""
    threshold = config.project.collinearity_threshold
    area = units.patches.set_index("patch_id")
    rows = []

    def add(scope: str, wide: pd.DataFrame) -> None:
        ids = list(wide.columns)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if frozenset((a, b)) in parent_child:
                    continue  # a composite and its own components are related by construction
                pair = wide[[a, b]].dropna()
                n = len(pair)
                rho = pair[a].corr(pair[b], method="spearman") if n >= 10 else np.nan
                rows.append({"scope": scope, "indicator_a": a, "indicator_b": b, "spearman": rho, "n_pairs": n,
                             "flagged": bool(abs(rho) > threshold) if not np.isnan(rho) else False})

    parent_child = {frozenset((item.id, part)) for item in config.indicators for part in item.components}
    patch_cols = {}
    for inp in inputs:
        if inp.indicator.level is Level.MANGROVE:
            t = inp.table.dropna(subset=["value"]).set_index(["unit_id", "year"])["value"]
            patch_cols[inp.indicator.id] = t
    if len(patch_cols) > 1:
        add("patch-year", pd.DataFrame(patch_cols))

    atoll_cols = {}
    for inp in inputs:
        t = inp.table.dropna(subset=["value"])
        if inp.indicator.level is Level.MANGROVE:
            t = t.merge(area[["atoll", "area_m2"]], left_on="unit_id", right_index=True)
            t = t.assign(weighted=t["value"] * t["area_m2"])
            grouped = t.groupby(["atoll", "year"]).agg(w=("weighted", "sum"), a=("area_m2", "sum"))
            atoll_cols[inp.indicator.id] = grouped["w"] / grouped["a"]
        else:
            atoll_cols[inp.indicator.id] = t.rename(columns={"unit_id": "atoll"}).set_index(["atoll", "year"])["value"]
    if len(atoll_cols) > 1:
        add("atoll-year", pd.DataFrame(atoll_cols))
    return pd.DataFrame(rows, columns=["scope", "indicator_a", "indicator_b", "spearman", "n_pairs", "flagged"])
