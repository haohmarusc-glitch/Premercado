import type { Request, Response, NextFunction } from "express";
import { eq } from "drizzle-orm";
import { db, usersTable } from "@workspace/db";
import { SESSION_COOKIE, verifySessionToken } from "../lib/auth";
import { SEED_OWNER_EMAIL } from "../lib/claim-seed-account";
import { logger } from "../lib/logger";

// Cache simples em memória do id da conta dona -- resolvida uma vez por
// processo, já que só muda se o e-mail seed mudar (não muda em runtime).
let cachedOwnerUserId: number | null = null;

async function resolveOwnerUserId(): Promise<number | null> {
  if (cachedOwnerUserId != null) return cachedOwnerUserId;
  try {
    const [owner] = await db
      .select({ id: usersTable.id })
      .from(usersTable)
      .where(eq(usersTable.email, SEED_OWNER_EMAIL))
      .limit(1);
    if (owner) cachedOwnerUserId = owner.id;
    return owner?.id ?? null;
  } catch (err) {
    logger.error({ err }, "Failed to resolve owner user id for OPERATOR_API_KEY auth");
    return null;
  }
}

// Aceita DOIS caminhos de autenticação:
// 1. Cookie de sessão (usuário logado pelo browser).
// 2. Bearer OPERATOR_API_KEY (agente Python / carteira.py, que já mandam esse
//    header hoje mas nunca foi validado em lugar nenhum) -- age como a conta
//    dona (mesma que recebe o backfill dos dados existentes).
export async function requireAuth(req: Request, res: Response, next: NextFunction): Promise<void> {
  const authHeader = req.headers.authorization;
  const operatorKey = process.env.OPERATOR_API_KEY;

  // DIAGNÓSTICO TEMPORÁRIO -- investigando 401 persistente em produção no
  // caminho Bearer OPERATOR_API_KEY (ver .agents/memory -- issue de 12-13/jul).
  // Só loga booleanos/tamanhos, nunca o valor da chave/header. Remover depois
  // de identificar a causa raiz.
  if (authHeader?.startsWith("Bearer ")) {
    logger.info({
      hasOperatorKeyEnv: !!operatorKey,
      operatorKeyLength: operatorKey?.length ?? 0,
      authHeaderLength: authHeader.length,
      keysMatch: !!operatorKey && authHeader === `Bearer ${operatorKey}`,
      path: req.path,
    }, "DIAGNOSTICO requireAuth: tentativa de Bearer auth");
  }

  if (operatorKey && authHeader === `Bearer ${operatorKey}`) {
    const ownerUserId = await resolveOwnerUserId();
    if (authHeader?.startsWith("Bearer ")) {
      logger.info({ ownerUserId }, "DIAGNOSTICO requireAuth: resolveOwnerUserId apos match de chave");
    }
    if (ownerUserId != null) {
      req.userId = ownerUserId;
      next();
      return;
    }
  }

  const token = req.cookies?.[SESSION_COOKIE];
  const payload = typeof token === "string" ? verifySessionToken(token) : null;
  if (!payload) {
    res.status(401).json({ error: "Not authenticated" });
    return;
  }

  req.userId = payload.userId;
  next();
}

// Usado nas rotas que só o administrador pode ver (ex.: histórico de runs do
// agente). Deve rodar DEPOIS de requireAuth (precisa de req.userId já setado).
export async function requireAdmin(req: Request, res: Response, next: NextFunction): Promise<void> {
  const [user] = await db
    .select({ isAdmin: usersTable.isAdmin })
    .from(usersTable)
    .where(eq(usersTable.id, req.userId!))
    .limit(1);
  if (!user?.isAdmin) {
    res.status(403).json({ error: "Admin access required" });
    return;
  }
  next();
}
