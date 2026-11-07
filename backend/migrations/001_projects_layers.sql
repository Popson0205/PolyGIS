-- PolyGIS: an organization's projects (maps), their layers, and every layer's features in one spatially indexed
-- table. Geometry is kept in WGS 84 (EPSG:4326); each layer remembers the system its data came in, and people can
-- show and type coordinates in any system.

CREATE TABLE projects (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  description text NOT NULL DEFAULT '',
  view jsonb NOT NULL DEFAULT '{}',            -- {center:[lng,lat], zoom, bearing, pitch, basemap}
  display_epsg int NOT NULL DEFAULT 4326,      -- coordinates shown in the status bar and typed in
  created_by uuid,
  created_by_name text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX projects_org_idx ON projects (org_id, updated_at DESC);

CREATE TABLE layers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,
  org_id uuid NOT NULL,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  geometry_type text NOT NULL CHECK (geometry_type IN ('Point','LineString','Polygon')),   -- multi-parts allowed
  fields jsonb NOT NULL DEFAULT '[]',          -- [{name, type: string|integer|number|boolean|date}]
  style jsonb NOT NULL DEFAULT '{}',
  labels jsonb,                                -- {field, size, color} or null
  visible boolean NOT NULL DEFAULT true,
  position int NOT NULL DEFAULT 0,             -- drawing order: higher draws on top
  source jsonb NOT NULL DEFAULT '{}',          -- {filename, format, crs} it was imported from
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX layers_project_idx ON layers (project_id, position);

CREATE TABLE features (
  id bigserial PRIMARY KEY,
  layer_id uuid NOT NULL REFERENCES layers ON DELETE CASCADE,
  geom geometry(Geometry, 4326) NOT NULL,
  props jsonb NOT NULL DEFAULT '{}',
  updated_by uuid,
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX features_layer_idx ON features (layer_id, id);
CREATE INDEX features_geom_idx ON features USING gist (geom);

-- Every edit, so a change can be undone and anyone can see who changed what.
CREATE TABLE edits (
  id bigserial PRIMARY KEY,
  layer_id uuid NOT NULL REFERENCES layers ON DELETE CASCADE,
  feature_id bigint NOT NULL,
  action text NOT NULL CHECK (action IN ('add','change','delete')),
  before jsonb,                                -- {geometry, properties} before the edit (null for an add)
  after jsonb,                                 -- after it (null for a delete)
  actor_id uuid,
  actor_name text NOT NULL DEFAULT '',
  at timestamptz NOT NULL DEFAULT now(),
  undone_at timestamptz                        -- set when someone undid this edit
);
CREATE INDEX edits_layer_idx ON edits (layer_id, id DESC);

CREATE TABLE audit_log (
  id bigserial PRIMARY KEY,
  org_id uuid,
  actor_id uuid,
  actor_name text NOT NULL DEFAULT '',
  action text NOT NULL,
  target_type text,
  target_id text,
  details jsonb NOT NULL DEFAULT '{}',
  at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX audit_org_idx ON audit_log (org_id, at DESC);
