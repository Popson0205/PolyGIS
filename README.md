# PolyGIS

A full GIS in the browser, for the work people do in ArcGIS Pro or QGIS: build maps from many layers, style and label
them, edit features with snapping, select and query, measure, and export. Part of the GeoEstate platform, beside Geohub,
Land Registry, LandCheck and FarmScan: one sign-in (GeoEstate ID, through Geohub), one licence system, one look.

## Phase 1: maps and editing (this release)

- **Projects** for the organization, each with its own coordinate system for the read-out (any EPSG system; the
  Nigerian grids, Minna / UTM 31N and 32N and the Nigeria belts, are offered first).
- **Add data**: GeoJSON, zipped Shapefile, GeoPackage, KML/KMZ, CSV (longitude/latitude or WKT columns), DXF, GPX. Every
  layer in a file comes in; a layer that mixes points, lines and polygons is split into one layer per kind. Data is
  reprojected to WGS 84 with the proper datum shift (a `.prj` in Esri's form is matched to its EPSG system so the shift
  is not lost). A file without a system is refused with what to choose, unless its coordinates are longitude/latitude.
- **Contents pane**: visibility, drawing order, zoom to layer, attribute table, legend.
- **Symbology**: single symbol, unique values (categories) or graduated colours (quantile or equal interval, five
  ColorBrewer ramps); outline, width, size, opacity. **Labels** from any field.
- **Fields**: add, rename (values, style and labels follow), delete; a field calculator for all or the selected features.
- **Attribute table**: a page at a time, sorted by any field, searched, all or only the selection; click to select,
  double-click to zoom; cells edited in place while the layer is being edited.
- **Selection**: click (Shift to add), by a drawn shape, by attributes (several conditions), by location (intersects,
  within, contains, within a distance of another layer, or only its selection), switch, clear, zoom to.
- **Editing**: points, lines and polygons drawn on the map with **snapping** to vertices and edges of every visible
  layer at full precision (not the rounded map tiles); reshape (drag, add and delete vertices); delete; **undo** of your
  own edits; every edit recorded with who and when.
- **Measure** distances, areas and perimeters (geodesic).
- **Export** a layer or its selection as GeoPackage, zipped Shapefile, GeoJSON, CSV with WKT or KML, in any coordinate
  system (GeoJSON and KML stay in WGS 84).
- **Status bar** coordinates in the project's system, worked out in the browser with the server's own definition,
  datum shift included, so they agree with the server to the millimetre.
- **Basemaps** that may be used in a paid product without a key: OpenFreeMap streets and light grey, Sentinel-2
  cloudless (EOX, CC BY 4.0), or none; `VITE_IMAGERY_URL` adds licensed high-resolution imagery.

