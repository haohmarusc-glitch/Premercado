-- E-mail de notificação por registro (alerta/posição), em vez de um único
-- notify_email global em settings -- cada alerta/posição escolhe o próprio
-- destinatário na criação, sem precisar consultar outra tabela ao disparar.
ALTER TABLE alerts ADD COLUMN IF NOT EXISTS notify_email text;
ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS notify_email text;

-- Flag de administrador -- só quem tem isso vê o menu/página Runs.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin boolean NOT NULL DEFAULT false;
