ALTER TABLE entry_exit_study_history ADD COLUMN IF NOT EXISTS news jsonb;

CREATE TABLE IF NOT EXISTS entry_exit_study_resolutions (
  id serial PRIMARY KEY,
  target_id integer NOT NULL REFERENCES entry_exit_study_targets(id) ON DELETE CASCADE,
  ticker text NOT NULL,
  target_price numeric(15, 4) NOT NULL,
  target_date text NOT NULL,
  final_price numeric(15, 4) NOT NULL,
  bateu boolean NOT NULL,
  prob_final numeric(15, 4),
  resolved_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT uq_entry_exit_study_resolutions_target UNIQUE (target_id)
);
CREATE INDEX IF NOT EXISTS idx_entry_exit_study_resolutions_target_id ON entry_exit_study_resolutions (target_id);
