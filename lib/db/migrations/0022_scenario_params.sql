CREATE TABLE IF NOT EXISTS scenario_params (
  ticker text PRIMARY KEY,
  vol_annual numeric(15, 4) NOT NULL,
  beta_sector numeric(15, 4) NOT NULL,
  updated_at timestamp NOT NULL DEFAULT now()
);

-- Seed inicial (estimativas de partida, ver PainelCenarios) -- ON CONFLICT
-- DO NOTHING pra não sobrescrever valores já ajustados manualmente numa
-- reaplicação da migração.
INSERT INTO scenario_params (ticker, vol_annual, beta_sector) VALUES
  ('NVDA', 0.50, 1.10),
  ('SMCI', 0.78, 1.60),
  ('ARM',  0.68, 1.45),
  ('AVGO', 0.42, 0.95),
  ('SKHY', 0.62, 1.30),
  ('MRVL', 0.66, 1.50)
ON CONFLICT (ticker) DO NOTHING;
