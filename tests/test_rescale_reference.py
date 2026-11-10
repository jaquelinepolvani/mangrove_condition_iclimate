"""Tests of rescaling and of the reference rule."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mangrove_condition.reference import CONFIGURATION, DATA_DERIVED, NONE, resolve_reference
from mangrove_condition.rescale import outside_range, rescale
from mangrove_condition.schema import Anchor, Direction, Indicator, ProjectSettings


def make_indicator(direction, lower=None, upper=None, opt_lower=None, opt_upper=None) -> Indicator:
    return Indicator(
        id="X", name="X", ect_class=None, unit=None, raster_file=None, level=None, direction=direction,
        ref_lower=lower, ref_upper=upper, optimum_lower=opt_lower, optimum_upper=opt_upper,
        in_index=True, weight=1.0, anchors=())


def project() -> ProjectSettings:
    """Default project settings (percentiles 5 and 95, at least 30 values)."""
    from mangrove_condition.config_reader import load_config
    from mangrove_condition.template import write_template
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        return load_config(write_template(f"{folder}/config.xlsx")).project


STANDARD = (Anchor(0, 0), Anchor(10, 1))
INVERSE = (Anchor(0, 1), Anchor(10, 0))
TWO_SIDED = (Anchor(0, 0), Anchor(4, 1), Anchor(6, 1), Anchor(10, 0))


def test_standard_is_linear_and_clipped():
    assert rescale([-5, 0, 2.5, 10, 20], STANDARD).tolist() == [0.0, 0.0, 0.25, 1.0, 1.0]


def test_inverse_flips():
    assert rescale([0, 2.5, 10], INVERSE).tolist() == [1.0, 0.75, 0.0]


def test_two_sided_has_a_plateau():
    assert rescale([0, 2, 4, 5, 6, 8, 10], TWO_SIDED).tolist() == [0.0, 0.5, 1.0, 1.0, 1.0, 0.5, 0.0]


def test_missing_stays_missing():
    result = rescale([np.nan, 5], STANDARD)
    assert np.isnan(result[0]) and result[1] == 0.5


def test_outside_range_counts_each_side():
    below, above = outside_range([-1, 0, 5, 10, 11, np.nan], STANDARD)
    assert below.tolist() == [True, False, False, False, False, False]
    assert above.tolist() == [False, False, False, False, True, False]


def test_two_anchors_with_same_value_and_different_score_are_rejected():
    with pytest.raises(ValueError):
        rescale([1], (Anchor(1, 0), Anchor(1, 1)))


def test_configuration_reference_comes_first():
    result = resolve_reference(make_indicator(Direction.STANDARD, 0, 1), pd.Series([5.0] * 100), project())
    assert result.source == CONFIGURATION and result.has_scale


def test_no_direction_means_no_score():
    result = resolve_reference(make_indicator(None), pd.Series(range(100), dtype=float), project())
    assert result.source == NONE and not result.has_scale


def test_two_sided_without_levels_is_not_derived_from_data():
    result = resolve_reference(make_indicator(Direction.TWO_SIDED), pd.Series(range(100), dtype=float), project())
    assert result.source == NONE and not result.has_scale


def test_percentile_reference_when_levels_are_missing():
    values = pd.Series(np.arange(101, dtype=float))
    result = resolve_reference(make_indicator(Direction.STANDARD), values, project())
    assert result.source == DATA_DERIVED
    assert result.value_low == pytest.approx(5.0) and result.value_high == pytest.approx(95.0)
    assert result.n_values == 101


def test_percentile_reference_needs_enough_values():
    result = resolve_reference(make_indicator(Direction.STANDARD), pd.Series(np.arange(10, dtype=float)), project())
    assert result.source == NONE and not result.has_scale
