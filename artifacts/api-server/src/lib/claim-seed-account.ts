import { eq, isNull } from "drizzle-orm";
import { db, usersTable, portfolioPositionsTable, alertsTable } from "@workspace/db";
import { generateUnusablePassword, hashPassword } from "./auth";
import { logger } from "./logger";

// Email do dono original -- as posições/alertas já existentes no banco antes
// desse deploy ficam vinculados a essa conta. Ver plano de auth (login por
// email/senha, carteira e alertas por usuário). Exportado pra require-auth.ts
// resolver o dono quando a chamada vem autenticada via bearer OPERATOR_API_KEY
// (agente Python / carteira.py), que não tem uma sessão de usuário própria.
export const SEED_OWNER_EMAIL = "haohmarusc@gmail.com";

// Roda em todo boot, depois de ensureSchema(). Idempotente:
// - Só cria a conta seed na primeira vez (não recria se já existir).
// - O backfill (`WHERE user_id IS NULL`) não afeta linhas já vinculadas,
//   então é seguro repetir em todo restart.
export async function claimSeedAccountBootstrap(): Promise<void> {
  try {
    let [seedUser] = await db
      .select({ id: usersTable.id })
      .from(usersTable)
      .where(eq(usersTable.email, SEED_OWNER_EMAIL))
      .limit(1);

    if (!seedUser) {
      const placeholderHash = await hashPassword(generateUnusablePassword());
      const [inserted] = await db
        .insert(usersTable)
        .values({ email: SEED_OWNER_EMAIL, passwordHash: placeholderHash, isClaimed: false })
        .returning({ id: usersTable.id });
      seedUser = inserted;
      logger.info({ email: SEED_OWNER_EMAIL }, "Seed owner account created (unclaimed) — use POST /auth/claim-seed-account to set a real password");
    }

    const positionsBackfilled = await db
      .update(portfolioPositionsTable)
      .set({ userId: seedUser.id })
      .where(isNull(portfolioPositionsTable.userId))
      .returning({ id: portfolioPositionsTable.id });

    const alertsBackfilled = await db
      .update(alertsTable)
      .set({ userId: seedUser.id })
      .where(isNull(alertsTable.userId))
      .returning({ id: alertsTable.id });

    if (positionsBackfilled.length > 0 || alertsBackfilled.length > 0) {
      logger.info(
        { positions: positionsBackfilled.length, alerts: alertsBackfilled.length },
        "Backfilled ownerless portfolio/alerts rows to seed owner account",
      );
    }
  } catch (err) {
    logger.error({ err }, "Failed to run claim-seed-account bootstrap");
  }
}
