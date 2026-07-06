import bcrypt from "bcryptjs";
import jwt from "jsonwebtoken";
import crypto from "node:crypto";

export const SESSION_COOKIE = "session";

const SALT_ROUNDS = 10;
const SESSION_TTL = "30d";

function getJwtSecret(): string {
  const secret = process.env.JWT_SECRET;
  if (!secret) {
    // Falha alto e explícito no boot -- um segredo default silencioso
    // tornaria trivial forjar cookies de sessão contra um app público.
    throw new Error("JWT_SECRET environment variable is required but was not provided.");
  }
  return secret;
}

export function hashPassword(plain: string): Promise<string> {
  return bcrypt.hash(plain, SALT_ROUNDS);
}

export function verifyPassword(plain: string, hash: string): Promise<boolean> {
  return bcrypt.compare(plain, hash);
}

// Senha placeholder pra conta seed -- aleatória e nunca revelada, só existe
// pra a conta ter uma linha válida em `users` até ser reivindicada via
// /auth/claim-seed-account (ver claim-seed-account.ts).
export function generateUnusablePassword(): string {
  return crypto.randomBytes(32).toString("hex");
}

export function signSessionToken(userId: number): string {
  return jwt.sign({ sub: userId }, getJwtSecret(), { expiresIn: SESSION_TTL });
}

export function verifySessionToken(token: string): { userId: number } | null {
  try {
    const payload = jwt.verify(token, getJwtSecret());
    if (typeof payload === "object" && payload !== null && typeof payload.sub === "string") {
      const userId = Number(payload.sub);
      if (Number.isFinite(userId)) return { userId };
    }
    return null;
  } catch {
    return null;
  }
}

export const SESSION_COOKIE_OPTIONS = {
  httpOnly: true,
  secure: process.env.NODE_ENV === "production",
  sameSite: "lax" as const,
  maxAge: 30 * 24 * 60 * 60 * 1000, // 30 dias, espelha SESSION_TTL
  path: "/",
};
