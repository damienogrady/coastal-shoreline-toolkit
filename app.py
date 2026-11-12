"""Streamlit interface for exploratory Cape Cod shoreline change analysis."""

import json
import os
from datetime import date

import ee
import folium
import geopandas as gpd
import google.auth.exceptions
import pandas as pd
import plotly.express as px
import rasterio
import streamlit as st
from folium.plugins import Draw
from shapely.geometry import mapping
from streamlit_folium import st_folium

from coastwatch.analysis import (
    annual_window,
    extract_transect_positions,
    summarize_movement,
)
from coastwatch.config import (
    DEFAULT_AOI_PATH,
    DEFAULT_END_YEAR,
    DEFAULT_START_YEAR,
    DEFAULT_TRANSECTS_PATH,
    DEFAULT_TRANSECT_BEARING_DEGREES,
    DEFAULT_TRANSECT_SPACING_M,
    MAX_OBSERVATIONS,
    SENTINEL_FIRST_YEAR,
)
from coastwatch.geometry import (
    analysis_crs_for_area,
    make_transects,
    parse_aoi_geojson,
    parse_transects_geojson,
)
from coastwatch.gee import (
    NoImageryError,
    connect_earth_engine,
    land_polygons,
    make_composite,
    water_area_m2,
)
from coastwatch.raster import sample_dem_transects

MAP_WIDTH = 840
MAP_HEIGHT = 850
SHORELINE_COLORS = [
    "#e76f51",
    "#2a9d8f",
    "#e9c46a",
    "#6a4c93",
    "#457b9d",
    "#f4a261",
    "#d1495b",
]


