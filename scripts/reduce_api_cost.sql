-- reduce_api_cost.sql
-- Ajusta o registro de settings JÁ EXISTENTE no banco para reduzir o custo de API.
-- Os defaults do schema só valem para linhas novas; este UPDATE corrige a linha atual.
--
-- Como rodar no Replit (shell):
--   psql "$DATABASE_URL" -f scripts/reduce_api_cost.sql
-- Ou cole o conteúdo no console SQL do seu Postgres.

-- 1) (MAIOR ECONOMIA) Desligar o scan intradiário automático.
--    Se quiser MANTER o scan, comente a linha abaixo e use o bloco 2.
UPDATE settings
SET premarket_enabled = false,
    updated_at = now();

-- 2) Alternativa: manter o scan, mas barato — 1x/hora numa janela curta (08h–10h).
--    Descomente este bloco e comente o UPDATE acima se preferir manter ligado.
-- UPDATE settings
-- SET premarket_enabled = true,
--     premarket_interval_min = 60,
--     premarket_window_start_hour = 8,
--     premarket_window_end_hour = 10,
--     updated_at = now();

-- 3) Conferir o resultado:
SELECT premarket_enabled,
       premarket_interval_min,
       premarket_window_start_hour,
       premarket_window_end_hour,
       schedule_enabled,
       schedule_hour,
       schedule_minute
FROM settings;
