-- Several maps in a project, as in ArcGIS Pro. Each layer sits in one map; the same data can be in several maps,
-- each styling and labelling it its own way: a layer that shares another's data points to it (data_id) and owns no
-- features of its own. Each map keeps its own view and basemap. Existing projects get one map, "Map", holding every
-- layer and the project's view.
CREATE TABLE maps (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id uuid NOT NULL REFERENCES projects ON DELETE CASCADE,
  org_id uuid NOT NULL,
  name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  position int NOT NULL DEFAULT 0,
  view jsonb NOT NULL DEFAULT '{}',            -- {center:[lng,lat], zoom, bearing, pitch, basemap}
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX maps_project_idx ON maps (project_id, position);

ALTER TABLE projects ADD COLUMN active_map_id uuid REFERENCES maps ON DELETE SET NULL;
ALTER TABLE layers ADD COLUMN map_id uuid REFERENCES maps ON DELETE CASCADE;
ALTER TABLE layers ADD COLUMN data_id uuid REFERENCES layers ON DELETE CASCADE;   -- the layer whose features this one shows
CREATE INDEX layers_map_idx ON layers (map_id, position);
CREATE INDEX layers_data_idx ON layers (data_id) WHERE data_id IS NOT NULL;

INSERT INTO maps (project_id, org_id, name, position, view, created_by)
  SELECT id, org_id, 'Map', 0, view, created_by FROM projects;
UPDATE projects p SET active_map_id = m.id FROM maps m WHERE m.project_id = p.id;
UPDATE layers l SET map_id = m.id FROM maps m WHERE m.project_id = l.project_id;

-- A layer made without saying its map goes into the map the project has open (else its first map), so every way of
-- adding a layer (import, tools, Poly Earth, scenarios, georeferencing, linked data) lands in the map being worked on.
CREATE FUNCTION layers_default_map() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.map_id IS NULL THEN
    SELECT coalesce(p.active_map_id, (SELECT m.id FROM maps m WHERE m.project_id = NEW.project_id ORDER BY m.position, m.created_at LIMIT 1))
      INTO NEW.map_id FROM projects p WHERE p.id = NEW.project_id;
  END IF;
  IF NEW.map_id IS NULL THEN                    -- a project with no map yet (made before its first map): make one
    INSERT INTO maps (project_id, org_id, name, view) SELECT id, org_id, 'Map', view FROM projects WHERE id = NEW.project_id
      RETURNING id INTO NEW.map_id;
    UPDATE projects SET active_map_id = NEW.map_id WHERE id = NEW.project_id AND active_map_id IS NULL;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER layers_default_map BEFORE INSERT ON layers FOR EACH ROW EXECUTE FUNCTION layers_default_map();

-- A new project starts with one map.
CREATE FUNCTION projects_first_map() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE mid uuid;
BEGIN
  INSERT INTO maps (project_id, org_id, name, view, created_by) VALUES (NEW.id, NEW.org_id, 'Map', NEW.view, NEW.created_by) RETURNING id INTO mid;
  UPDATE projects SET active_map_id = mid WHERE id = NEW.id;
  RETURN NULL;
END $$;
CREATE TRIGGER projects_first_map AFTER INSERT ON projects FOR EACH ROW EXECUTE FUNCTION projects_first_map();

-- The layer whose features a layer shows: itself, or the one it shares data with.
CREATE FUNCTION layer_data(l uuid) RETURNS uuid LANGUAGE sql STABLE PARALLEL SAFE AS
  $$ SELECT coalesce(data_id, id) FROM layers WHERE id = l $$;

-- Layers that show the same data have the same fields: a change to one reaches the others.
CREATE FUNCTION layers_same_fields() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  UPDATE layers SET fields = NEW.fields
   WHERE coalesce(data_id, id) = coalesce(NEW.data_id, NEW.id) AND id <> NEW.id AND fields IS DISTINCT FROM NEW.fields;
  RETURN NULL;
END $$;
CREATE TRIGGER layers_same_fields AFTER UPDATE OF fields ON layers FOR EACH ROW
  WHEN (OLD.fields IS DISTINCT FROM NEW.fields) EXECUTE FUNCTION layers_same_fields();