st.set_page_config(
    page_title="Coastline | Cape Cod",
    page_icon="🌊",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def load_default_area():
    if not DEFAULT_AOI_PATH.is_file():
        raise FileNotFoundError(f"Default study-area file not found: {DEFAULT_AOI_PATH}")
    return parse_aoi_geojson(DEFAULT_AOI_PATH.read_text(encoding="utf-8"))


def load_default_transects(area):
    if not DEFAULT_TRANSECTS_PATH.is_file():
        raise FileNotFoundError(
            f"Default transect file not found: {DEFAULT_TRANSECTS_PATH}"
        )
    return parse_transects_geojson(
        DEFAULT_TRANSECTS_PATH.read_text(encoding="utf-8"),
        area,
    )


def make_map(area, draw_tools=False, basemap=False):
    center = area.centroid
    map_view = folium.Map(
        location=[center.y, center.x],
        zoom_start=13,
        tiles="OpenStreetMap" if basemap else None,
        control_scale=True,
    )
    folium.GeoJson(
        mapping(area),
        name="Study area",
        style_function=lambda _: {
            "color": "#087f8c",
            "weight": 3,
            "fillColor": "#44bba4",
            "fillOpacity": 0.12,
        },
        tooltip="Selected study area",
    ).add_to(map_view)
    if draw_tools:
        Draw(
            export=False,
            draw_options={
                "polyline": False,
                "rectangle": False,
                "circle": False,
                "circlemarker": False,
                "marker": False,
                "polygon": {"allowIntersection": False, "showArea": True},
            },
            edit_options={"edit": True, "remove": True},
        ).add_to(map_view)
    return map_view


def render_shoreline_legend(years, colors):
    legend_items = []
    legend_items.extend(
        (
            f'<div><span style="display:inline-block;width:16px;'
            f'border-top:3px solid {color};vertical-align:middle;'
            f'margin-right:7px;"></span>{year} shoreline</div>'
        )
        for year, color in zip(years, colors)
    )
    legend = (
        '<div style="padding:10px 12px;border:1px solid #9aa;border-radius:4px;'
        'font:13px/1.8 sans-serif;color:#16343b;">'
        '<strong>Map key</strong>'
        + "".join(legend_items)
        + "</div>"
    )
    st.markdown(legend, unsafe_allow_html=True)


def render_folium_map(map_view, key, height=MAP_HEIGHT):
    st_folium(
        map_view,
        width=MAP_WIDTH,
        height=height,
        use_container_width=False,
        key=key,
    )


def observations_for(start_year, end_year, interval):
    years = list(range(start_year, end_year + 1, interval))
    if years[-1] != end_year:
        years.append(end_year)
    if len(years) > MAX_OBSERVATIONS:
        raise ValueError(
            f"Choose a wider interval; this app supports up to {MAX_OBSERVATIONS} "
            "observations in one run."
        )
    return years


def run_analysis(
    area,
    years,
    transect_spacing,
    bearing,
    active_profile_height,
    dem_bytes,
    supplied_transects=None,
):
    transects = supplied_transects
    if transects is None:
        transects = make_transects(area, transect_spacing, bearing)
    area_geojson = mapping(area)
    observations = []
    shoreline_points = []
    map_layers = []
    progress = st.progress(0, text="Preparing the study area…")

    for index, year in enumerate(years):
        start, end = annual_window(year)
        progress.progress(
            index / len(years),
            text=f"Processing Sentinel-2 observation window: {year}",
        )
        (
            region,
            composite,
            mndwi,
            annual_scene_count,
        ) = make_composite(
            area_geojson, start, end
        )
        land_mask = mndwi.lte(0).rename("land")
        polygons = land_polygons(land_mask, region)
        positions, points = extract_transect_positions(
            area, polygons, transect_spacing, bearing, transects=transects
        )
        points["year"] = year
        shoreline_points.append(points)

        false_colour = composite.getMapId(
            {"bands": ["B11", "B8", "B3"], "min": 0, "max": 3500, "gamma": 1.15}
        )
        shoreline_color = SHORELINE_COLORS[index % len(SHORELINE_COLORS)]
        observations.append(
            {
                "year": year,
                "start_date": start.isoformat(),
                "end_date": (end - pd.Timedelta(days=1).to_pytimedelta()).isoformat(),
                "annual_scene_count": annual_scene_count,
                "water_area_km2": water_area_m2(land_mask, region) / 1_000_000,
                "positions": positions,
            }
        )
        map_layers.append(
            {
                "year": year,
                "false_colour_tiles": false_colour["tile_fetcher"].url_format,
                "shoreline_geojson": polygons.to_json(),
                "shoreline_color": shoreline_color,
            }
        )

    positions, summary = summarize_movement(observations, active_profile_height)
    dem_profiles = (
        sample_dem_transects(dem_bytes, transects) if dem_bytes is not None else None
    )
    progress.progress(1.0, text="Analysis complete.")
    return {
        "observations": observations,
        "area": area,
        "positions": positions,
        "summary": summary,
        "shoreline_points": (
            pd.concat(shoreline_points, ignore_index=True)
            if shoreline_points
            else gpd.GeoDataFrame(geometry=[], crs=transects.crs)
        ),
        "map_layers": map_layers,
        "transects": transects,
        "transect_source": "supplied" if supplied_transects is not None else "generated",
        "dem_profiles": dem_profiles,
        "bearing": bearing,
        "transect_spacing": transect_spacing,
        "active_profile_height": active_profile_height,
    }


def render_results(results):
    area = results["area"]
    observations = results["observations"]
    st.subheader("Observation overview")
    transect_description = (
        f"{len(results['transects'])} supplied transects"
        if results["transect_source"] == "supplied"
        else (
            f"{results['transect_spacing']:,} m spacing, bearing "
            f"{results['bearing']:g}° sea-to-land"
        )
    )
    st.caption(
        f"Last successful run: {observations[0]['year']}–{observations[-1]['year']} "
        f"(full calendar-year median composites), {transect_description}. "
        "Change the settings and run again to refresh these results."
    )
    overview = pd.DataFrame(
        [
            {
                "Year": item["year"],
                "Composite window": (
                    f'{item["start_date"]} to {item["end_date"]}'
                ),
                "Annual scenes": item["annual_scene_count"],
                "Classified water area (km²)": item["water_area_km2"],
            }
            for item in observations
        ]
    )
    st.dataframe(
        overview.style.format({"Classified water area (km²)": "{:,.2f}"}),
        use_container_width=True,
        hide_index=True,
    )
    st.caption(
        "Classified water area is sensitive to tides, waves, cloud gaps and the "
        "threshold; interpret it as context, not as a direct erosion measurement."
    )
    st.caption(
        "Cloudy pixels are masked before the annual median. The median ignores "
        "masked values; areas with no clear observation in that year remain "
        "masked rather than being filled from another year."
    )

    st.subheader("False-colour imagery and annual shoreline outlines")
    map_view = make_map(area)
    for layer in results["map_layers"]:
        year = layer["year"]
        folium.TileLayer(
            tiles=layer["false_colour_tiles"],
            attr="Google Earth Engine / Copernicus Sentinel-2",
            name=f"{year} · SWIR/NIR/green",
            overlay=True,
            control=True,
            show=year == observations[-1]["year"],
            opacity=0.85,
        ).add_to(map_view)
        folium.GeoJson(
            json.loads(layer["shoreline_geojson"]),
            name=f"{year} · shoreline outline",
            style_function=lambda _, color=layer["shoreline_color"]: {
                "color": color,
                "weight": 2,
                "opacity": 0.95,
                "fill": False,
                "fillOpacity": 0,
            },
            overlay=True,
            control=True,
        ).add_to(map_view)
    folium.LayerControl(collapsed=False).add_to(map_view)
    map_column, key_column = st.columns([3.2, 1], gap="small")
    with map_column:
        render_folium_map(map_view, key="results_map")
    with key_column:
        render_shoreline_legend(
            [layer["year"] for layer in results["map_layers"]],
            [layer["shoreline_color"] for layer in results["map_layers"]],
        )

    st.subheader("Transects in plan")
    st.markdown(
        (
            "Supplied cross-shore transects used to compare waterline positions."
            if results["transect_source"] == "supplied"
            else "Generated cross-shore transects used to compare waterline positions."
        )
        + " Shown separately from the imagery map."
    )
    transect_map = make_map(area, basemap=True)
    transects = results["transects"].to_crs("EPSG:4326")
    folium.GeoJson(
        transects,
        name="Cross-shore transects",
        style_function=lambda _: {"color": "#153e75", "weight": 2, "opacity": 0.9},
        tooltip=folium.GeoJsonTooltip(fields=["transect_id"], aliases=["Transect"]),
    ).add_to(transect_map)
    folium.LayerControl(collapsed=False).add_to(transect_map)
    render_folium_map(transect_map, key="transects_map", height=700)

    st.subheader("Shoreline positions along transects")
    st.markdown(
        "Each line represents a cross-shore transect. The x-axis is distance along "
        "the transect from its seaward end; it is **not** an elevation profile."
    )
    if results["positions"].empty:
        st.info(
            "No waterline crossings were found. Try a different year range, "
            "transect bearing, or study area."
        )
    else:
        profile_figure = px.line(
            results["positions"],
            x="year",
            y="position_m",
            color="transect_id",
            markers=True,
            labels={
                "year": "Observation year",
                "position_m": "Waterline position along transect (m)",
                "transect_id": "Transect",
            },
            title="Waterline position through time",
        )
        profile_figure.update_layout(legend_title_text="Transect")
        st.plotly_chart(profile_figure, use_container_width=True, height=900)

    if results["dem_profiles"] is not None:
        st.subheader("Elevation profiles from the supplied DEM")
        dem_figure = px.line(
            results["dem_profiles"],
            x="distance_m",
            y="elevation",
            color="transect_id",
            labels={
                "distance_m": "Distance along transect (m)",
                "elevation": "Raster elevation value",
                "transect_id": "Transect",
            },
            title="Static elevation profiles",
        )
        st.plotly_chart(dem_figure, use_container_width=True)
        st.caption(
            "These profiles describe the uploaded raster only; they are not "
            "time-series elevation change or bathymetry."
        )

    summary = results["summary"]
    if summary.empty or summary.iloc[0]["transects_compared"] < 2:
        st.info("At least two transects with waterline crossings in both endpoint "
                "observations are needed for an area and volume estimate.")
    else:
        row = summary.iloc[0]
        area_change = row["shoreline_area_change_m2"]
        volume_change = row["indicative_volume_change_m3"]
        col1, col2, col3 = st.columns(3)
        col1.metric("Transects compared", f'{int(row["transects_compared"])}')
        col2.metric(
            "Signed planform-change proxy",
            f"{area_change:,.0f} m²",
            help="Positive values indicate landward movement given the selected bearing.",
        )
        col3.metric(
            "Indicative volume change",
            f"{volume_change:,.0f} m³",
            help=(
                "Planform-change proxy multiplied by the assumed active-profile "
                "height. This is not a measured sediment volume."
            ),
        )
        st.caption(
            f"Volume uses an assumed active-profile height of "
            f'{row["assumed_active_profile_height_m"]:g} m. Positive landward '
            "movement is reported as an indicative volume loss. This screening "
            "estimate is not a surveyed beach-volume change."
        )

    st.subheader("Download results")
    positions_csv = results["positions"].to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download transect positions (CSV)",
        positions_csv,
        file_name="shoreline_transect_positions.csv",
        mime="text/csv",
    )
    points = results["shoreline_points"]
    if not points.empty:
        geojson = points.to_crs("EPSG:4326").to_json()
        st.download_button(
            "Download shoreline crossings (GeoJSON)",
            geojson,
            file_name="shoreline_transect_crossings.geojson",
            mime="application/geo+json",
        )


