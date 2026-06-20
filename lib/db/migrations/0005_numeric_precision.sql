-- Migra colunas de double precision para numeric(15,4) para evitar
-- erros de arredondamento em valores financeiros.
-- USING garante conversão segura de dados existentes.

ALTER TABLE observations
  ALTER COLUMN price_at_observation TYPE numeric(15,4)
    USING price_at_observation::numeric(15,4);

ALTER TABLE alerts
  ALTER COLUMN threshold_pct   TYPE numeric(15,4) USING threshold_pct::numeric(15,4),
  ALTER COLUMN threshold_price TYPE numeric(15,4) USING threshold_price::numeric(15,4);

ALTER TABLE alert_firings
  ALTER COLUMN threshold_pct       TYPE numeric(15,4) USING threshold_pct::numeric(15,4),
  ALTER COLUMN threshold_price     TYPE numeric(15,4) USING threshold_price::numeric(15,4),
  ALTER COLUMN change_pct_at_firing TYPE numeric(15,4) USING change_pct_at_firing::numeric(15,4),
  ALTER COLUMN price_at_firing     TYPE numeric(15,4) USING price_at_firing::numeric(15,4);

ALTER TABLE portfolio_positions
  ALTER COLUMN quantity        TYPE numeric(15,4) USING quantity::numeric(15,4),
  ALTER COLUMN avg_cost        TYPE numeric(15,4) USING avg_cost::numeric(15,4),
  ALTER COLUMN invested_amount TYPE numeric(15,4) USING invested_amount::numeric(15,4);

ALTER TABLE portfolio_purchases
  ALTER COLUMN amount         TYPE numeric(15,4) USING amount::numeric(15,4),
  ALTER COLUMN purchase_price TYPE numeric(15,4) USING purchase_price::numeric(15,4),
  ALTER COLUMN sale_price     TYPE numeric(15,4) USING sale_price::numeric(15,4);
