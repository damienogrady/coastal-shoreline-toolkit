import geopandas as gpd
import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import LineString

from coastwatch.raster import sample_dem_transects


def make_dem():
    memory_file = MemoryFile()
    with memory_file.open(
        driver="GTiff",
        height=100,
        width=100,
        count=1,
        dtype="float32",
        crs="EPSG:26919",
        transform=from_origin(500_000, 4_620_000, 10, 10),
        nodata=-9999,
    ) as dataset:
        dataset.write(100 * np.ones((100, 100), dtype="float32"), 1)
    return memory_file.read()


def test_sample_dem_returns_elevations_along_transect():
    transects = gpd.GeoDataFrame(
        {"transect_id": ["T001"]},
        geometry=[LineString([(500_100, 4_619_950), (500_800, 4_619_950)])],
        crs="EPSG:26919",
    )
    profiles = sample_dem_transects(make_dem(), transects)
    assert not profiles.empty
    assert set(profiles["elevation"]) == {100.0}


def test_sample_dem_rejects_geographic_raster():
    memory_file = MemoryFile()
    with memory_file.open(
        driver="GTiff",
        height=10,
        width=10,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=from_origin(-70, 42, 0.01, 0.01),
    ) as dataset:
        dataset.write(np.ones((10, 10), dtype="float32"), 1)
    transects = gpd.GeoDataFrame(
        {"transect_id": ["T001"]},
        geometry=[LineString([(500_100, 4_619_950), (500_800, 4_619_950)])],
        crs="EPSG:26919",
    )
    with pytest.raises(ValueError, match="projected coordinate"):
        sample_dem_transects(memory_file.read(), transects)
