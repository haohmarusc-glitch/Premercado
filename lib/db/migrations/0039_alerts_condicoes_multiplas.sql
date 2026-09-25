-- Alerta com mais de uma condição (E): preço > 365 E rvol > 1.2.
--
-- O `DEFAULT '[]'` nas linhas existentes é deliberado e NÃO é backfill
-- pendente. A conversão do formato antigo (indicator + threshold_* em colunas
-- separadas) para a lista de condições acontece na LEITURA, em
-- alert-conditions.ts::condicoesDoAlerta. Dois motivos:
--
--   1. A precedência de threshold_price sobre threshold_pct teria de ser
--      reimplementada aqui em SQL -- terceira cópia de uma regra que já quebrou
--      quando tinha duas (ver agent/volume_intradiario.py).
--   2. Uma migração que escreva a condição errada num alerta que manda e-mail
--      sobre dinheiro real é difícil de desfazer. Uma derivação na leitura é
--      sempre coerente com as colunas, e as colunas ficam intactas.
--
-- Lista vazia não dispara (avaliarCondicoes), então uma linha antiga com '[]'
-- nunca fica no estado "dispara sempre" -- ela continua sendo avaliada pelas
-- colunas antigas, exatamente como antes desta migração.
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS conditions jsonb NOT NULL DEFAULT '[]'::jsonb;

-- Avaliar só após 16:00 ET, com fechamento e RVOL do dia inteiro.
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS confirm_at_close boolean NOT NULL DEFAULT false;

-- Disparar uma vez e desativar, em vez do cooldown de 4h.
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS fire_once boolean NOT NULL DEFAULT false;

-- Nota/origem em texto livre ("Chat 25/09 -- confirmação de reversão").
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS note text;

-- Retrato das condições no momento do disparo, com o valor lido de cada uma.
-- As colunas threshold_* guardam uma condição só e não têm onde pôr o RVOL.
ALTER TABLE alert_firings ADD COLUMN IF NOT EXISTS conditions jsonb NOT NULL DEFAULT '[]'::jsonb;
