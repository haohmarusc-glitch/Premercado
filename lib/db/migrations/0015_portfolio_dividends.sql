ALTER TABLE portfolio_positions
  ADD COLUMN IF NOT EXISTS dividends numeric(15,4) NOT NULL DEFAULT 0;
