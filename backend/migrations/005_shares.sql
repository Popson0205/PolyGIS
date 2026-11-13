-- Share links: a read-only view of a project's map that anyone with the link can open without signing in (or, if
-- the link says so, only people of the organization). Each link names the layers it shows; it can end on a date,
-- allow downloading those layers, and be revoked.
CREATE TABLE shares (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,
  org_id uuid NOT NULL,
  token text NOT NULL UNIQUE,
  title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
  description text NOT NULL DEFAULT '',
  layer_ids uuid[] NOT NULL DEFAULT '{}',
  view jsonb NOT NULL DEFAULT '{}',              -- {center, zoom, bearing, basemap}
  audience text NOT NULL DEFAULT 'anyone' CHECK (audience IN ('anyone', 'organization')),
  downloads boolean NOT NULL DEFAULT false,
  expires_at timestamptz,
  revoked_at timestamptz,
  views bigint NOT NULL DEFAULT 0,
  last_viewed_at timestamptz,
  created_by uuid,
  created_by_name text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX shares_project_idx ON shares (project_id, created_at DESC);
