-- Live tracking: feeds of positions (own devices and apps, aircraft by ADS-B, ships by AIS, Geohub form submissions),
-- each kept as a point layer of the latest position of every device, with the recent trail and geofence alerts.
CREATE TABLE IF NOT EXISTS trackers (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id  uuid NOT NULL REFERENCES projects (id) ON DELETE CASCADE,
    org_id      uuid NOT NULL,
    name        text NOT NULL,
    kind        text NOT NULL CHECK (kind IN ('device', 'adsb', 'ais', 'geohub')),
    token       text NOT NULL UNIQUE,                 -- in the address devices send to; secret
    config      jsonb NOT NULL DEFAULT '{}'::jsonb,   -- area, source, form, geofence layer, how long trails are kept
    secret      jsonb NOT NULL DEFAULT '{}'::jsonb,   -- API keys; never sent back
    layer_id    uuid REFERENCES layers (id) ON DELETE SET NULL,
    active      boolean NOT NULL DEFAULT true,
    last_at     timestamptz,                          -- the last position received
    refreshed_at timestamptz,                         -- the last time a feed was asked (ADS-B, AIS, Geohub)
    last_error  text,
    created_by  uuid,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS trackers_project ON trackers (project_id);

CREATE TABLE IF NOT EXISTS track_points (
    id          bigserial PRIMARY KEY,
    tracker_id  uuid NOT NULL REFERENCES trackers (id) ON DELETE CASCADE,
    device      text NOT NULL,
    ts          timestamptz NOT NULL,
    geom        geometry(Point, 4326) NOT NULL,
    speed_kmh   real,
    course      real,
    altitude    real,
    props       jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS track_points_device ON track_points (tracker_id, device, ts DESC);
CREATE INDEX IF NOT EXISTS track_points_ts ON track_points (ts);

CREATE TABLE IF NOT EXISTS track_devices (
    tracker_id  uuid NOT NULL REFERENCES trackers (id) ON DELETE CASCADE,
    device      text NOT NULL,
    name        text,
    feature_id  bigint,                                -- its point in the tracker's layer
    last_ts     timestamptz,
    zones       text[] NOT NULL DEFAULT '{}',          -- the geofences it is inside
    PRIMARY KEY (tracker_id, device)
);

CREATE TABLE IF NOT EXISTS track_alerts (
    id          bigserial PRIMARY KEY,
    tracker_id  uuid NOT NULL REFERENCES trackers (id) ON DELETE CASCADE,
    project_id  uuid NOT NULL,
    device      text NOT NULL,
    name        text,
    kind        text NOT NULL CHECK (kind IN ('enter', 'exit')),
    zone        text NOT NULL,
    ts          timestamptz NOT NULL,
    geom        geometry(Point, 4326),
    seen        boolean NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS track_alerts_project ON track_alerts (project_id, ts DESC);
