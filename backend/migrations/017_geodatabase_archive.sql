-- Archiving a geodatabase: its features are packed into a GeoPackage in object storage and removed from the database,
-- so a cold project's data does not sit in the central DB. The manifest keeps each layer's definition (name, style,
-- fields, geometry, raster pointer, position) so the geodatabase can be restored exactly, and the counts so the
-- catalog can still show how much is archived.
ALTER TABLE geodatabases
  ADD COLUMN manifest jsonb,                    -- layer definitions + counts captured at archive time (null when live)
  ADD COLUMN archived_at timestamptz;
