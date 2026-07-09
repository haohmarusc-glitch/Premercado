ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS is_etf boolean NOT NULL DEFAULT false;
