CREATE TABLE portfolio_alert_firings (
  id        serial      PRIMARY KEY,
  alert_key text        NOT NULL UNIQUE,
  fired_at  timestamp   NOT NULL DEFAULT now()
);

CREATE INDEX idx_portfolio_alert_firings_key ON portfolio_alert_firings (alert_key);
