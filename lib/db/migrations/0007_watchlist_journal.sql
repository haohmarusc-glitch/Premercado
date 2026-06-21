CREATE TABLE IF NOT EXISTS watchlist (
  id serial PRIMARY KEY,
  ticker text NOT NULL UNIQUE,
  notes text,
  added_at timestamp NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS trade_journal (
  id serial PRIMARY KEY,
  ticker text NOT NULL,
  entry_date text NOT NULL,
  entry_price numeric(15,4),
  stop_loss numeric(15,4),
  target_price numeric(15,4),
  thesis text,
  emotional_state text NOT NULL DEFAULT 'neutral',
  exit_date text,
  exit_price numeric(15,4),
  result text,
  notes text,
  created_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now()
);
ALTER TABLE observations ADD COLUMN IF NOT EXISTS user_notes text;
