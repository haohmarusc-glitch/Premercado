import { eq, isNull, sql } from "drizzle-orm";
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

    // Dono original é sempre admin (único jeito de promover conta hoje é por
    // este backfill/SQL direto -- não tem tela de administração). Idempotente.
    await db.update(usersTable).set({ isAdmin: true }).where(eq(usersTable.id, seedUser.id));

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

    // Alertas/posições criados antes deste recurso não têm notify_email
    // próprio -- preenche a partir do e-mail de login do dono (user_id já
    // resolvido acima). Não afeta linhas que já têm notify_email definido.
    const alertsEmailBackfilled = await db.execute(sql`
      UPDATE alerts SET notify_email = users.email
      FROM users
      WHERE alerts.user_id = users.id AND alerts.notify_email IS NULL
    `);
    const positionsEmailBackfilled = await db.execute(sql`
      UPDATE portfolio_positions SET notify_email = users.email
      FROM users
      WHERE portfolio_positions.user_id = users.id AND portfolio_positions.notify_email IS NULL
    `);
    if ((alertsEmailBackfilled.rowCount ?? 0) > 0 || (positionsEmailBackfilled.rowCount ?? 0) > 0) {
      logger.info(
        { alerts: alertsEmailBackfilled.rowCount, positions: positionsEmailBackfilled.rowCount },
        "Backfilled notify_email from owner's login email",
      );
    }
  } catch (err) {
    logger.error({ err }, "Failed to run claim-seed-account bootstrap");
  }
}