Roles come from Geohub: owners and admins are **admins** (delete anyone's project), members are **editors**,
collectors and viewers are **viewers** (open and export). An expired licence is read-only; a suspended one is closed.

## Next phases

2. **Geoprocessing**: 107 vector tools in nine groups (proximity, overlay, generalization, conversion, topology, sampling, spatial statistics such as hot spots and Moran's I, and network analysis such as routes, closest facility and service areas), run as jobs, results as layers.
3. **Rasters and print layouts**: raster import and analysis (see Rasters below); print layouts (see Print layouts below).
4. **Sharing**: share links, publishing to Geohub's Data hub, linked layers from the other products and 3D (see below);
   a model builder (see below); next, a desktop app wrapping the same web app.

## Print layouts

Share › Print layout (or Insert › New layout) opens the sheet as it will print, beside its settings (Advanced plan,
`layouts` feature). A4 to A0, Letter, Legal and Tabloid, portrait or landscape; the map at a set scale (1:N, true at the
map's centre), moved by dragging it on the sheet and rescaled by scrolling; title, subtitle and notes; a legend in a
side column, a band below the map, inside the map or none (the visible layers, as Contents shows them); a scale bar, a
north arrow, and a grid of the project's coordinate system with its eastings and northings (or degrees and minutes)
round the frame; a footer with the system, the date, who printed it and the basemap's credits. Export makes a PDF with
sharp vector text and lines and the map as a picture at 150, 200 or 300 dpi (less on very large sheets, where a browser
cannot draw that much: the sheet says so), or a PNG. Layouts are saved with the project. The page is drawn in the
browser (`src/lib/layout.ts`), so the preview and the PDF are the same drawing.

## Share links

Share › Share link makes a read-only link to the map: the layers chosen, opening where the map is when the link is made,
with their legend, a feature's details on click, a choice of basemap and, if allowed, downloads of those layers. A link
is for anyone who has it (no sign-in) or only for people of the organization (signed in); it can end after a week, 30 or
90 days or a year; it shows the layers as they are when opened, so later edits appear; "show the map as it is now"
updates its view and layers without changing its address; turning it off stops it at once. Views are counted and
downloads recorded in the activity log. A shared page may be embedded in another site (an iframe).

## Publishing to the Data hub

Share › Publish layer sends the active vector layer (or only its selection) to Geohub's Data hub as a dataset, as the
person signed in (their GeoEstate ID access token): title, description, licence, tags, and who may see it (only them,
their organization, or anyone). Geohub keeps each publication as a version that never changes, with previews and
downloads; publishing the layer again adds the next version to a dataset published from it before. Field names travel
as column labels. Up to 50,000 features at a time. Needs Geohub with `/id/data/datasets` (Geohub PR "Data hub: datasets
published from the other GeoEstate apps").

## Linked layers

Insert › Linked layer shows data that lives in another GeoEstate app as a layer that follows it: Land Registry's parcel
fabric (PIN, title, land use, area, locality, survey plan; coloured by land use), FarmScan's farms (farmer, crop, area,
latest NDVI; coloured red to green), or the records of a Geohub form (approved ones, or all). PolyGIS reads them as the
person signed in, so each shows only what they may see in that app: Geohub's data with their own access token, Land
Registry and FarmScan with a short token from Geohub's token exchange (GeoHub PR 46; Land Registry PR 17 and FarmScan
PR 13 accept it). The features are copied into the layer, so it is styled, labelled, queried, analysed, printed and
shared like any other; it is refreshed from its app on demand and when the project opens (if older than ten minutes),
and is changed in its app, not here. `LAND_API` and `FARM_API` override the apps' API addresses (default: their
addresses from Geohub, with `/api` for Land Registry).

## 3D

Map › 3D view tilts the map onto terrain: an elevation model in the project (served as Terrarium tiles from
`/layers/{id}/raster/terrain/…`) or, anywhere, the open Terrain Tiles (Mapzen, AWS Open Data), with 1× to 5×
exaggeration; right-drag to turn and tilt. Polygons can be drawn in 3D (Symbology › Draw in 3D): their height from a
numeric field times a factor (3 m per storey, say) or a fixed height. Shared maps show the heights too; print layouts
are drawn flat, from above.

## Model builder

Analysis › Model builder chains geoprocessing and raster tools into a model the organization saves and runs again on
other layers, as ArcGIS's ModelBuilder does without scripting. A model names its inputs (Boreholes: points, say);
each step is one tool whose layer settings take an input or the result of an earlier step, shown as a flow diagram.
Run… asks for each input's layer in the project and keeps the final result (and any step marked to keep) or every
step's result; the layers in between are removed. Each step runs exactly as the tool does alone, so it shows in the
project's history; when one fails, the run stops there and says which step and why.

The API: `GET/POST /models`, `PATCH/DELETE /models/{id}` and `POST /projects/{id}/models/{model_id}/run` with
`{"inputs": {"boreholes": "<layer id>"}, "keep": "final"}`. Saving and running need an editor and geoprocessing in
the plan; a model with raster steps needs PolyGIS Advanced.

## How it is built

FastAPI with PostgreSQL and PostGIS (the `gis` schema of the platform database) behind; React with TypeScript, Vite and
MapLibre GL in front; Terra Draw for drawing. Layers are served to the map as vector tiles straight from PostGIS
(`ST_AsMVT`). Files are read and written with GDAL (pyogrio, which brings its own GDAL).

## Rasters

Elevation models, drone and satellite imagery, and the results of raster tools are raster layers, listed with the
vector layers. Each is kept as a Cloud-Optimized GeoTIFF (rasterio, with its own GDAL) and drawn as map tiles warped
to Web Mercator from the overview that suits the zoom; clicking one shows its values. 69 raster tools (Advanced plan,
`raster` feature): terrain (slope, aspect, hillshade, curvature, TPI, TRI, roughness, VRM, contours, viewshed, cut and
fill, profiles), hydrology (fill, flow direction and accumulation, streams with Strahler order, watersheds, basins,
wetness index, flow length), map algebra (raster calculator, NDVI, SAVI, EVI, reclassify, slice, cell statistics),
focal and zonal statistics, distance and cost distance, kernel density, interpolation (IDW, TIN, spline), conversion
(project, resample, clip, mosaic, bands, raster to and from features) and image classification. Cell sizes are worked
out in metres on the ground, also for rasters in longitude and latitude.

## Open remote-sensing data

No need to download scenes or tiles by hand: **Add data > Get data from an open source** fetches the part inside the area
you choose (what the map shows, or the selected layer's extent), with **only the bands you tick**, and adds it as a raster
layer in the UTM zone of the area.

| Data | Bands | Sources |
| --- | --- | --- |
| Sentinel-2 imagery (10 m) | Red, Green, Blue, NIR (usual); red-edge 1-3, NIR narrow, SWIR 1-2 (20 m) | Planetary Computer, Earth Search, Earth Engine |
| Landsat 8 and 9 imagery (30 m) | Red, Green, Blue, NIR (usual); SWIR 1-2 | Planetary Computer, Earth Engine |
| Sentinel-1 radar (10 m, through cloud) | VV, VH (linear power) | Planetary Computer, Earth Engine |
| Copernicus DEM (30 m) | Elevation (m) | Planetary Computer, Earth Search, Earth Engine |
| NASADEM, SRTM-based (30 m) | Elevation (m) | Planetary Computer, Earth Engine |
| ESA WorldCover land cover (10 m, 2021) | Class (drawn in the official colours) | Planetary Computer, Earth Engine |
| CHIRPS rainfall (about 5.5 km) | Total over the period (mm) | Earth Engine |

- **Microsoft Planetary Computer** and **Earth Search** (Element 84, AWS) are public STAC catalogues: no account or key.
  Files are read from their Cloud-Optimized GeoTIFFs, only the part inside the area and only the bands asked for; scenes
  are joined (least cloudy first for optical imagery, newest first for radar) where one does not cover the area.
- **Google Earth Engine** is offered once the server has a project (`GEE_PROJECT`), with a service account
  (`GEE_SERVICE_ACCOUNT` and `GEE_PRIVATE_KEY`, the key file's JSON or a path to it) or the credentials the server's
  environment already holds. Optical scenes are cloud-masked and combined as a median composite. Earth Engine is free for
  non-commercial and research use; commercial use needs a paid Earth Engine licence on that Google Cloud project.

*Find scenes* (optical imagery, and radar from the catalogues) lists what is there, with date and cloud cover, so scenes can
be chosen by hand; otherwise PolyGIS picks them. A layer is drawn in true colour when it has red, green and blue, in false
colour (NIR, red, green) when it has those, and otherwise as a colour ramp on its first band. Optical bands come out
the same from every source, as surface reflectance times 10 000 (unsigned 16-bit, 0 for no data), so band ratios such as
NDVI are right: Sentinel-2 scenes from 25 January 2022 on (processing baseline 04.00) lose the +1000 offset ESA adds where the
source has not already taken it off, and Landsat Collection 2 values (DN × 0.0000275 − 0.2) are rescaled. Elevation and
radar are 32-bit floats, with -32768 for no data. Fewer bands mean finer cells for a large area: `IMAGERY_MAX_MB` (128) is the memory
one request may use, `IMAGERY_GEE_MAX_MB` (40) the same for Earth Engine, whose direct download is limited to about 48 MB.
Cells are enlarged (up to 8 times the data's own) to fit, then the area is refused. `IMAGERY_TIMEOUT_S` (240) limits a request.

## Poly Earth: satellite analysis without code

The analyses people write Google Earth Engine scripts for, as tools with a form (ribbon tab **Poly Earth**; PolyGIS Advanced).
They run on PolyGIS's own server with open data, so no Google account or Earth Engine licence is needed: Sentinel-2 and
Landsat 8/9 (Planetary Computer, Earth Search), Sentinel-1 radar, Copernicus DEM, ESA WorldCover and JRC Global Surface
Water (Planetary Computer), and CHIRPS monthly rainfall (Climate Hazards Center, UCSB).

| Group | Tools |
| --- | --- |
| Images and indices | Cloud-free composite (median, greenest pixel, most recent, mean, min, max, percentile); spectral index (NDVI, EVI, SAVI, MSAVI, GNDVI, NDRE, NDWI, MNDWI, NDMI, NBR, NDBI, BSI); your own band formula; land surface temperature (Landsat, deg C); rainfall total (CHIRPS); terrain (elevation, slope, aspect, hillshade); surface water history 1984-2020 |
| Time series and change | Time series chart for points, polygons or the area (index, rainfall, temperature, radar VV/VH; by scene, month or year; CSV, and fields on the places); trend over the years (slope per year at every pixel); anomaly against earlier years (index or rainfall, mm and % of normal); change between two dates with areas; burn severity (dNBR, USGS classes); water change; flood map from Sentinel-1 |
| Classification | Supervised land cover (random forest, CART, SVM, naive Bayes, minimum distance) from training points or polygons, with held-back places for the error matrix, overall accuracy, kappa, producer's and user's accuracy, and class areas; unsupervised k-means |
| Statistics | Index statistics for each polygon; land cover breakdown (hectares and share of each WorldCover class) |

How it matches Earth Engine: clouds are masked pixel by pixel before scenes are combined (Sentinel-2 SCL classes 0, 1, 3, 8,
9, 10, 11; Landsat QA_PIXEL bits 0-4); optical values are surface reflectance with Sentinel-2's baseline 04.00 offset removed
and Landsat's Collection 2 scale applied; indices come from the composite; flood mapping follows UN-SPIDER's Recommended
Practice (VH, 50 m smoothing, after/before ratio of dB above 1.25, permanent water of 10+ months removed, slopes of 5 degrees
or more and patches under 8 pixels removed). Classification accuracy is checked on whole training places held back
(not pixels from the places it learnt from), so it is not flattered; where a class has only one place it says so.

A run reads many scenes, so it runs in the background with its progress shown, and its layers are added when it finishes
(the pane can be closed meanwhile); runs are kept in the geoprocessing history. `EARTH_MAX_MB` (384) is the memory one run's
scenes may use (larger areas get larger cells), a run is stopped only when it makes no progress for `EARTH_STALL_S` (600), or after `EARTH_TIMEOUT_S` (10 800, 0 for never) in all; `EARTH_WORKERS` (12) the
files read at once and `EARTH_JOBS` (2) the runs at once on the server. Runs in progress are held in the server's memory: a
restart or redeploy stops them.

## Setting it up

1. In Geohub's variables: add PolyGIS to `ID_CLIENTS`
   (`{"id":"gis","name":"PolyGIS","redirect_uris":["https://<polygis>/api/auth/callback"],"secret":"<a long random secret>"}`)
   and `gis=https://<polygis>` to `PRODUCT_URLS`.
2. A Railway service from this repository (the Dockerfile builds the web app and the API into one image), with
   `DATABASE_URL` (Geohub's database), `APP_URL`, `GEOHUB_URL`, `GEOHUB_API`, `ID_CLIENT=gis`, `ID_SECRET` (the same
   secret). The `gis` schema is made on start-up. Optional: `MAX_UPLOAD_MB` (200), `MAX_FEATURES` (500000),
   `VITE_IMAGERY_URL` and `VITE_IMAGERY_ATTRIBUTION`.
   Rasters: set `S3_ENDPOINT`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` and `S3_BUCKET` (Geohub's R2 bucket works; files go
   under `S3_PREFIX`, `polygis/rasters/`), and `RASTER_DIR` is then only a local copy. Without a bucket, mount a
   Railway volume at `RASTER_DIR` (default `./data/rasters`), or rasters are lost when the service restarts.
   Optional: `MAX_RASTER_MB` (1000), `MAX_RASTER_CELLS` (60 million), `MAX_HYDRO_CELLS` (16 million).
3. Give an organization PolyGIS in Geohub's staff console (Products), or from a paid CRM invoice.

## Developing

```
# API on :8012 (needs PostgreSQL with PostGIS)
cd backend && pip install -r requirements-dev.txt && API_PREFIX=/api uvicorn app.main:app --port 8012
# web app on :5176, reaching the API through /api
cd frontend && npm install && npm run dev
```

Tests: `cd backend && cp .env.test.example .env.test && python -m pytest` (a `gis_test` database), and
`cd frontend && npm test`.
