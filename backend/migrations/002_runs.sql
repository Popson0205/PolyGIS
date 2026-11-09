-- Geoprocessing runs: what was run, on which layers, by whom, how it went, and the layer it made.
CREATE TABLE runs (
  id bigserial PRIMARY KEY,
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,
  org_id uuid NOT NULL,
  tool text NOT NULL,
  params jsonb NOT NULL DEFAULT '{}',
  output_layer_id uuid REFERENCES layers ON DELETE SET NULL,
  output_name text,
  status text NOT NULL CHECK (status IN ('done','failed')),
  message text,
  feature_count int,
  ms int,
  actor_id uuid,
  actor_name text NOT NULL DEFAULT '',
  at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX runs_project_idx ON runs (project_id, id DESC);
