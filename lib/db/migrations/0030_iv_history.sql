-- Série diária de IV ATM por ticker.
--
-- O gate de IV do relatório precisa julgar a IV de hoje contra o histórico do
-- PRÓPRIO papel (IV Rank/Percentile) -- SMCI vive perto de 95% e NVDA quase
-- nunca passa de 45%, então corte único não discrimina. O yfinance só devolve
-- a cadeia AO VIVO: não existe série histórica para consultar nem como
-- preencher retroativamente, então a única forma de ter rank é começar a
-- gravar. Até ~60 pregões acumulados o gate segue no proxy de vol realizada.
--
-- Índice único (ticker, date): mais de uma run no mesmo dia acontece (em
-- 31/07 saíram três) e não pode duplicar a série.
CREATE TABLE IF NOT EXISTS iv_history (
  id            SERIAL PRIMARY KEY,
  ticker        TEXT NOT NULL,
  date          TEXT NOT NULL,
  atm_iv_pct    NUMERIC NOT NULL,
  atr_pct       NUMERIC,
  recorded_at   TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_iv_history_ticker_date ON iv_history (ticker, date);
CREATE INDEX IF NOT EXISTS idx_iv_history_ticker ON iv_history (ticker);
