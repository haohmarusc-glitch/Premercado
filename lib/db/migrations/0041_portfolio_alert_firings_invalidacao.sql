-- Marca os disparos que sabemos ter sido indevidos. NÃO apaga nenhum.
--
-- A varredura de 03/10/2026 em portfolio_alert_firings achou 98 disparos
-- falsos em 204. Dois defeitos no checker (ver o cabeçalho de
-- portfolio-alerts.ts):
--
--   * 69 alertas de GANHO por divisão por zero. `recomputePosition` zera
--     avg_cost ao vender tudo, e ((price - 0) / 0) * 100 é Infinity, que passa
--     em todos os limiares de uma vez. O e-mail saía dizendo "Infinity%". O
--     caso que fecha o argumento é gain:BABA:50 em 02/10 -- BABA comprada a
--     125,97 e vendida a 105,88, PREJUÍZO de 15,95%.
--   * 29 marcos de HOLDING sobre lote já vendido. holding:META:2026-03-20:180
--     disparou em 16/09 sobre um lote vendido em 07/05, 132 dias depois.
--
-- As linhas ficam porque são a evidência: elas é que datam o defeito e medem o
-- tamanho dele. As colunas abaixo permitem excluí-las de contagens futuras sem
-- destruir nada.
--
-- Isto não muda comportamento nenhum. A dedupe passou a usar chaves `v2:`, que
-- não colidem com as antigas -- marcar uma linha v1 não libera nem bloqueia
-- disparo algum.

ALTER TABLE portfolio_alert_firings ADD COLUMN IF NOT EXISTS invalidated_at timestamp;
ALTER TABLE portfolio_alert_firings ADD COLUMN IF NOT EXISTS invalidation_reason text;

-- ── Marco de holding sobre lote vendido ─────────────────────────────────────
--
-- Preciso no nível do lote: a chave antiga é holding:TICKER:DATA:DIAS, e
-- existe um lote (ou mais, quando foi dividido para venda parcial) com esse
-- ticker e essa data de compra. É falso quando TODOS os lotes daquele
-- ticker+data já estavam vendidos no instante do disparo.
UPDATE portfolio_alert_firings f
SET invalidated_at = now(),
    invalidation_reason = 'lote_vendido'
WHERE f.invalidated_at IS NULL
  AND f.alert_key LIKE 'holding:%'
  AND f.alert_key NOT LIKE 'holding:v2:%'
  AND EXISTS (
    SELECT 1
    FROM portfolio_purchases pu
    JOIN portfolio_positions po ON po.id = pu.position_id
    WHERE po.ticker = split_part(f.alert_key, ':', 2)
      AND pu.purchase_date = split_part(f.alert_key, ':', 3)
  )
  AND NOT EXISTS (
    -- algum lote daquele ticker+data ainda em aberto no instante do disparo
    SELECT 1
    FROM portfolio_purchases pu
    JOIN portfolio_positions po ON po.id = pu.position_id
    WHERE po.ticker = split_part(f.alert_key, ':', 2)
      AND pu.purchase_date = split_part(f.alert_key, ':', 3)
      AND (
        pu.sale_date IS NULL
        OR pu.sale_price IS NULL
        OR pu.sale_date::date >= f.fired_at::date
      )
  );

-- ── Alerta de ganho com custo médio zerado ──────────────────────────────────
--
-- Conservador de propósito: só marca quando NENHUM lote daquele ticker estava
-- em aberto no instante do disparo. Com isso, a rajada de gain:ARM:10..50 de
-- 21/09 NÃO é marcada -- a ARM #12 estava encerrada, mas a ARM #22 seguia
-- aberta, e a chave antiga não diz de qual posição o disparo veio. Deixar de
-- marcar um falso é erro menor que marcar como falso um disparo legítimo.
--
-- Por isso o número marcado aqui é menor que os 69 do relatório; os 69 foram
-- apurados cruzando o horário de cada rajada com o `updated_at` da posição que
-- havia acabado de ser zerada, que é informação que esta tabela não tem.
UPDATE portfolio_alert_firings f
SET invalidated_at = now(),
    invalidation_reason = 'custo_medio_zerado'
WHERE f.invalidated_at IS NULL
  AND f.alert_key LIKE 'gain:%'
  AND f.alert_key NOT LIKE 'gain:v2:%'
  AND EXISTS (
    SELECT 1 FROM portfolio_positions po
    JOIN portfolio_purchases pu ON pu.position_id = po.id
    WHERE po.ticker = split_part(f.alert_key, ':', 2)
  )
  AND NOT EXISTS (
    SELECT 1
    FROM portfolio_positions po
    JOIN portfolio_purchases pu ON pu.position_id = po.id
    WHERE po.ticker = split_part(f.alert_key, ':', 2)
      AND (
        pu.sale_date IS NULL
        OR pu.sale_price IS NULL
        OR pu.sale_date::date >= f.fired_at::date
      )
  );
