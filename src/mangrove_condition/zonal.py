"""Raster values per spatial unit, without resampling the rasters.

Mangrove patches: area weighted mean. Each pixel counts in proportion to the share
of the pixel covered by the patch, so patches smaller than a pixel still get a value.

Atoll indicators: mean of the pixels whose centre lies inside the atoll polygon
enlarged by a buffer (the same rule used for the salinity extraction).
"""

from __future__ import annotations

import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
import shapely
from rasterio.windows import Window, from_bounds

TABLE_COLUMNS = ["unit_id", "year", "value", "valid_share", "n_pixels"]


class RasterError(Exception):
    """Raised when a raster cannot be used."""


def band_years(path: Path, first_band_year: int | None = None) -> list[int]:
    """Year of each band: from the band_years tag, the band names, or first_band_year."""
    with rasterio.open(path) as src:
        count = src.count
        tag = src.tags().get("band_years")
        if tag:
            pairs = re.findall(r"(\d+)\s*=\s*(\d{4})", tag)
            mapping = {int(band): int(year) for band, year in pairs}
            if set(mapping) == set(range(1, count + 1)):
                return [mapping[band] for band in range(1, count + 1)]
        names = [d or "" for d in src.descriptions]
        matches = [re.search(r"((?:19|20)\d{2})\s*$", name) for name in names]
        if all(matches):
            return [int(m.group(1)) for m in matches]
    if first_band_year is not None:
        return [first_band_year + i for i in range(count)]
    raise RasterError(
        f"{Path(path).name}: the band years are not stored in the file. "
        "Fill 'first_band_year' in the Indicators sheet (year of band 1; bands must be consecutive years)."
    )


def _window_for(src, bounds) -> Window | None:
    window = from_bounds(*bounds, transform=src.transform)
    window = window.round_offsets(op="floor").round_lengths(op="ceil")
    window = Window(window.col_off - 1, window.row_off - 1, window.width + 2, window.height + 2)
    full = Window(0, 0, src.width, src.height)
    try:
        return window.intersection(full)
    except Exception:
        return None


def _check_north_up(src, path: Path) -> None:
    t = src.transform
    if t.b != 0 or t.d != 0:
        raise RasterError(f"{Path(path).name}: rotated rasters are not supported.")


def patch_table(path: Path, patches: gpd.GeoDataFrame, years: list[int]) -> pd.DataFrame:
    """Area weighted mean of every band inside each patch.

    Returns unit_id, year, value, valid_share, n_pixels. valid_share is the share of the
    patch area covered by valid pixels. n_pixels is the patch area in pixel units.
    """
    rows = []
    with rasterio.open(path) as src:
        _check_north_up(src, path)
        if src.count != len(years):
            raise RasterError(f"{Path(path).name}: {src.count} bands but {len(years)} years.")
        projected = patches.to_crs(src.crs)
        for patch_id, geometry in zip(projected["patch_id"], projected.geometry, strict=True):
            window = _window_for(src, geometry.bounds)
            if window is None or window.width <= 0 or window.height <= 0:
                rows += [(patch_id, year, np.nan, 0.0, 0.0) for year in years]
                continue
            data = src.read(window=window, masked=True)
            transform = src.window_transform(window)
            height, width = data.shape[1:]
            xs = transform.c + transform.a * np.arange(width + 1)
            ys = transform.f + transform.e * np.arange(height + 1)
            x0, x1 = np.minimum(xs[:-1], xs[1:]), np.maximum(xs[:-1], xs[1:])
            y0, y1 = np.minimum(ys[:-1], ys[1:]), np.maximum(ys[:-1], ys[1:])
            boxes = shapely.box(
                np.broadcast_to(x0[None, :], (height, width)),
                np.broadcast_to(y0[:, None], (height, width)),
                np.broadcast_to(x1[None, :], (height, width)),
                np.broadcast_to(y1[:, None], (height, width)),
            )
            pixel_area = abs(transform.a * transform.e)
            cover = np.clip(shapely.area(shapely.intersection(boxes, geometry)) / pixel_area, 0.0, 1.0)
            total = cover.sum()
            for index, year in enumerate(years):
                band = data[index]
                valid = ~np.ma.getmaskarray(band) & np.isfinite(band.filled(np.nan).astype(float))
                weights = np.where(valid, cover, 0.0)
                weight_sum = weights.sum()
                if total <= 0 or weight_sum <= 0:
                    rows.append((patch_id, year, np.nan, 0.0, float(total)))
                    continue
                values = np.where(valid, band.filled(0.0).astype(float), 0.0)
                rows.append((patch_id, year, float((values * weights).sum() / weight_sum),
                             float(weight_sum / total), float(total)))
    return pd.DataFrame(rows, columns=TABLE_COLUMNS)


def atoll_table(path: Path, atolls: gpd.GeoDataFrame, years: list[int], buffer_km: float) -> pd.DataFrame:
    """Mean of the pixels whose centre lies inside each atoll polygon enlarged by a buffer.

    valid_share compares the pixels with a value in a year with the pixels that have a value
    in at least one year. This way cells that are always empty (for example land cells of an
    ocean model) do not count as missing data.
    """
    rows = []
    with rasterio.open(path) as src:
        _check_north_up(src, path)
        if src.count != len(years):
            raise RasterError(f"{Path(path).name}: {src.count} bands but {len(years)} years.")
        buffered = atolls.copy()
        buffered["geometry"] = atolls.geometry.buffer(buffer_km * 1000.0)
        buffered = buffered.to_crs(src.crs)
        for atoll, geometry in zip(buffered["atoll"], buffered.geometry, strict=True):
            window = _window_for(src, geometry.bounds)
            if window is None or window.width <= 0 or window.height <= 0:
                rows += [(atoll, year, np.nan, 0.0, 0.0) for year in years]
                continue
            data = src.read(window=window, masked=True)
            transform = src.window_transform(window)
            height, width = data.shape[1:]
            cols, rws = np.meshgrid(np.arange(width), np.arange(height))
            px = transform.c + transform.a * (cols + 0.5)
            py = transform.f + transform.e * (rws + 0.5)
            inside = shapely.contains_xy(geometry, px, py)
            valid = ~np.ma.getmaskarray(data) & np.isfinite(data.filled(np.nan).astype(float))
            valid &= inside[None, :, :]
            ever = valid.any(axis=0)
            reference = int(ever.sum())
            for index, year in enumerate(years):
                n_valid = int(valid[index].sum())
                if n_valid == 0 or reference == 0:
                    rows.append((atoll, year, np.nan, 0.0, float(reference)))
                    continue
                value = float(data[index].filled(0.0).astype(float)[valid[index]].mean())
                rows.append((atoll, year, value, n_valid / reference, float(reference)))
    return pd.DataFrame(rows, columns=TABLE_COLUMNS)
