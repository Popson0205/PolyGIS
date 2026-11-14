-- Scenarios: named what-if settings for a simulation (suitability, flood, land change, spread), run into a raster
-- layer with a summary of figures, varied by duplicating and compared side by side.
CREATE TABLE scenarios (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,
  org_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN ('suitability', 'flood', 'land_change', 'spread')),
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  notes text NOT NULL DEFAULT '',
  params jsonb NOT NULL DEFAULT '{}',
  status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'ready', 'failed')),
  summary jsonb,
  error text,
  layer_id uuid REFERENCES layers ON DELETE SET NULL,
  ran_at timestamptz,
  run_ms int,
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX scenarios_project_idx ON scenarios (project_id, kind, created_at);
