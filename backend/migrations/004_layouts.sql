-- Print layouts: a project's saved map sheets (page size, title, scale, legend, grid and so on). The page is drawn and
-- turned into a PDF in the browser; only the settings are kept here.
CREATE TABLE layouts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,
  org_id uuid NOT NULL,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  settings jsonb NOT NULL DEFAULT '{}',
  created_by uuid,
  created_by_name text NOT NULL DEFAULT '',
  updated_by_name text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX layouts_project_idx ON layouts (project_id, updated_at DESC);
