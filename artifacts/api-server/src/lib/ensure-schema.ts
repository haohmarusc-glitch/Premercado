import { sql } from "drizzle-orm";
import { db } from "@workspace/db";
import { logger } from "./logger";

// Garante no boot as colunas exigidas por features novas quando o banco ainda
// não recebeu `pnpm --filter db push` (ex.: processo reiniciado sem o
// post-merge hook rodar). Statements idempotentes — espelham
// lib/db/migrations/0008_settings_cash.sql, 0009_agent_runs_usage.sql e
// 0009_alerts_technical_indicator.sql.
export async function ensureSchema(): Promise<void> {
  try {
    await db.execute(sql`ALTER TABLE settings ADD COLUMN IF NOT EXISTS cash_real numeric(15,4) NOT NULL DEFAULT 0`);
    await db.execute(sql`ALTER TABLE settings ADD COLUMN IF NOT EXISTS cash_simulated numeric(15,4) NOT NULL DEFAULT 0`);
    logger.info("Schema check ok (settings.cash_real/cash_simulated)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (cash columns)");
  }

  try {
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS input_tokens integer`);
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS output_tokens integer`);
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS cache_read_tokens integer`);
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS cache_write_tokens integer`);
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS cost_usd numeric(12,6)`);
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS llm_provider text`);
    await db.execute(sql`ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS llm_model text`);
    logger.info("Schema check ok (agent_runs usage/cost columns)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (agent_runs usage columns)");
  }

  try {
    await db.execute(sql`ALTER TABLE alerts ADD COLUMN IF NOT EXISTS indicator text NOT NULL DEFAULT 'price'`);
    await db.execute(sql`ALTER TABLE alerts ADD COLUMN IF NOT EXISTS threshold_value numeric(15,4)`);
    await db.execute(sql`ALTER TABLE alert_firings ADD COLUMN IF NOT EXISTS indicator text NOT NULL DEFAULT 'price'`);
    await db.execute(sql`ALTER TABLE alert_firings ADD COLUMN IF NOT EXISTS threshold_value numeric(15,4)`);
    await db.execute(sql`ALTER TABLE alert_firings ADD COLUMN IF NOT EXISTS value_at_firing numeric(15,4)`);
    logger.info("Schema check ok (alerts technical indicator columns)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (alerts technical indicator columns)");
  }

  try {
    await db.execute(sql`
      CREATE TABLE IF NOT EXISTS users (
        id serial PRIMARY KEY,
        email text NOT NULL UNIQUE,
        password_hash text NOT NULL,
        is_claimed boolean NOT NULL DEFAULT true,
        created_at timestamp NOT NULL DEFAULT now(),
        updated_at timestamp NOT NULL DEFAULT now()
      )
    `);
    await db.execute(sql`ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE`);
    await db.execute(sql`ALTER TABLE alerts ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_portfolio_positions_user_id ON portfolio_positions(user_id)`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_alerts_user_id ON alerts(user_id)`);
    logger.info("Schema check ok (users table + portfolio/alerts ownership columns)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (users/ownership columns)");
  }

  try {
    await db.execute(sql`ALTER TABLE alerts ADD COLUMN IF NOT EXISTS notify_email text`);
    await db.execute(sql`ALTER TABLE portfolio_positions ADD COLUMN IF NOT EXISTS notify_email text`);
    await db.execute(sql`ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin boolean NOT NULL DEFAULT false`);
    logger.info("Schema check ok (notify_email per-record + users.is_admin)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (notify_email/is_admin columns)");
  }

  try {
    await db.execute(sql`ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE`);
    await db.execute(sql`ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE`);
    await db.execute(sql`ALTER TABLE trade_journal ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_chat_sessions_user_id ON chat_sessions(user_id)`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_watchlist_user_id ON watchlist(user_id)`);
    await db.execute(sql`CREATE INDEX IF NOT EXISTS idx_trade_journal_user_id ON trade_journal(user_id)`);
    await db.execute(sql`ALTER TABLE watchlist DROP CONSTRAINT IF EXISTS watchlist_ticker_key`);
    await db.execute(sql`
      DO $$
      BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_watchlist_user_ticker') THEN
          ALTER TABLE watchlist ADD CONSTRAINT uq_watchlist_user_ticker UNIQUE (user_id, ticker);
        END IF;
      END $$;
    `);
    logger.info("Schema check ok (chat_sessions/watchlist/trade_journal ownership)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (chat/watchlist/journal ownership)");
  }

  try {
    await db.execute(sql`ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at timestamp`);
    await db.execute(sql`ALTER TABLE users ADD COLUMN IF NOT EXISTS last_path text`);
    logger.info("Schema check ok (users.last_seen_at/last_path)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (users activity tracking columns)");
  }
}
