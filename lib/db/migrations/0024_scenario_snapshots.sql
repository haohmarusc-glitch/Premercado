CREATE TABLE IF NOT EXISTS scenario_snapshots (
  id serial PRIMARY KEY,
  user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  snapshot_date text NOT NULL,
  data_alvo text NOT NULL,
  dias_restantes integer NOT NULL,
  p_empate numeric(15, 4) NOT NULL,
  valor_total_hoje numeric(15, 4) NOT NULL,
  custo_total numeric(15, 4) NOT NULL,
  p05 numeric(15, 4) NOT NULL,
  p50 numeric(15, 4) NOT NULL,
  p95 numeric(15, 4) NOT NULL,
  created_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT uq_scenario_snapshots_user_date UNIQUE (user_id, snapshot_date)
);
CREATE INDEX IF NOT EXISTS idx_scenario_snapshots_user_id ON scenario_snapshots (user_id);

CREATE TABLE IF NOT EXISTS scenario_resolutions (
  id serial PRIMARY KEY,
  user_id integer NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  data_alvo text NOT NULL,
  valor_final numeric(15, 4) NOT NULL,
  custo_total numeric(15, 4) NOT NULL,
  p_empate_final numeric(15, 4) NOT NULL,
  bateu boolean NOT NULL,
  resolved_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT uq_scenario_resolutions_user_alvo UNIQUE (user_id, data_alvo)
);
CREATE INDEX IF NOT EXISTS idx_scenario_resolutions_user_id ON scenario_resolutions (user_id);
