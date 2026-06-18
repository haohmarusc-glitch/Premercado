ALTER TABLE alerts
  ADD COLUMN IF NOT EXISTS threshold_price double precision,
  ALTER COLUMN threshold_pct DROP NOT NULL;

ALTER TABLE alert_firings
  ADD COLUMN IF NOT EXISTS threshold_price double precision,
  ALTER COLUMN threshold_pct DROP NOT NULL,
  ALTER COLUMN change_pct_at_firing DROP NOT NULL;
