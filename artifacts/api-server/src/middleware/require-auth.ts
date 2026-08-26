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

// Header de IDENTIDADE do subprocesso. Só é lido no caminho da
// OPERATOR_API_KEY -- nunca no de cookie, onde seria escalação de privilégio
// pura: qualquer usuário logado passaria a ler a conta que quisesse.
export const ACTING_USER_HEADER = "x-acting-user-id";

/**
 * O id que o subprocesso diz estar representando, ou null.
 *
 * Vazamento real (26/08/2026): o agente Python autentica com a
 * OPERATOR_API_KEY, que era resolvida SEMPRE para a conta dona. Então
 * `get_exit_plan_items` e `get_scenario_status` devolviam os dados do dono
 * para qualquer conta que disparasse um Veredito ou abrisse o Chat -- e o
 * modo Reavaliar Plano de Saída podia ESCREVER no plano do dono a mando de
 * outra conta.
 *
 * Sobre o raio da chave: com este header a OPERATOR_API_KEY deixa de dar
 * acesso só ao dono e passa a poder selecionar qualquer usuário. É uma
 * ampliação real, e foi escolhida de olhos abertos -- a alternativa era o
 * estado anterior, em que TODO subprocesso já agia como o dono para todos os
 * usuários, que é estritamente pior. A chave é segredo de servidor, no env do
 * container, mesmo nível de confiança das credenciais do banco; quem a tem já
 * podia ler e escrever tudo do dono.
 */
function actingUserIdDoHeader(req: Request): number | null {
  const bruto = req.headers[ACTING_USER_HEADER];
  const texto = Array.isArray(bruto) ? bruto[0] : bruto;
  if (typeof texto !== "string" || !/^[0-9]{1,10}$/.test(texto)) return null;
  const id = Number(texto);
  return Number.isSafeInteger(id) && id > 0 ? id : null;
}

// Aceita DOIS caminhos de autenticação:
// 1. Cookie de sessão (usuário logado pelo browser).
// 2. Bearer OPERATOR_API_KEY (agente Python / carteira.py). Age como o
//    usuário nomeado em X-Acting-User-Id quando ele vem, e como a conta dona
//    quando não vem (carteira.py e scripts do operador, que não representam
//    ninguém).
export async function requireAuth(req: Request, res: Response, next: NextFunction): Promise<void> {
  const authHeader = req.headers.authorization;
  const operatorKey = process.env.OPERATOR_API_KEY;
  if (operatorKey && authHeader === `Bearer ${operatorKey}`) {
    const agindoComo = actingUserIdDoHeader(req);
    if (agindoComo != null) {
      req.userId = agindoComo;
      next();
      return;
    }
    const ownerUserId = await resolveOwnerUserId();
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

// Extraído de requireAdmin pra ser reutilizável em rotas que só precisam da
// checagem condicionalmente (ex.: POST /agent/run, que exige admin só para
// os modos compartilhados -- ver routes/agent.ts).
export async function isAdminUser(userId: number): Promise<boolean> {
  const [user] = await db
    .select({ isAdmin: usersTable.isAdmin })
    .from(usersTable)
    .where(eq(usersTable.id, userId))
    .limit(1);
  return !!user?.isAdmin;
}

// Usado nas rotas que só o administrador pode ver (ex.: histórico de runs do
// agente). Deve rodar DEPOIS de requireAuth (precisa de req.userId já setado).
export async function requireAdmin(req: Request, res: Response, next: NextFunction): Promise<void> {
  if (!(await isAdminUser(req.userId!))) {
    res.status(403).json({ error: "Admin access required" });
    return;
  }
  next();
}
