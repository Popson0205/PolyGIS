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

2. **Geoprocessing**: buffer, clip, intersect, union, dissolve, spatial join and more, run as jobs, results as layers.
3. **Rasters and print layouts**: raster import and analysis (slope, hillshade, reclassify, zonal statistics), and
   layouts with legend, scale bar, north arrow and grid to PDF.
4. **Sharing**: publish to Geohub's data hub, live layers from the other products, a model builder, 3D, a desktop app
   wrapping the same web app.

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
