ALTER TABLE portfolio_purchases
  ADD COLUMN IF NOT EXISTS purchase_price double precision,
  ADD COLUMN IF NOT EXISTS sale_date text,
  ADD COLUMN IF NOT EXISTS sale_price double precision;
