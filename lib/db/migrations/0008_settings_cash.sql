-- Caixa disponível (USD não investido) por modo de carteira (real/paper).
-- Espelha o "Disponível para investir" da corretora; entra no Patrimônio
-- total mas não conta como valor investido.
ALTER TABLE settings ADD COLUMN IF NOT EXISTS cash_real numeric(15,4) NOT NULL DEFAULT 0;
ALTER TABLE settings ADD COLUMN IF NOT EXISTS cash_simulated numeric(15,4) NOT NULL DEFAULT 0;
