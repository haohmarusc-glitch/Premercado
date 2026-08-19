-- Retrato diário do risco macro setorial (IA/semis). Uma linha por pregão.
--
-- aggregate_score é NULLABLE de propósito: abaixo da cobertura mínima não há
-- score, e gravar 0 ali seria dizer "sem risco" sobre um dia que o sistema não
-- conseguiu medir -- exatamente o bug que macro_risk.py existe para não ter.
CREATE TABLE IF NOT EXISTS macro_risk_snapshots (
  id serial PRIMARY KEY,
  snapshot_date text NOT NULL UNIQUE,
  evaluated_at timestamptz NOT NULL DEFAULT now(),
  aggregate_score integer,
  coverage_pct integer NOT NULL DEFAULT 0,
  active_flags jsonb NOT NULL DEFAULT '[]'::jsonb,
  degraded_sources jsonb NOT NULL DEFAULT '{}'::jsonb,
  raw jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_macro_risk_date ON macro_risk_snapshots(snapshot_date DESC);