st.title("Coastal shoreline change toolkit")
st.markdown(
    "Explore how the visible waterline has changed using multi-date "
    "**Sentinel-2** imagery. Start with the supplied Cape Cod study area, then "
    "draw or upload a polygon for another coastal site."
)
st.info(
    "This is an exploratory remote-sensing demonstration. Shoreline position "
    "varies with tide, waves, season and image conditions; results are not "
    "survey-grade shoreline or sediment-volume measurements."
)

st.header("1 · Connect to Google Earth Engine")
st.markdown(
    "Earth Engine provides the Sentinel-2 archive and processes imagery in the "
    "cloud. Before connecting, authenticate once in the project terminal:"
)
st.code("earthengine authenticate --auth_mode=localhost", language="bash")
st.markdown(
    "Use a Google Cloud project with the **Earth Engine API enabled**. "
    "[Earth Engine authentication guide](https://developers.google.com/earth-engine/guides/auth)"
)
project_id = st.text_input(
    "Google Cloud project ID",
    value=os.getenv("EE_PROJECT", ""),
    placeholder="your-google-cloud-project",
    help="The project is used for Earth Engine API access; no credentials are stored by this app.",
)
connect_clicked = st.button("Verify Earth Engine connection", type="secondary")
if connect_clicked:
    try:
        connect_earth_engine(project_id)
        st.session_state["ee_project"] = project_id.strip()
        st.success(f"Connected to Earth Engine project `{project_id.strip()}`.")
    except (ee.EEException, google.auth.exceptions.DefaultCredentialsError, ValueError) as error:
        st.session_state.pop("ee_project", None)
        st.error(f"Could not connect to Earth Engine: {error}")
