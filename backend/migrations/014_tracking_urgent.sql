-- Tracking: urgent alerts from reports (a patrol asking for support, a hostile or suspect sighting), the alert text in zone.
ALTER TABLE track_alerts DROP CONSTRAINT IF EXISTS track_alerts_kind_check;
ALTER TABLE track_alerts ADD CONSTRAINT track_alerts_kind_check CHECK (kind IN ('enter', 'exit', 'new', 'urgent'));
