"""Tiny synthetic project used by the pipeline tests: 2 atolls, 3 patches, 2 rasters with known values."""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from openpyxl import load_workbook
from rasterio.transform import from_origin
from shapely.geometry import box

from mangrove_condition.template import write_template

YEARS = list(range(2017, 2026))


def write_raster(path: Path, west: float, north: float, pixel: float, shape: tuple[int, int],
                 band_values: list[float], nodata: float = -9999.0) -> None:
    height, width = shape
    data = np.stack([np.full((height, width), v, dtype="float32") for v in band_values])
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", driver="GTiff", height=height, width=width, count=len(band_values),
                       dtype="float32", crs="EPSG:4326", transform=from_origin(west, north, pixel, pixel),
                       nodata=nodata) as dst:
        dst.write(data)


def ndvi_value(year: int) -> float:
    return 0.1 + 0.05 * (year - 2017)


def salinity_value(year: int) -> float:
    return 34.0 + 0.1 * (year - 2017)


def make_project(root: Path) -> Path:
    """Write data and configuration under root. Returns the configuration path."""
    atolls = gpd.GeoDataFrame(
        {"adm1_name": ["Atoll A", "Atoll B"]},
        geometry=[box(73.00, 5.00, 73.10, 5.10), box(73.30, 5.00, 73.40, 5.10)], crs="EPSG:4326")
    (root / "data/atolls").mkdir(parents=True)
    atolls.to_file(root / "data/atolls/mdv_admin1.geojson", driver="GeoJSON")

    patches = gpd.GeoDataFrame(
        {"Island": ["Isl1", "Isl1", "Isl2"], "Atoll": ["Atoll A", "Atoll A", "Atoll B"]},
        geometry=[box(73.020, 5.020, 73.021, 5.021), box(73.030, 5.020, 73.032, 5.021),
                  box(73.320, 5.020, 73.321, 5.021)], crs="EPSG:4326")
    (root / "data/units").mkdir(parents=True)
    patches.to_file(root / "data/units/Mangrove_extent_2020_Cerri.shp")

    write_raster(root / "data/rasters/NDVI.tif", 73.0, 5.2, 0.001, (200, 500), [ndvi_value(y) for y in YEARS])
    write_raster(root / "data/rasters/SAL.tif", 72.5, 5.5, 0.05, (20, 30), [salinity_value(y) for y in YEARS])

    path = write_template(root / "config" / "condition_config.xlsx")
    workbook = load_workbook(path)
    sheet = workbook["Indicators"]
    for row in range(2, 40):
        for col in range(1, 17):
            sheet.cell(row=row, column=col).value = None
    rows = [
        ("COND_NDVI", "NDVI", "Functional", "unitless", "NDVI.tif", "mangrove", "standard", 0, 1, None, None, "yes", 1, None, None, 2017),
        ("COND_SAL", "Salinity", "Chemical", "psu", "SAL.tif", "atoll", "inverse", 5, 40, None, None, "yes", 1, None, None, 2017),
    ]
    for r, row in enumerate(rows, start=2):
        for c, value in enumerate(row, start=1):
            sheet.cell(row=r, column=c).value = value
    project = workbook["Project"]
    for row in range(2, project.max_row + 1):          # two years per period, to test the averaging
        if project.cell(row=row, column=1).value == "opening_years":
            project.cell(row=row, column=2).value = "2017;2018"
        elif project.cell(row=row, column=1).value == "closing_years":
            project.cell(row=row, column=2).value = "2024;2025"
    workbook.save(path)
    return path