connected = st.session_state.get("ee_project") == project_id.strip() and bool(project_id.strip())
if connected:
    st.success("Earth Engine connection is ready for this session.")
else:
    st.warning("Authenticate in the terminal, then verify your Earth Engine connection above.")

st.header("2 · Define the study")
mode = st.radio(
    "Study area",
    ["Cape Cod study area", "Upload GeoJSON", "Draw polygon on map"],
    horizontal=True,
)
area = None
area_error = None
uploaded_area = None
if mode == "Cape Cod study area":
    try:
        area = load_default_area()
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as error:
        area_error = str(error)
elif mode == "Upload GeoJSON":
    uploaded_area = st.file_uploader(
        "Upload a GeoJSON Polygon or MultiPolygon",
        type=["geojson", "json"],
        help="Coordinates must be longitude/latitude (EPSG:4326).",
    )
    if uploaded_area is not None:
        try:
            area = parse_aoi_geojson(uploaded_area.getvalue())
        except (ValueError, KeyError, TypeError) as error:
            area_error = f"Could not read the uploaded study area: {error}"
else:
    if "drawn_area_geojson" not in st.session_state:
        try:
            st.session_state["drawn_area_geojson"] = json.loads(
                DEFAULT_AOI_PATH.read_text(encoding="utf-8")
            )["features"][0]["geometry"]
        except (FileNotFoundError, json.JSONDecodeError, KeyError, IndexError):
            st.session_state["drawn_area_geojson"] = None
    if st.session_state["drawn_area_geojson"] is not None:
        try:
            area = parse_aoi_geojson(st.session_state["drawn_area_geojson"])
        except ValueError as error:
            area_error = str(error)

