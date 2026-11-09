-- Raster layers (elevation models, imagery, results of raster tools) live in the same list as vector layers, so they
-- share drawing order, visibility and naming. Their cells are in a Cloud-Optimized GeoTIFF in storage; `raster`
-- describes it (size, coordinate system, bands and their statistics).
ALTER TABLE layers DROP CONSTRAINT IF EXISTS layers_geometry_type_check;
ALTER TABLE layers ADD CONSTRAINT layers_geometry_type_check CHECK (geometry_type IN ('Point','LineString','Polygon','Raster'));
ALTER TABLE layers ADD COLUMN raster jsonb;
