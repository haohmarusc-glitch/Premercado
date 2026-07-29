CREATE TABLE IF NOT EXISTS scenario_alert_settings (
  user_id integer PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  data_alvo text NOT NULL,
  threshold_pct numeric(15, 4) NOT NULL DEFAULT 50,
  enabled boolean NOT NULL DEFAULT true,
  notify_email text,
  last_fired_at timestamp,
  updated_at timestamp NOT NULL DEFAULT now(),
  created_at timestamp NOT NULL DEFAULT now()
);