if mode == "Draw polygon on map":
    preview_area = area
    if preview_area is None:
        preview_area = parse_aoi_geojson(
            {
                "type": "Polygon",
                "coordinates": [[[-69.99, 41.62], [-69.92, 41.62], [-69.92, 41.71],
                                 [-69.99, 41.71], [-69.99, 41.62]]],
            }
        )
    st.caption(
        "Use the polygon tool on the map. A drawn polygon becomes the analysis area; "
        "the map extent by itself is not used as a study boundary."
    )
    draw_response = st_folium(
        make_map(preview_area, draw_tools=True, basemap=True),
        width=MAP_WIDTH,
        height=MAP_HEIGHT,
        use_container_width=False,
        key="drawing_map",
        returned_objects=["all_drawings"],
    )
    if draw_response and draw_response.get("all_drawings"):
        drawn_features = draw_response["all_drawings"]
        candidate = drawn_features[-1].get("geometry")
        if candidate:
            st.session_state["drawn_area_geojson"] = candidate
            try:
                area = parse_aoi_geojson(candidate)
                area_error = None
            except ValueError as error:
                area = None
                area_error = f"Drawn polygon is not ready: {error}"

if area_error:
    st.error(area_error)
if area is not None and mode != "Draw polygon on map":
    render_folium_map(make_map(area, basemap=True), key="study_area_map")
if area is not None:
    area_km2 = (
        gpd.GeoSeries([area], crs="EPSG:4326")
        .to_crs(analysis_crs_for_area(area))
        .area.iloc[0]
        / 1_000_000
    )
    st.caption(f"Validated study-area size: **{area_km2:,.1f} km²** (maximum 500 km²).")

supplied_transects = None
transect_error = None
if mode == "Cape Cod study area" and area is not None:
    try:
        supplied_transects = load_default_transects(area)
    except (FileNotFoundError, OSError, ValueError, json.JSONDecodeError) as error:
        transect_error = str(error)
        st.error(f"Could not load the default transects: {transect_error}")

