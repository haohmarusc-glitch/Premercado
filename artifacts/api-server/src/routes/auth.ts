import { Router, type IRouter } from "express";
import { eq } from "drizzle-orm";
import { db, usersTable } from "@workspace/db";
import {
  AuthSignupBody,
  AuthLoginBody,
  AuthLoginResponse as AuthUserSchema,
  GetAuthMeResponse,
  AuthClaimSeedAccountBody,
  AuthClaimSeedAccountResponse,
} from "@workspace/api-zod";
import {
  SESSION_COOKIE,
  SESSION_COOKIE_OPTIONS,
  hashPassword,
  verifyPassword,
  signSessionToken,
  verifySessionToken,
} from "../lib/auth";
import { SEED_OWNER_EMAIL } from "../lib/claim-seed-account";
import { logger } from "../lib/logger";

const router: IRouter = Router();

function normalizeEmail(email: string): string {
  return email.trim().toLowerCase();
}

function setSessionCookie(res: import("express").Response, userId: number): void {
  res.cookie(SESSION_COOKIE, signSessionToken(userId), SESSION_COOKIE_OPTIONS);
}

router.post("/auth/signup", async (req, res): Promise<void> => {
  const parsed = AuthSignupBody.safeParse(req.body);
  if (!parsed.success) { res.status(400).json({ error: parsed.error.message }); return; }

  const email = normalizeEmail(parsed.data.email);
  const [existing] = await db.select({ id: usersTable.id }).from(usersTable).where(eq(usersTable.email, email)).limit(1);
  if (existing) { res.status(409).json({ error: "Email already registered" }); return; }

  const passwordHash = await hashPassword(parsed.data.password);
  const [user] = await db
    .insert(usersTable)
    .values({ email, passwordHash, isClaimed: true })
    .returning({ id: usersTable.id, email: usersTable.email });

  setSessionCookie(res, user.id);
  res.status(201).json(AuthUserSchema.parse(user));
});

router.post("/auth/login", async (req, res): Promise<void> => {
  const parsed = AuthLoginBody.safeParse(req.body);
  if (!parsed.success) { res.status(400).json({ error: parsed.error.message }); return; }

  const email = normalizeEmail(parsed.data.email);
  const [user] = await db.select().from(usersTable).where(eq(usersTable.email, email)).limit(1);

  // Mensagem genérica em qualquer caso de falha -- nunca revela se o email
  // existe ou não.
  const invalid = () => { res.status(401).json({ error: "Invalid email or password" }); };

  if (!user || !user.isClaimed) { invalid(); return; }

  const ok = await verifyPassword(parsed.data.password, user.passwordHash);
  if (!ok) { invalid(); return; }

  setSessionCookie(res, user.id);
  res.json(AuthUserSchema.parse({ id: user.id, email: user.email }));
});

router.post("/auth/logout", (_req, res): void => {
  res.clearCookie(SESSION_COOKIE, SESSION_COOKIE_OPTIONS);
  res.status(204).end();
});

router.get("/auth/me", async (req, res): Promise<void> => {
  const token = req.cookies?.[SESSION_COOKIE];
  const payload = typeof token === "string" ? verifySessionToken(token) : null;
  if (!payload) { res.json(GetAuthMeResponse.parse({ user: null })); return; }

  const [user] = await db
    .select({ id: usersTable.id, email: usersTable.email })
    .from(usersTable)
    .where(eq(usersTable.id, payload.userId))
    .limit(1);

  res.json(GetAuthMeResponse.parse({ user: user ?? null }));
});

router.post("/auth/claim-seed-account", async (req, res): Promise<void> => {
  const parsed = AuthClaimSeedAccountBody.safeParse(req.body);
  if (!parsed.success) { res.status(400).json({ error: parsed.error.message }); return; }

  const email = normalizeEmail(parsed.data.email);
  if (email !== SEED_OWNER_EMAIL) {
    res.status(403).json({ error: "Email does not match the seed account" });
    return;
  }

  const [user] = await db.select().from(usersTable).where(eq(usersTable.email, email)).limit(1);
  if (!user || user.isClaimed) {
    res.status(403).json({ error: "Seed account not found or already claimed" });
    return;
  }

  const passwordHash = await hashPassword(parsed.data.newPassword);
  await db.update(usersTable).set({ passwordHash, isClaimed: true }).where(eq(usersTable.id, user.id));

  logger.info({ email }, "Seed owner account claimed");
  setSessionCookie(res, user.id);
  res.json(AuthClaimSeedAccountResponse.parse({ id: user.id, email: user.email }));
});

export default router;
