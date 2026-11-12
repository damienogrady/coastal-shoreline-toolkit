"""Local elevation sampling for optional coastal transect profiles."""

import math
from io import BytesIO

import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio
from rasterio.io import MemoryFile


def sample_dem_transects(
    raster_bytes: bytes,
    transects: gpd.GeoDataFrame,
) -> pd.DataFrame:
    """Sample a projected, single-band GeoTIFF along the supplied transects."""
    with MemoryFile(BytesIO(raster_bytes)) as memory_file:
        with memory_file.open() as dataset:
            if dataset.count != 1:
                raise ValueError("The elevation GeoTIFF must contain exactly one band.")
            if dataset.crs is None or dataset.crs.is_geographic:
                raise ValueError(
                    "The elevation GeoTIFF must use a projected coordinate reference system."
                )

            dem_transects = transects.to_crs(dataset.crs)
            _, metres_per_unit = dataset.crs.linear_units_factor
            if metres_per_unit <= 0:
                raise ValueError("The elevation GeoTIFF uses unsupported horizontal units.")
            pixel_size_m = min(abs(value) for value in dataset.res) * metres_per_unit
            if pixel_size_m <= 0:
                raise ValueError("The elevation GeoTIFF has an invalid pixel size.")

            samples = []
            coordinates = []
            for transect in dem_transects.itertuples():
                line = transect.geometry
                sample_count = min(
                    999, max(1, int(line.length * metres_per_unit / pixel_size_m))
                )
                for index in range(sample_count + 1):
                    distance = line.length * index / sample_count
                    point = line.interpolate(distance)
                    if not (
                        dataset.bounds.left <= point.x <= dataset.bounds.right
                        and dataset.bounds.bottom <= point.y <= dataset.bounds.top
                    ):
                        continue
                    coordinates.append((point.x, point.y))
                    samples.append(
                        {
                            "transect_id": transect.transect_id,
                            "distance_m": distance * metres_per_unit,
                        }
                    )

            elevations = dataset.sample(coordinates, masked=True)
            records = []
            for sample, elevation in zip(samples, elevations):
                if (
                    elevation.size
                    and not np.ma.is_masked(elevation[0])
                    and math.isfinite(float(elevation[0]))
                ):
                    records.append({**sample, "elevation": float(elevation[0])})

    if not records:
        raise ValueError(
            "The elevation raster does not overlap any of the study-area transects."
        )
    return pd.DataFrame(records)
