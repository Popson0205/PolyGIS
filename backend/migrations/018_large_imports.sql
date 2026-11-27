-- Large (gigabyte / national) vector datasets: ingested server-side by reference, streamed
-- straight into PostGIS, and drawn only as vector tiles. Such a layer is marked `big` so the
-- map renders it from tiles while bulk table/export are guarded (a filter is required first).

ALTER TABLE layers ADD COLUMN IF NOT EXISTS big boolean NOT NULL DEFAULT false;

-- One background ingest of a server-side file into a layer.
CREATE TABLE IF NOT EXISTS imports (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL,
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,
  layer_id uuid REFERENCES layers ON DELETE SET NULL,     -- the layer being filled (null once deleted)
  source text NOT NULL,                                   -- file name under the server ingest directory
  name text NOT NULL,
  status text NOT NULL DEFAULT 'queued'                   -- queued | running | done | failed
    CHECK (status IN ('queued', 'running', 'done', 'failed')),
  features bigint NOT NULL DEFAULT 0,                      -- features loaded so far
  total bigint,                                           -- features expected (from the file header), if known
  message text,                                           -- error detail when failed
  created_by uuid,
  created_by_name text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS imports_project_idx ON imports (project_id, created_at DESC);
