-- A SESSÃO que um disparo confirma, para um alerta de "avaliar no fechamento".
--
-- Não é redundante com `fired_at`. Numa confirmação atrasada os dois divergem:
-- o fechamento de uma sexta pode ser confirmado na manhã de segunda, e aí
-- `session_date` é a sexta e `fired_at` é a segunda. Deduplicar por `fired_at`
-- (ou pelo dia de bolsa dele) erra os dois lados -- deixa a sexta confirmada na
-- segunda bloquear o fechamento da própria segunda, e não bloqueia nada se o
-- relógio virar entre dois disparos da mesma sessão.
--
-- NULL nos disparos de alerta intradiário e nos anteriores a esta coluna: eles
-- não confirmam sessão nenhuma, são eventos de momento.
ALTER TABLE alert_firings ADD COLUMN IF NOT EXISTS session_date text;

-- O índice serve à pergunta que o checker faz a cada ciclo: "este alerta já
-- confirmou esta sessão?".
CREATE INDEX IF NOT EXISTS idx_alert_firings_sessao
  ON alert_firings(alert_id, session_date);
