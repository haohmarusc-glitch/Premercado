-- Alertas por condição técnica (RSI/MACD/cruzamento de médias), além dos já
-- existentes por preço/variação %. indicator='price' preserva o
-- comportamento original para todos os alertas já cadastrados.
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS indicator text NOT NULL DEFAULT 'price';
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS threshold_value numeric(15,4);

ALTER TABLE alert_firings ADD COLUMN IF NOT EXISTS indicator text NOT NULL DEFAULT 'price';
ALTER TABLE alert_firings ADD COLUMN IF NOT EXISTS threshold_value numeric(15,4);
ALTER TABLE alert_firings ADD COLUMN IF NOT EXISTS value_at_firing numeric(15,4);
