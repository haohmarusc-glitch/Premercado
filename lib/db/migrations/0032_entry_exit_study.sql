CREATE TABLE IF NOT EXISTS entry_exit_study_targets (
  id serial PRIMARY KEY,
  user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  ticker text NOT NULL,
  target_price numeric(15, 4) NOT NULL,
  target_date text NOT NULL,
  exit_alert_id integer REFERENCES alerts(id) ON DELETE SET NULL,
  entry_avg_low_alert_id integer REFERENCES alerts(id) ON DELETE SET NULL,
  entry_min_low_alert_id integer REFERENCES alerts(id) ON DELETE SET NULL,
  active boolean NOT NULL DEFAULT true,
  created_at timestamp NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_entry_exit_study_targets_user_id ON entry_exit_study_targets (user_id);

CREATE TABLE IF NOT EXISTS entry_exit_study_history (
  id serial PRIMARY KEY,
  target_id integer NOT NULL REFERENCES entry_exit_study_targets(id) ON DELETE CASCADE,
  calc_date text NOT NULL,
  current_price numeric(15, 4) NOT NULL,
  avg_low_1y numeric(15, 4),
  min_low_1y numeric(15, 4),
  avg_low_6m numeric(15, 4),
  min_low_6m numeric(15, 4),
  vol_annual numeric(15, 4),
  beta_sector numeric(15, 4),
  prob_reach_target numeric(15, 4),
  created_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT uq_entry_exit_study_history_target_date UNIQUE (target_id, calc_date)
);
CREATE INDEX IF NOT EXISTS idx_entry_exit_study_history_target_id ON entry_exit_study_history (target_id);
