import { sql } from "drizzle-orm";
import { db } from "@workspace/db";
import { logger } from "./logger";

// Garante no boot as colunas exigidas por features novas quando o banco ainda
// não recebeu `pnpm --filter db push` (ex.: processo reiniciado sem o
// post-merge hook rodar). Statements idempotentes — espelham
// lib/db/migrations/0008_settings_cash.sql e 0009_agent_runs_usage.sql.
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
}
