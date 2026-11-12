# Methods and interpretation

## Imagery and compositing

The app queries the `COPERNICUS/S2_SR_HARMONIZED` surface-reflectance and
`COPERNICUS/S2_CLOUD_PROBABILITY` collections over each study area and full
calendar year. Images are matched by `system:index`. Scene Classification Layer
(SCL) classes for no data, defective pixels, cloud shadow, unclassified/
low-confidence cloud, medium- and high-probability clouds, cirrus and snow
(classes 0, 1, 3, 7, 8, 9, 10 and 11) are masked; cloud-probability values of 40
or greater are also masked. The remaining clear SWIR (B11), NIR (B8) and green
(B3) observations are median-composited independently for each year. Earth
Engine's median reducer ignores masked pixel values, so every clear observation
in that year contributes to the pixel's temporal median while cloudy observations
do not. Locations with no clear observations for a year remain masked; no
values are borrowed from another year. Matching the cloud-probability collection
also avoids relying on SCL classification alone. The map uses B11/B8/B3 as
red/green/blue, respectively.

Sentinel-2 first became available in 2015, but that partial first year is
excluded from this study. The interface starts at 2016 and uses two-year
intervals by default; the first and last years and interval can be changed.
Each observation is an annual composite, not a single acquisition. If no clear
pixels remain in a location during a year, that location is masked in that
year's composite.

## Waterline proxy

For each composite, the Modified Normalized Difference Water Index is

```text
MNDWI = (green - SWIR) / (green + SWIR)
```

where green is B3 and SWIR is B11. Pixels with MNDWI greater than zero are
classified as water, while pixels at or below zero form the land mask for this
demonstration. The threshold is fixed in this version and should be treated as
an exploratory choice, not a universal coastline classifier. The land mask is
vectorized at 20 m for transect crossings. The results map shows each year's
detected waterline as a differently coloured vector outline with no fill; a
legend identifies the colours and the year-specific layers can be toggled.
False-colour composites are also available as toggleable layers. Section 2 uses
OpenStreetMap to help define the area; the imagery result map omits the street
basemap, while the separate transect plan map includes OpenStreetMap.

Water area is reported only as contextual information. It is not used as a
stand-alone measure of erosion: its extent can change with tide, wave run-up,
water turbidity, cloud masking and the index threshold.

## Transect movement

The default Cape Cod study uses the supplied transect lines in
`data/transects/transects.geojson`, projected to the local metric CRS (UTM zone
19N, EPSG:32619). Their digitized direction defines increasing distance along
each transect, and cumulative distance between line midpoints defines the
alongshore coordinate for integration. Uploaded and drawn study areas instead
use generated transects in the local UTM or UPS CRS at the selected spacing and
bearing. For each observation, the app selects the detected waterline crossing
nearest the study-area centroid on each transect. The time-series chart shows
**linear waterline position in metres**, not a topographic beach profile.

A separate plan-view map shows the transects used over the selected study area.

The endpoint displacement is integrated alongshore using trapezoidal
integration between transects. Positive planform change indicates landward
waterline movement for the selected bearing. Transects without a crossing in
both endpoint observations are excluded from this endpoint estimate. A water
polygon touching the AOI boundary can create an artificial edge; a 25 m boundary
buffer is excluded from crossing extraction, but manual review remains
important.

## Indicative volume estimate

The volume estimate multiplies the signed planform-change proxy by an
active-profile height entered by the user:

```text
indicative volume change (m³)
    = - planform change proxy (m²) × assumed active-profile height (m)
```

The sign convention reports landward movement as indicative volume loss. The
profile height is not measured by Sentinel-2, and the result assumes a uniform,
user-supplied active-profile height. It is **not** a beach-volume survey,
sediment budget, or validated estimate of sediment transport. For defensible
volumetrics, use repeat topographic and bathymetric surveys tied to an
appropriate vertical datum and account for profile shape and alongshore
variability.

## Optional elevation profiles

An uploaded single-band projected GeoTIFF is sampled along the generated
transects with rasterio. It must overlap the study area, use a projected
coordinate system, and contain elevation values in known units. This produces a
static profile from the supplied raster only; it does not provide historical
elevation change, nearshore bathymetry, or input to the volume estimate.

## Limitations

- Optical waterlines are tide- and wave-dependent and are not datum-corrected.
- Annual median compositing combines acquisitions from different dates and
  seasons within each calendar year.
- Persistent cloud or shadow cover can leave masked pixels after annual
  compositing; cloud probability and SCL masks cannot guarantee a clear
  observation at every location.
- Clouds, shadows, surf, wet sand, vegetation, estuaries and narrow beaches can
  affect water classification and extracted crossings.
- Transect geometry and the centroid-nearest crossing rule influence results,
  particularly where a transect crosses multiple water bodies.
- Annual medians combine acquisitions across the calendar year and can obscure
  seasonal or storm-scale variability.
- The default 20 m extraction scale and fixed MNDWI threshold are screening
  choices; evaluate sensitivity before interpreting a site.
- Results should be checked against tide records, aerial imagery, field surveys
  and local shoreline definitions before reporting.
