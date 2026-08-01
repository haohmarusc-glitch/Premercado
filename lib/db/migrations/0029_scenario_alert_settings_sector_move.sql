ALTER TABLE scenario_alert_settings ADD COLUMN IF NOT EXISTS sector_move_pct numeric(15, 4);
ALTER TABLE scenario_alert_settings ADD COLUMN IF NOT EXISTS sector_move_updated_at timestamp;
