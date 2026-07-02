import { sql } from "drizzle-orm";
import { db } from "@workspace/db";
import { logger } from "./logger";

// Garante no boot as colunas exigidas por features novas quando o banco ainda
// não recebeu `pnpm --filter db push` (ex.: processo reiniciado sem o
// post-merge hook rodar). Statements idempotentes — espelham
// lib/db/migrations/0008_settings_cash.sql.
export async function ensureSchema(): Promise<void> {
  try {
    await db.execute(sql`ALTER TABLE settings ADD COLUMN IF NOT EXISTS cash_real numeric(15,4) NOT NULL DEFAULT 0`);
    await db.execute(sql`ALTER TABLE settings ADD COLUMN IF NOT EXISTS cash_simulated numeric(15,4) NOT NULL DEFAULT 0`);
    logger.info("Schema check ok (settings.cash_real/cash_simulated)");
  } catch (err) {
    logger.error({ err }, "Failed to ensure schema (cash columns)");
  }
}
