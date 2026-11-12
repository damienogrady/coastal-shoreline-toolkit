"""Transect-based shoreline movement and indicative volume calculations."""

import geopandas as gpd
import pandas as pd
from shapely.geometry import GeometryCollection, MultiPoint, Point

from coastwatch.geometry import analysis_crs_for_area, make_transects


def _intersection_points(geometry) -> list[Point]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, Point):
        return [geometry]
    if isinstance(geometry, MultiPoint):
        return list(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        return [
            point
            for part in geometry.geoms
            for point in _intersection_points(part)
        ]
    return []


def extract_transect_positions(
    area,
    water_gdf: gpd.GeoDataFrame,
    spacing_m: int,
    bearing_degrees: float,
    transects: gpd.GeoDataFrame | None = None,
) -> tuple[pd.DataFrame, gpd.GeoDataFrame]:
    """Find a waterline crossing on each transect for one observation."""
    if transects is None:
        transects = make_transects(area, spacing_m, bearing_degrees)
    if water_gdf.empty or transects.empty:
        empty = pd.DataFrame(
            columns=["transect_id", "alongshore_m", "spacing_m", "position_m"]
        )
        return empty, gpd.GeoDataFrame(geometry=[], crs=transects.crs)

    projected_crs = analysis_crs_for_area(area)
    water_projected = water_gdf.to_crs(projected_crs)
    area_projected = gpd.GeoSeries([area], crs="EPSG:4326").to_crs(projected_crs).iloc[0]
    shoreline = water_projected.geometry.union_all().boundary.difference(
        area_projected.boundary.buffer(25)
    )
    center = area_projected.centroid
    positions = []
    points = []

    for transect in transects.itertuples():
        crossings = _intersection_points(shoreline.intersection(transect.geometry))
        if not crossings:
            continue
        crossing = min(crossings, key=center.distance)
        positions.append(
            {
                "transect_id": transect.transect_id,
                "alongshore_m": transect.alongshore_m,
                "spacing_m": transect.spacing_m,
                "position_m": float(transect.geometry.project(crossing)),
            }
        )
        points.append(
            {
                "year": None,
                "transect_id": transect.transect_id,
                "geometry": crossing,
            }
        )

    position_frame = pd.DataFrame(positions)
    if not points:
        return position_frame, gpd.GeoDataFrame(geometry=[], crs=projected_crs)
    point_frame = gpd.GeoDataFrame(points, geometry="geometry", crs=projected_crs)
    return position_frame, point_frame


def summarize_movement(
    observations: list[dict],
    active_profile_height_m: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Summarize landward shoreline movement and an assumed-profile volume proxy."""
    if active_profile_height_m <= 0:
        raise ValueError("Active-profile height must be greater than zero.")

    position_frames = []
    for observation in observations:
        frame = observation["positions"].copy()
        if frame.empty:
            continue
        frame["year"] = observation["year"]
        position_frames.append(frame)

    if not position_frames:
        return pd.DataFrame(), pd.DataFrame()
    positions = pd.concat(position_frames, ignore_index=True)

    first_year = observations[0]["year"]
    last_year = observations[-1]["year"]
    first = positions.loc[positions["year"] == first_year].set_index("transect_id")
    last = positions.loc[positions["year"] == last_year].set_index("transect_id")
    first = first.rename(columns={"position_m": "position_m_start"})
    last = last.rename(columns={"position_m": "position_m_end"})
    common = first[["alongshore_m", "spacing_m", "position_m_start"]].join(
        last[["position_m_end"]], how="inner"
    )
    common = common.sort_values("alongshore_m")
    common["landward_shift_m"] = (
        common["position_m_end"] - common["position_m_start"]
    )

    area_change_m2 = 0.0
    for left, right in zip(common.iloc[:-1].itertuples(), common.iloc[1:].itertuples()):
        alongshore_distance = right.alongshore_m - left.alongshore_m
        area_change_m2 += (
            (left.landward_shift_m + right.landward_shift_m)
            / 2
            * alongshore_distance
        )

    volume_change_m3 = -area_change_m2 * active_profile_height_m
    summary = pd.DataFrame(
        [
            {
                "start_year": first_year,
                "end_year": last_year,
                "shoreline_area_change_m2": area_change_m2,
                "assumed_active_profile_height_m": active_profile_height_m,
                "indicative_volume_change_m3": volume_change_m3,
                "transects_compared": len(common),
            }
        ]
    )
    return positions, summary


def annual_window(year: int):
    """Return a full calendar year as an Earth Engine half-open date interval."""
    return pd.Timestamp(year=year, month=1, day=1).date(), pd.Timestamp(
        year=year + 1, month=1, day=1
    ).date()
