import express, { type Express } from "express";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { LeadStore, normalizeWhatsapp } from "./leads.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

export interface AppConfig {
  leadsFile: string;
  adminToken: string | undefined;
  whatsappNumber: string; // dígitos com DDI, ex: 5511987654321
  instagramUrl: string;
  couponCode: string;
  couponDiscount: string; // texto exibido, ex: "10%"
}

export function configFromEnv(): AppConfig {
  return {
    leadsFile: process.env.LEADS_FILE ?? path.join(__dirname, "..", "data", "leads.json"),
    adminToken: process.env.ADMIN_TOKEN,
    whatsappNumber: process.env.WHATSAPP_NUMBER ?? "",
    instagramUrl: process.env.INSTAGRAM_URL ?? "https://www.instagram.com/docesbeijaflor/",
    couponCode: process.env.COUPON_CODE ?? "BEIJAFLOR10",
    couponDiscount: process.env.COUPON_DISCOUNT ?? "10%",
  };
}

export function createApp(config: AppConfig): Express {
  const app = express();
  const store = new LeadStore(config.leadsFile);

  app.use(express.json());
  app.use(express.static(path.join(__dirname, "..", "public")));

  // Config pública consumida pela landing (número do WhatsApp, cupom etc.) --
  // fica em env var em vez de hardcoded no HTML pra trocar sem redeploy.
  app.get("/api/config", (_req, res) => {
    res.json({
      whatsappNumber: config.whatsappNumber,
      instagramUrl: config.instagramUrl,
      couponCode: config.couponCode,
      couponDiscount: config.couponDiscount,
    });
  });

  app.post("/api/leads", (req, res) => {
    const { name, whatsapp, source } = req.body ?? {};
    if (typeof name !== "string" || name.trim().length < 2) {
      res.status(400).json({ error: "Informe seu nome." });
      return;
    }
    if (typeof whatsapp !== "string") {
      res.status(400).json({ error: "Informe seu WhatsApp." });
      return;
    }
    const normalized = normalizeWhatsapp(whatsapp);
    if (!normalized) {
      res.status(400).json({ error: "WhatsApp inválido — use DDD + número, ex: (11) 98765-4321." });
      return;
    }
    const src = typeof source === "string" && source.trim() ? source.trim().slice(0, 40) : "site";
    const { lead, created } = store.upsert(name.trim().slice(0, 80), normalized, src);
    res.status(created ? 201 : 200).json({
      couponCode: config.couponCode,
      couponDiscount: config.couponDiscount,
      alreadyRegistered: !created,
      leadId: lead.id,
    });
  });

  // Lista de leads -- protegida por token (header X-Admin-Token). Sem
  // ADMIN_TOKEN configurado a rota fica desligada por completo, nunca aberta.
  app.get("/api/leads", (req, res) => {
    if (!config.adminToken) {
      res.status(503).json({ error: "ADMIN_TOKEN não configurado no servidor." });
      return;
    }
    if (req.get("x-admin-token") !== config.adminToken) {
      res.status(401).json({ error: "Token inválido." });
      return;
    }
    res.json(store.list());
  });

  return app;
}
