-- Paper Trading: carteira virtual paralela à carteira real
ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS is_simulated boolean NOT NULL DEFAULT false;

CREATE INDEX IF NOT EXISTS idx_portfolio_positions_simulated
  ON portfolio_positions (is_simulated);
