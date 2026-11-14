-- Forecasting scenarios: a raster time-series forecast and a forecast of values on features.
ALTER TABLE scenarios DROP CONSTRAINT IF EXISTS scenarios_kind_check;
ALTER TABLE scenarios ADD CONSTRAINT scenarios_kind_check
  CHECK (kind IN ('suitability', 'flood', 'land_change', 'spread', 'raster_forecast', 'value_forecast'));
