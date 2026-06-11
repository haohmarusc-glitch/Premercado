-- Adiciona colunas de high/low e alertas caso não existam
-- (idempotente: seguro rodar mesmo se 001 já as criou)
ALTER TABLE portfolio_positions
    ADD COLUMN IF NOT EXISTS high_since_purchase REAL,
    ADD COLUMN IF NOT EXISTS low_since_purchase  REAL,
    ADD COLUMN IF NOT EXISTS high_date           TIMESTAMP,
    ADD COLUMN IF NOT EXISTS low_date            TIMESTAMP,
    ADD COLUMN IF NOT EXISTS alert_high_pct      REAL DEFAULT 20.0,
    ADD COLUMN IF NOT EXISTS alert_low_pct       REAL DEFAULT -10.0;
