CREATE TABLE IF NOT EXISTS exit_plan_items (
  id serial PRIMARY KEY,
  ticker text NOT NULL,
  phase integer NOT NULL,
  phase_label text NOT NULL,
  target_date text NOT NULL,
  action text NOT NULL,
  rationale text NOT NULL,
  event_date text,
  status text NOT NULL DEFAULT 'pending',
  sold_at text,
  sold_price numeric(15, 4),
  user_id integer REFERENCES users(id) ON DELETE CASCADE,
  created_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_exit_plan_items_user_id ON exit_plan_items(user_id);
CREATE INDEX IF NOT EXISTS idx_exit_plan_items_ticker ON exit_plan_items(ticker);
