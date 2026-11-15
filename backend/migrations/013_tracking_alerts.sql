-- Tracking: alerts when a new device is seen (not only geofences), WhatsApp delivery of alerts, and what the last ask of a feed found.
ALTER TABLE track_alerts ALTER COLUMN zone DROP NOT NULL;
ALTER TABLE track_alerts DROP CONSTRAINT IF EXISTS track_alerts_kind_check;
ALTER TABLE track_alerts ADD CONSTRAINT track_alerts_kind_check CHECK (kind IN ('enter', 'exit', 'new'));
ALTER TABLE track_alerts ADD COLUMN IF NOT EXISTS notified boolean NOT NULL DEFAULT true;    -- those from before: not sent
ALTER TABLE track_alerts ALTER COLUMN notified SET DEFAULT false;
CREATE INDEX IF NOT EXISTS track_alerts_unnotified ON track_alerts (tracker_id) WHERE NOT notified;

ALTER TABLE trackers ADD COLUMN IF NOT EXISTS asked_at timestamptz;      -- the last ask of the feed that was answered
ALTER TABLE trackers ADD COLUMN IF NOT EXISTS last_seen integer;         -- how many it answered with (in the area)
ALTER TABLE trackers ADD COLUMN IF NOT EXISTS notified_at timestamptz;   -- the last WhatsApp message sent (or tried)
ALTER TABLE trackers ADD COLUMN IF NOT EXISTS notify_error text;