st.header("3 · Choose observations and run")
available_years = list(range(SENTINEL_FIRST_YEAR, date.today().year + 1))
controls = st.columns(4)
start_default = min(DEFAULT_START_YEAR, date.today().year)
end_default = min(max(DEFAULT_END_YEAR, start_default), date.today().year)
start_year = controls[0].selectbox(
    "First observation year",
    available_years,
    index=available_years.index(start_default),
)
end_year = controls[1].selectbox(
    "Last observation year",
    available_years,
    index=available_years.index(end_default),
)
interval = controls[2].selectbox(
    "Observation interval",
    [1, 2, 3, 4],
    index=1,
    format_func=lambda value: f"Every {value} year{'s' if value != 1 else ''}",
)
if supplied_transects is None:
    transect_spacing = controls[3].selectbox(
        "Transect spacing",
        [250, 500, 750, 1000],
        index=1,
        format_func=lambda value: f"{value:,} m",
    )
else:
    transect_spacing = DEFAULT_TRANSECT_SPACING_M
    controls[3].markdown("**Transects**")
    controls[3].caption(f"{len(supplied_transects)} supplied lines")

if supplied_transects is not None:
    bearing = DEFAULT_TRANSECT_BEARING_DEGREES
else:
    bearing = st.number_input(
        "Transect bearing: sea toward land (°)",
        min_value=0,
        max_value=359,
        value=DEFAULT_TRANSECT_BEARING_DEGREES,
        help="Set the azimuth along which the transects extend from sea toward land.",
    )
st.caption(
    "Each selected year uses a full-calendar-year median composite. Cloud, "
    "cloud-shadow, cirrus, snow, no-data and defective pixels are masked before "
    "compositing; masked pixels are not filled from other years."
)
active_profile_height = st.number_input(
    "Assumed active-profile height for indicative volume (m)",
    min_value=0.1,
    max_value=30.0,
    value=5.0,
    step=0.5,
    help="This is an explicit user assumption, not measured by Sentinel-2.",
)
dem_upload = st.file_uploader(
    "Optional projected, single-band elevation GeoTIFF for static transect profiles",
    type=["tif", "tiff"],
    help=(
        "Elevation values are sampled with rasterio. This raster is not used to "
        "infer historical elevation change or sediment volume."
    ),
)

try:
    if start_year > end_year:
        raise ValueError("The last observation year must not precede the first.")
    if start_year == end_year:
        raise ValueError("Select at least two different observation years to compare.")
    years = observations_for(start_year, end_year, interval)
except ValueError as error:
    years = []
    st.error(str(error))

if years:
    st.caption(
        "Observations to be processed: "
        + " · ".join(str(year) for year in years)
        + ". Each observation is a full-year median of cloud-masked Sentinel-2 pixels."
    )

st.session_state.setdefault("analysis_running", False)
run_clicked = st.button(
    "Run shoreline comparison",
    type="primary",
    disabled=(
        not connected
        or area is None
        or not years
        or transect_error is not None
        or st.session_state["analysis_running"]
    ),
)
if run_clicked:
    st.session_state["analysis_running"] = True
    st.session_state.pop("analysis_results", None)
    st.session_state.pop("analysis_error", None)
    st.rerun()

if st.session_state["analysis_running"]:
    try:
        connect_earth_engine(project_id)
        st.session_state["analysis_results"] = run_analysis(
            area,
            years,
            transect_spacing,
            float(bearing),
            active_profile_height,
            dem_upload.getvalue() if dem_upload is not None else None,
            supplied_transects=supplied_transects,
        )
    except (
        ee.EEException,
        google.auth.exceptions.DefaultCredentialsError,
        NoImageryError,
        ValueError,
        rasterio.errors.RasterioError,
    ) as error:
        st.session_state["analysis_error"] = str(error)
    finally:
        st.session_state["analysis_running"] = False
    st.rerun()

if analysis_error := st.session_state.get("analysis_error"):
    st.error(f"Analysis could not be completed: {analysis_error}")

results = st.session_state.get("analysis_results")
if results:
    st.header("4 · Explore results")
    render_results(results)

st.divider()
st.caption(
    "Data: Copernicus Sentinel-2 surface reflectance via Google Earth Engine. "
    "This application is an educational screening tool; validate results against "
    "local tide, datum and field-survey information before interpretation."
)
