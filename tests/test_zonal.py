"""Tests of the zonal statistics on small rasters with known values."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from mangrove_condition.zonal import RasterError, atoll_table, band_years, patch_table


def write(path, arrays, nodata=-9999.0, pixel=0.01, west=0.0, north=0.01, tags=None):
    data = np.stack(arrays).astype("float32")
    with rasterio.open(path, "w", driver="GTiff", height=data.shape[1], width=data.shape[2], count=data.shape[0],
                       dtype="float32", crs="EPSG:4326", transform=from_origin(west, north, pixel, pixel),
                       nodata=nodata) as dst:
        dst.write(data)
        if tags:
            dst.update_tags(**tags)


def patches(geometry):
    return gpd.GeoDataFrame({"patch_id": ["P1"]}, geometry=[geometry], crs="EPSG:4326")


def test_partly_covered_pixel_counts_by_its_share(tmp_path):
    path = tmp_path / "r.tif"
    write(path, [np.array([[0.0, 1.0]])])           # pixel 1 value 0, pixel 2 value 1
    table = patch_table(path, patches(box(0.005, 0.0, 0.02, 0.01)), [2020])
    row = table.iloc[0]
    assert row["value"] == pytest.approx((0 * 0.5 + 1 * 1.0) / 1.5)
    assert row["valid_share"] == pytest.approx(1.0)


def test_nodata_lowers_valid_share_not_the_mean(tmp_path):
    path = tmp_path / "r.tif"
    write(path, [np.array([[2.0, -9999.0]])])
    row = patch_table(path, patches(box(0.0, 0.0, 0.02, 0.01)), [2020]).iloc[0]
    assert row["value"] == pytest.approx(2.0)
    assert row["valid_share"] == pytest.approx(0.5)


def test_patch_outside_the_raster_is_missing(tmp_path):
    path = tmp_path / "r.tif"
    write(path, [np.array([[1.0, 1.0]])])
    row = patch_table(path, patches(box(5.0, 5.0, 5.01, 5.01)), [2020]).iloc[0]
    assert np.isnan(row["value"]) and row["valid_share"] == 0.0


def test_band_count_must_match_years(tmp_path):
    path = tmp_path / "r.tif"
    write(path, [np.array([[1.0]]), np.array([[2.0]])])
    with pytest.raises(RasterError):
        patch_table(path, patches(box(0.0, 0.0, 0.01, 0.01)), [2020])


def test_band_years_from_first_band_year(tmp_path):
    path = tmp_path / "r.tif"
    write(path, [np.array([[1.0]])] * 3)
    assert band_years(path, first_band_year=2017) == [2017, 2018, 2019]


def test_atoll_table_ignores_cells_that_are_never_valid(tmp_path):
    path = tmp_path / "r.tif"
    # 4 cells, one never valid (land). Year 1: 3 valid cells, year 2: 2 valid cells.
    write(path, [np.array([[1.0, 2.0], [3.0, -9999.0]]), np.array([[1.0, 2.0], [-9999.0, -9999.0]])],
          pixel=0.5, north=1.0)
    atolls = gpd.GeoDataFrame({"atoll": ["A"]}, geometry=[box(0.0, 0.0, 1.0, 1.0)], crs="EPSG:4326")
    table = atoll_table(path, atolls, [2020, 2021], buffer_km=0.0)
    first, second = table.iloc[0], table.iloc[1]
    assert first["value"] == pytest.approx(2.0) and first["valid_share"] == pytest.approx(1.0)
    assert second["value"] == pytest.approx(1.5) and second["valid_share"] == pytest.approx(2 / 3)
