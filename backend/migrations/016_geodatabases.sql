-- Project geodatabases: like ArcGIS, every project comes with a default geodatabase, and all its layers and analysis
-- results are stored there. A project can hold several geodatabases; the default one is where new work lands. A
-- geodatabase can be downloaded as a GeoPackage, or (later) archived to object storage so a cold project's data does
-- not sit in the central database.

CREATE TABLE geodatabases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,
  org_id uuid NOT NULL,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  is_default boolean NOT NULL DEFAULT false,
  storage text NOT NULL DEFAULT 'postgis' CHECK (storage IN ('postgis', 'archived')),
  archive_key text,                            -- object-storage key when storage = 'archived'
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX geodatabases_project_idx ON geodatabases (project_id, created_at);
-- at most one default per project
CREATE UNIQUE INDEX geodatabases_one_default ON geodatabases (project_id) WHERE is_default;

-- every layer belongs to a geodatabase
ALTER TABLE layers ADD COLUMN geodatabase_id uuid REFERENCES geodatabases ON DELETE CASCADE;

-- A new project gets a "Default" geodatabase automatically.
CREATE OR REPLACE FUNCTION make_default_gdb() RETURNS trigger AS $$
BEGIN
  INSERT INTO geodatabases (project_id, org_id, name, is_default, created_by)
    VALUES (NEW.id, NEW.org_id, 'Default', true, NEW.created_by);
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER projects_make_default_gdb AFTER INSERT ON projects
  FOR EACH ROW EXECUTE FUNCTION make_default_gdb();

-- Any layer created without a geodatabase (imports, tool and analysis outputs) lands in the project's default one,
-- so no output path has to know about geodatabases.
CREATE OR REPLACE FUNCTION layer_set_gdb() RETURNS trigger AS $$
BEGIN
  IF NEW.geodatabase_id IS NULL THEN
    SELECT id INTO NEW.geodatabase_id FROM geodatabases
      WHERE project_id = NEW.project_id AND is_default LIMIT 1;
  END IF;
  RETURN NEW;
END $$ LANGUAGE plpgsql;
CREATE TRIGGER layers_set_gdb BEFORE INSERT ON layers
  FOR EACH ROW EXECUTE FUNCTION layer_set_gdb();

-- Backfill: give every existing project a default geodatabase and move its layers into it.
INSERT INTO geodatabases (project_id, org_id, name, is_default, created_by)
  SELECT id, org_id, 'Default', true, created_by FROM projects;
UPDATE layers l SET geodatabase_id = g.id
  FROM geodatabases g WHERE g.project_id = l.project_id AND g.is_default;
ALTER TABLE layers ALTER COLUMN geodatabase_id SET NOT NULL;
