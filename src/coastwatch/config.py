"""Shared application settings."""

from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_AOI_PATH = PROJECT_ROOT / "data" / "aoi" / "cape_cod.geojson"
DEFAULT_TRANSECTS_PATH = PROJECT_ROOT / "data" / "transects" / "transects.geojson"
SENTINEL_COLLECTION = "COPERNICUS/S2_SR_HARMONIZED"
SENTINEL_FIRST_YEAR = 2016
MAX_AOI_AREA_KM2 = 500
MAX_OBSERVATIONS = 7
DEFAULT_START_YEAR = 2016
DEFAULT_END_YEAR = min(2025, date.today().year)
DEFAULT_TRANSECT_SPACING_M = 500
DEFAULT_TRANSECT_BEARING_DEGREES = 90
MAX_TRANSECTS = 60
