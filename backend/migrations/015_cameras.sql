-- Live cameras: a phone (or later an IP camera) sending pictures and its position; watched in Defence › Cameras.
CREATE TABLE IF NOT EXISTS cameras (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id uuid NOT NULL,
  project_id uuid NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  name text NOT NULL,
  token text NOT NULL UNIQUE,                 -- the camera's secret address: whoever has it can send pictures, nothing else
  last_at timestamptz,                        -- the last picture
  lng double precision, lat double precision, accuracy double precision, heading double precision, located_at timestamptz,
  created_by uuid,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cameras_project ON cameras (project_id);
