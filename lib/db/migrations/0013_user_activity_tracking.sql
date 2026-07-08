-- Rastreio de atividade por usuário (tela de administração: online/offline
-- e última página visitada). Atualizado via heartbeat do frontend.
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at timestamp;
ALTER TABLE users ADD COLUMN IF NOT EXISTS last_path text;
