"""Study-area validation and projected transect construction."""

import json
import math
from collections.abc import Mapping

import geopandas as gpd
from shapely.geometry import LineString, MultiPolygon, Polygon, shape
from shapely.ops import unary_union

from coastwatch.config import MAX_AOI_AREA_KM2, MAX_TRANSECTS


def analysis_crs_for_area(area: Polygon | MultiPolygon) -> str:
    """Choose a local metric CRS for the study-area centroid."""
    longitude, latitude = area.centroid.coords[0]
    if latitude > 84:
        return "EPSG:32661"
    if latitude < -80:
        return "EPSG:32761"
    zone = min(60, max(1, math.floor((longitude + 180) / 6) + 1))
    epsg = (32600 if latitude >= 0 else 32700) + zone
    return f"EPSG:{epsg}"


def parse_aoi_geojson(payload: str | bytes | Mapping) -> Polygon | MultiPolygon:
    """Parse and validate a polygonal GeoJSON area in longitude/latitude."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, Mapping):
        raise ValueError("The study area must be a GeoJSON object.")

    geojson_type = payload.get("type")
    if geojson_type == "FeatureCollection":
        features = payload.get("features", [])
        geometries = [
            shape(feature["geometry"])
            for feature in features
            if feature.get("geometry") is not None
        ]
        if not geometries:
            raise ValueError("The GeoJSON FeatureCollection contains no geometries.")
        geometry = unary_union(geometries)
    elif geojson_type == "Feature":
        if payload.get("geometry") is None:
            raise ValueError("The GeoJSON feature has no geometry.")
        geometry = shape(payload["geometry"])
    else:
        geometry = shape(payload)

    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise ValueError("The study area must contain a Polygon or MultiPolygon.")
    if geometry.is_empty or not geometry.is_valid:
        raise ValueError("The study-area polygon is empty or invalid.")

    min_x, min_y, max_x, max_y = geometry.bounds
    if min_x < -180 or max_x > 180 or min_y < -90 or max_y > 90:
        raise ValueError("Study-area coordinates must be longitude/latitude (EPSG:4326).")

    projected = gpd.GeoSeries([geometry], crs="EPSG:4326").to_crs(
        analysis_crs_for_area(geometry)
    )
    area_km2 = projected.area.iloc[0] / 1_000_000
    if area_km2 <= 0 or area_km2 > MAX_AOI_AREA_KM2:
        raise ValueError(
            f"The study area must be greater than 0 and no larger than "
            f"{MAX_AOI_AREA_KM2} km² (submitted area: {area_km2:,.1f} km²)."
        )
    return geometry


def parse_transects_geojson(
    payload: str | bytes | Mapping,
    area: Polygon | MultiPolygon,
) -> gpd.GeoDataFrame:
    """Parse WGS84 transect lines and add projected analysis attributes."""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, Mapping):
        raise ValueError("Transects must be provided as a GeoJSON object.")

    geojson_type = payload.get("type")
    if geojson_type == "FeatureCollection":
        features = payload.get("features", [])
    elif geojson_type == "Feature":
        features = [payload]
    elif geojson_type == "LineString":
        features = [{"type": "Feature", "properties": {}, "geometry": payload}]
    else:
        raise ValueError("Transects must be GeoJSON LineString features.")

    geometries = []
    for feature in features:
        if not isinstance(feature, Mapping) or feature.get("geometry") is None:
            raise ValueError("Every transect feature must contain a LineString.")
        geometry = shape(feature["geometry"])
        if (
            not isinstance(geometry, LineString)
            or geometry.is_empty
            or len(geometry.coords) < 2
        ):
            raise ValueError("Every transect must be a non-empty LineString.")
        min_x, min_y, max_x, max_y = geometry.bounds
        if min_x < -180 or max_x > 180 or min_y < -90 or max_y > 90:
            raise ValueError("Transect coordinates must be longitude/latitude (EPSG:4326).")
        geometries.append(geometry)

    if len(geometries) < 2:
        raise ValueError("At least two transects are required for shoreline comparison.")
    if len(geometries) > MAX_TRANSECTS:
        raise ValueError(f"No more than {MAX_TRANSECTS} transects are supported.")

    projected_crs = analysis_crs_for_area(area)
    projected = gpd.GeoDataFrame(geometry=geometries, crs="EPSG:4326").to_crs(
        projected_crs
    )
    midpoints = projected.geometry.interpolate(0.5, normalized=True)
    alongshore_distances = [0.0]
    for previous, current in zip(midpoints[:-1], midpoints[1:]):
        alongshore_distances.append(
            alongshore_distances[-1] + previous.distance(current)
        )
    segment_distances = [
        current - previous
        for previous, current in zip(alongshore_distances[:-1], alongshore_distances[1:])
    ]
    spacing = [
        segment_distances[min(index, len(segment_distances) - 1)]
        for index in range(len(geometries))
    ]
    projected.insert(
        0,
        "transect_id",
        [f"T{index:03d}" for index in range(1, len(geometries) + 1)],
    )
    projected["alongshore_m"] = alongshore_distances
    projected["spacing_m"] = [round(distance) for distance in spacing]
    return projected


def make_transects(
    area: Polygon | MultiPolygon,
    spacing_m: int,
    bearing_degrees: float,
) -> gpd.GeoDataFrame:
    """Create parallel cross-shore transects in a local metric CRS.

    The bearing points from sea toward land. Increasing distance along a
    transect therefore represents a landward shoreline movement.
    """
    if spacing_m <= 0:
        raise ValueError("Transect spacing must be greater than zero.")
    if not 0 <= bearing_degrees < 360:
        raise ValueError("Transect bearing must be between 0 and 359 degrees.")

    projected_crs = analysis_crs_for_area(area)
    projected = gpd.GeoSeries([area], crs="EPSG:4326").to_crs(projected_crs).iloc[0]
    center = projected.centroid
    min_x, min_y, max_x, max_y = projected.bounds
    diagonal = math.hypot(max_x - min_x, max_y - min_y)

    bearing = math.radians(bearing_degrees)
    cross_shore = (math.sin(bearing), math.cos(bearing))
    alongshore = (math.cos(bearing), -math.sin(bearing))
    corners = [
        (min_x, min_y),
        (min_x, max_y),
        (max_x, min_y),
        (max_x, max_y),
    ]
    offsets = [
        (x - center.x) * alongshore[0] + (y - center.y) * alongshore[1]
        for x, y in corners
    ]
    first = math.floor(min(offsets) / spacing_m) * spacing_m
    last = math.ceil(max(offsets) / spacing_m) * spacing_m
    positions = list(range(first, last + 1, spacing_m))
    if len(positions) > MAX_TRANSECTS:
        raise ValueError(
            f"This spacing would create more than {MAX_TRANSECTS} transects. "
            "Increase the transect spacing or reduce the study area."
        )

    half_length = diagonal * 0.75
    rows = []
    for index, offset in enumerate(positions, start=1):
        origin_x = center.x + alongshore[0] * offset
        origin_y = center.y + alongshore[1] * offset
        line = LineString(
            [
                (
                    origin_x - cross_shore[0] * half_length,
                    origin_y - cross_shore[1] * half_length,
                ),
                (
                    origin_x + cross_shore[0] * half_length,
                    origin_y + cross_shore[1] * half_length,
                ),
            ]
        )
        if line.intersects(projected):
            rows.append(
                {
                    "transect_id": f"T{index:03d}",
                    "alongshore_m": float(offset),
                    "spacing_m": int(spacing_m),
                    "geometry": line,
                }
            )

    if not rows:
        return gpd.GeoDataFrame(
            {
                "transect_id": [],
                "alongshore_m": [],
                "spacing_m": [],
            },
            geometry=gpd.GeoSeries([], crs=projected_crs),
            crs=projected_crs,
        )
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=projected_crs)
