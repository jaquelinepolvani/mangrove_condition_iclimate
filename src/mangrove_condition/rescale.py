"""Rescaling of variables to indicator scores between 0 (worst) and 1 (best)."""

from __future__ import annotations

import numpy as np

from .schema import Anchor


def clean_anchors(anchors: tuple[Anchor, ...]) -> tuple[np.ndarray, np.ndarray]:
    """Sorted anchor values and scores. Repeated identical points are merged."""
    points = sorted({(a.value, a.score) for a in anchors})
    values = np.array([p[0] for p in points], dtype=float)
    scores = np.array([p[1] for p in points], dtype=float)
    if len(set(values.tolist())) != len(values):
        raise ValueError("Two anchors share a value but have different scores. Use a ramp, not a step.")
    return values, scores


def rescale(values, anchors: tuple[Anchor, ...]) -> np.ndarray:
    """Piecewise linear score of each value. Missing values stay missing.

    Values beyond the first or last anchor take the score of that anchor.
    """
    xs, ys = clean_anchors(anchors)
    array = np.asarray(values, dtype=float)
    scores = np.interp(array, xs, ys)
    scores[np.isnan(array)] = np.nan
    return scores


def outside_range(values, anchors: tuple[Anchor, ...]) -> tuple[np.ndarray, np.ndarray]:
    """Boolean arrays: value below the first anchor, value above the last anchor."""
    xs, _ = clean_anchors(anchors)
    array = np.asarray(values, dtype=float)
    with np.errstate(invalid="ignore"):
        return array < xs[0], array > xs[-1]
