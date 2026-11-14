-- Georeferencing: an image being placed on the map from control points. The image (or the raster being re-placed) is
-- kept as a file; its points are saved as they are added, so the work survives closing the window.
CREATE TABLE georef_sources (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,
  org_id uuid NOT NULL,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  key text NOT NULL,                           -- the image file in raster storage
  owns_key boolean NOT NULL DEFAULT true,      -- false when it is an existing layer's file (not removed with this)
  layer_id uuid REFERENCES layers ON DELETE SET NULL,
  width int NOT NULL,
  height int NOT NULL,
  band_count int NOT NULL,
  had_crs text,                                -- the coordinate system the raster had, when re-placing one
  points jsonb NOT NULL DEFAULT '[]',          -- [{id, col, row, lng, lat, on}]
  method text NOT NULL DEFAULT 'affine',
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX georef_sources_project_idx ON georef_sources (project_id, updated_at DESC);
