import { Request, Response, NextFunction } from "express";
import { logger } from "../lib/logger";

declare module "express-session" {
  interface SessionData {
    authenticated: boolean;
  }
}

export function requireAuth(req: Request, res: Response, next: NextFunction): void {
  if (req.session?.authenticated) {
    return next();
  }

  const apiKey = process.env.OPERATOR_API_KEY?.trim();
  if (apiKey) {
    const authHeader = req.headers["authorization"];
    if (authHeader === `Bearer ${apiKey}`) {
      return next();
    }
  } else {
    logger.warn("OPERATOR_API_KEY is not configured — bearer token auth disabled");
  }

  res.status(401).json({ error: "Unauthorized" });
}
