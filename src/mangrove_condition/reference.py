"""Reference levels of an indicator: from the configuration, or derived from the data."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .schema import Anchor, Direction, Indicator, ProjectSettings

CONFIGURATION = "configuration"
DATA_DERIVED = "data_derived"
NONE = "none"


@dataclass(frozen=True)
class ReferenceResult:
    """Scale used to rescale one indicator and where it comes from."""

    anchors: tuple[Anchor, ...] | None
    source: str                 # configuration, data_derived or none
    note: str                   # plain explanation, written to the outputs
    n_values: int | None = None
    percentile_low: float | None = None
    percentile_high: float | None = None
    value_low: float | None = None
    value_high: float | None = None

    @property
    def has_scale(self) -> bool:
        return self.anchors is not None


def resolve_reference(indicator: Indicator, values: pd.Series, project: ProjectSettings) -> ReferenceResult:
    """Reference levels: configuration first, then data derived percentiles, otherwise none."""
    if indicator.direction is None:
        return ReferenceResult(None, NONE, "No direction defined: variable reported, no score.")

    anchors = indicator.rescale_anchors()
    if anchors is not None:
        return ReferenceResult(anchors, CONFIGURATION, "Reference levels given in the configuration.")

    if indicator.direction not in (Direction.STANDARD, Direction.INVERSE):
        return ReferenceResult(
            None, NONE,
            f"Direction {indicator.direction.value} needs reference levels in the configuration: variable reported, no score.",
        )

    sample = pd.to_numeric(values, errors="coerce").dropna()
    n = int(sample.size)
    if n < project.fallback_min_sample:
        return ReferenceResult(
            None, NONE,
            f"No reference levels and only {n} values (minimum {project.fallback_min_sample}): variable reported, no score.",
            n_values=n,
        )
    low = float(np.percentile(sample, project.fallback_percentile_low))
    high = float(np.percentile(sample, project.fallback_percentile_high))
    if not low < high:
        return ReferenceResult(None, NONE, "The data derived reference levels are equal: variable reported, no score.", n_values=n)
    scores = (0.0, 1.0) if indicator.direction is Direction.STANDARD else (1.0, 0.0)
    note = (
        f"No reference levels in the configuration: percentiles {project.fallback_percentile_low:g} and "
        f"{project.fallback_percentile_high:g} of {n} values pooled over all years and units. "
        "Scores are relative to these data, not to an ecological reference."
    )
    return ReferenceResult(
        (Anchor(low, scores[0]), Anchor(high, scores[1])), DATA_DERIVED, note,
        n_values=n, percentile_low=project.fallback_percentile_low,
        percentile_high=project.fallback_percentile_high, value_low=low, value_high=high,
    )
