-- chat_sessions, watchlist e trade_journal eram globais -- qualquer conta
-- logada via qualquer login enxergava as conversas/watchlist/diario de
-- TODOS os usuarios (achado testando com uma conta comum nova). Aplica o
-- mesmo padrao de dono ja usado em alerts/portfolio_positions.
ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE;
ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_id ON chat_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_watchlist_user_id ON watchlist(user_id);
CREATE INDEX IF NOT EXISTS idx_trade_journal_user_id ON trade_journal(user_id);

-- watchlist.ticker era UNIQUE global (um usuario so no app todo); agora
-- cada usuario pode ter os mesmos tickers na propria lista, sem duplicar
-- para ELE.
ALTER TABLE watchlist DROP CONSTRAINT IF EXISTS watchlist_ticker_key;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'uq_watchlist_user_ticker'
  ) THEN
    ALTER TABLE watchlist ADD CONSTRAINT uq_watchlist_user_ticker UNIQUE (user_id, ticker);
  END IF;
END $$;
