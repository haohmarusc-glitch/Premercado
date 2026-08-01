CREATE TABLE IF NOT EXISTS sector_momentum (
  benchmark text PRIMARY KEY,
  momentum_annual_pct numeric(15, 4) NOT NULL,
  lookback_days integer NOT NULL,
  updated_at timestamp NOT NULL DEFAULT now()
);
