import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box

from mangrove_condition import ecv_maps
from mangrove_condition.schema import EcvSpec


def _spec():
    return EcvSpec(id="ECV_T", name="Test variable", unit="m", file="t.xlsx", sheet="s", unit_column="atoll",
                   time_column="year", value_column="v", aggregation="none", min_months=1, exclude_years=())


def _raster(path, years=(2000, 2001, 2002)):
    data = np.random.default_rng(0).uniform(0.7, 0.9, (len(years), 20, 10)).astype("float32")
    data[:, 0, 0] = np.nan
    with rasterio.open(path, "w", driver="GTiff", height=20, width=10, count=len(years), dtype="float32", crs="EPSG:4326",
                       transform=from_origin(72.5, 7.0, 0.1, 0.1), nodata=np.nan) as dst:
        for i, year in enumerate(years, start=1):
            dst.write(data[i - 1], i)
            dst.set_band_description(i, str(year))


def _atolls():
    return gpd.GeoDataFrame({"atoll": ["Haa Alifu", "Kaafu", "Seenu"]},
                            geometry=[box(72.7, 6.5, 72.9, 6.9), box(73.0, 4.2, 73.3, 4.6), box(73.1, 5.5, 73.3, 5.7)],
                            crs="EPSG:4326")


def test_read_stack_gives_extent_and_years(tmp_path):
    path = tmp_path / "a.tif"
    _raster(path)
    data, extent, names = ecv_maps.read_stack(path)
    assert data.shape == (3, 20, 10)
    assert np.allclose(extent, (72.5, 73.5, 5.0, 7.0))
    assert ecv_maps._period_text(names) == "2000 to 2002"


def test_maps_are_written(tmp_path):
    path = tmp_path / "a.tif"
    _raster(path)
    atolls = _atolls()
    first = ecv_maps.plot_value_map(_spec(), path, atolls, ["Haa Alifu", "Kaafu"], "viridis", tmp_path)
    means = pd.Series({"Haa Alifu": 0.8, "Kaafu": 0.82})
    second = ecv_maps.plot_atoll_map(_spec(), means, atolls, "2000 to 2002", "viridis", tmp_path)
    assert first.exists() and second.exists()
    assert (tmp_path / "vector" / "ECV_T_4_map_mean.pdf").exists()


def test_regions_and_label_rules():
    assert not set(ecv_maps.REGIONS["N"]) & set(ecv_maps.REGIONS["C"])
    assert ecv_maps._spread([1.0, 1.05, 3.0], 0.2) == [1.0, 1.2, 3.0]
