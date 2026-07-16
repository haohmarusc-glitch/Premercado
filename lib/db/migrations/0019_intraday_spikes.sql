CREATE TABLE IF NOT EXISTS intraday_spikes (
  id serial PRIMARY KEY,
  ticker text NOT NULL,
  kind text NOT NULL,
  severity text NOT NULL,
  title text NOT NULL,
  detail text NOT NULL,
  value numeric(15, 4),
  fired_at timestamp NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_intraday_spikes_ticker ON intraday_spikes(ticker);
CREATE INDEX IF NOT EXISTS idx_intraday_spikes_fired_at ON intraday_spikes(fired_at);
