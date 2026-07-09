ALTER TABLE portfolio_purchases
  ADD COLUMN IF NOT EXISTS price_manually_edited boolean NOT NULL DEFAULT false;
