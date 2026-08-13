ALTER TABLE entry_exit_study_history ADD COLUMN IF NOT EXISTS prob_reach_target_momentum numeric(15, 4);
ALTER TABLE entry_exit_study_history ADD COLUMN IF NOT EXISTS momentum_annual_pct numeric(15, 4);
