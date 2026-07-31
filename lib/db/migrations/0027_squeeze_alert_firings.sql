CREATE TABLE IF NOT EXISTS squeeze_alert_firings (
  id serial PRIMARY KEY,
  alert_key text NOT NULL UNIQUE,
  fired_at timestamp NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_squeeze_alert_firings_key ON squeeze_alert_firings(alert_key);
