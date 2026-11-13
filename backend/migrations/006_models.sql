-- Model builder: a chain of geoprocessing and raster tools saved as a model the organization can run again on other
-- layers. Inputs are named; each step's layer parameters take an input or an earlier step's result.
CREATE TABLE models (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  description text NOT NULL DEFAULT '',
  inputs jsonb NOT NULL DEFAULT '[]',          -- [{name, label, kinds: ["Point"|"LineString"|"Polygon"|"Raster"]}]
  steps jsonb NOT NULL DEFAULT '[]',           -- [{id, tool, kind: "vector"|"raster", params, output_name}]
  runs int NOT NULL DEFAULT 0,
  created_by uuid,
  created_by_name text NOT NULL DEFAULT '',
  updated_by_name text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX models_org_idx ON models (org_id, updated_at DESC);
