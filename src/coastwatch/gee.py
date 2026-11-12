"""Google Earth Engine image preparation and shoreline extraction."""

from datetime import date

import ee
import geopandas as gpd

from coastwatch.analysis import annual_window
from coastwatch.config import SENTINEL_COLLECTION


class NoImageryError(ValueError):
    """Raised when a date window contains no usable Sentinel-2 scenes."""


def connect_earth_engine(project_id: str) -> None:
    """Initialize Earth Engine with the user's Cloud project."""
    if not project_id.strip():
        raise ValueError("Enter a Google Cloud project ID to connect to Earth Engine.")
    ee.Initialize(project=project_id.strip())


def make_composite(
    area_geojson: dict,
    start_date: date,
    end_date: date,
):
    """Build a cloud-masked annual median; masked pixels are ignored by median."""
    region = ee.Geometry(area_geojson)
    annual_start, annual_end = annual_window(start_date.year)
    if start_date != annual_start or end_date != annual_end:
        raise ValueError("Sentinel-2 composites must cover a full calendar year.")
    surface_reflectance = (
        ee.ImageCollection(SENTINEL_COLLECTION)
        .filterBounds(region)
        .filterDate(start_date.isoformat(), end_date.isoformat())
    )
    cloud_probability = (
        ee.ImageCollection("COPERNICUS/S2_CLOUD_PROBABILITY")
        .filterBounds(region)
        .filterDate(start_date.isoformat(), end_date.isoformat())
    )
    joined = ee.ImageCollection(
        ee.Join.saveFirst("cloud_probability").apply(
            primary=surface_reflectance,
            secondary=cloud_probability,
            condition=ee.Filter.equals(
                leftField="system:index",
                rightField="system:index",
            ),
        )
    ).filter(ee.Filter.notNull(["cloud_probability"]))

    def mask_clouds(image):
        image = ee.Image(image)
        cloud_image = ee.Image(image.get("cloud_probability"))
        scene_classification = image.select("SCL")
        clear_scene = (
            scene_classification.neq(0)
            .And(scene_classification.neq(1))
            .And(scene_classification.neq(3))
            .And(scene_classification.neq(7))
            .And(scene_classification.neq(8))
            .And(scene_classification.neq(9))
            .And(scene_classification.neq(10))
            .And(scene_classification.neq(11))
        )
        clear_probability = cloud_image.select("probability").lt(40)
        return image.updateMask(clear_scene.And(clear_probability)).select(
            ["B11", "B8", "B3"]
        )

    clear_collection = joined.map(mask_clouds)
    annual_collection = clear_collection.filterDate(
        start_date.isoformat(), end_date.isoformat()
    )
    annual_scene_count = annual_collection.size().getInfo()
    if annual_scene_count == 0:
        raise NoImageryError(
            f"No Sentinel-2 scenes with matching cloud-probability data were found "
            f"from {start_date} to {end_date}. Try another year or study area."
        )

    composite = annual_collection.median().clip(region)
    mndwi = composite.normalizedDifference(["B3", "B11"]).rename("MNDWI")
    return region, composite, mndwi, int(annual_scene_count)


def water_area_m2(land_mask, region) -> float:
    """Return classified-water pixel area within the study area."""
    result = (
        ee.Image.pixelArea()
        .updateMask(land_mask.Not())
        .reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=region,
            scale=20,
            maxPixels=50_000_000,
            tileScale=4,
        )
        .getInfo()
    )
    return float(result.get("area", 0) or 0)


def land_polygons(land_mask, region) -> gpd.GeoDataFrame:
    """Vectorize the classified land mask for shoreline transect analysis."""
    vector_image = land_mask.rename("land").selfMask()
    features = (
        vector_image.reduceToVectors(
            reducer=ee.Reducer.countEvery(),
            geometry=region,
            scale=20,
            geometryType="polygon",
            eightConnected=True,
            labelProperty="land",
            maxPixels=50_000_000,
            tileScale=4,
        )
        .map(lambda feature: feature.simplify(20))
        .getInfo()["features"]
    )
    if not features:
        return gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")
