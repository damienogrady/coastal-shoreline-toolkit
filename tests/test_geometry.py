from pathlib import Path

import pytest
import geopandas as gpd
from shapely.geometry import box

from coastwatch.config import (
    DEFAULT_TRANSECT_BEARING_DEGREES,
    DEFAULT_TRANSECTS_PATH,
    SENTINEL_FIRST_YEAR,
)
from coastwatch.geometry import (
    analysis_crs_for_area,
    make_transects,
    parse_aoi_geojson,
    parse_transects_geojson,
)


ROOT = Path(__file__).resolve().parents[1]


def cape_cod_area():
    return parse_aoi_geojson(
        (ROOT / "data" / "aoi" / "cape_cod.geojson").read_text(encoding="utf-8")
    )


def test_supplied_cape_cod_polygon_is_valid_and_bounded():
    area = cape_cod_area()
    assert area.geom_type == "Polygon"
    assert area.is_valid
    assert analysis_crs_for_area(area) == "EPSG:32619"
    assert DEFAULT_TRANSECT_BEARING_DEGREES == 90
    assert SENTINEL_FIRST_YEAR == 2016


def test_default_transects_are_loaded_as_projected_analysis_lines():
    transects = parse_transects_geojson(
        DEFAULT_TRANSECTS_PATH.read_text(encoding="utf-8"),
        cape_cod_area(),
    )

    assert len(transects) == 12
    assert str(transects.crs) == "EPSG:32619"
    assert transects.transect_id.iloc[0] == "T001"
    assert transects.transect_id.iloc[-1] == "T012"
    assert transects.alongshore_m.is_monotonic_increasing
    assert all(transects.geometry.geom_type == "LineString")


def test_analysis_crs_follows_the_selected_study_area():
    sydney_area = parse_aoi_geojson(
        {
            "type": "Polygon",
            "coordinates": [
                [[151, -34], [151.01, -34], [151.01, -34.01], [151, -34.01], [151, -34]]
            ],
        }
    )
    polar_area = parse_aoi_geojson(
        {
            "type": "Polygon",
            "coordinates": [
                [[20, 85], [20.01, 85], [20.01, 84.99], [20, 84.99], [20, 85]]
            ],
        }
    )
    assert analysis_crs_for_area(sydney_area) == "EPSG:32756"
    assert analysis_crs_for_area(polar_area) == "EPSG:32661"


def test_parse_aoi_rejects_non_polygon_geometry():
    with pytest.raises(ValueError, match="Polygon or MultiPolygon"):
        parse_aoi_geojson(
            {"type": "Point", "coordinates": [-69.95, 41.66]}
        )


def test_parse_aoi_rejects_invalid_polygon():
    invalid_polygon = {
        "type": "Polygon",
        "coordinates": [
            [[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]
        ],
    }
    with pytest.raises(ValueError, match="empty or invalid"):
        parse_aoi_geojson(invalid_polygon)


def test_parse_aoi_rejects_oversized_area():
    large_area = {
        "type": "Polygon",
        "coordinates": [
            [[-70, 41], [-60, 41], [-60, 42], [-70, 42], [-70, 41]]
        ],
    }
    with pytest.raises(ValueError, match="no larger than"):
        parse_aoi_geojson(large_area)


def test_transects_use_metric_crs_and_requested_bearing():
    transects = make_transects(cape_cod_area(), spacing_m=500, bearing_degrees=90)
    assert not transects.empty
    assert str(transects.crs) == "EPSG:32619"
    assert transects.geometry.iloc[0].length > 1_000
    assert transects.iloc[0]["transect_id"].startswith("T")


def test_transects_reject_spacing_that_exceeds_processing_limit():
    large_area = (
        gpd.GeoSeries([box(500_000, 4_600_000, 520_000, 4_620_000)], crs="EPSG:26919")
        .to_crs("EPSG:4326")
        .iloc[0]
    )
    with pytest.raises(ValueError, match="more than 60 transects"):
        make_transects(large_area, spacing_m=250, bearing_degrees=0)
