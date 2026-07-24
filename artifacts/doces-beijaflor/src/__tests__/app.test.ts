import { describe, it, expect, beforeEach } from "vitest";
import request from "supertest";
import path from "node:path";
import fs from "node:fs";
import os from "node:os";
import { createApp, type AppConfig } from "../app.js";
import { normalizeWhatsapp } from "../leads.js";

function makeConfig(overrides: Partial<AppConfig> = {}): AppConfig {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "beijaflor-"));
  return {
    leadsFile: path.join(dir, "leads.json"),
    adminToken: "segredo",
    whatsappNumber: "5511987654321",
    instagramUrl: "https://www.instagram.com/docesbeijaflor/",
    couponCode: "BEIJAFLOR10",
    couponDiscount: "10%",
    ...overrides,
  };
}

describe("normalizeWhatsapp", () => {
  it("aceita formatos comuns e normaliza com DDI 55", () => {
    expect(normalizeWhatsapp("(11) 98765-4321")).toBe("5511987654321");
    expect(normalizeWhatsapp("11987654321")).toBe("5511987654321");
    expect(normalizeWhatsapp("+55 11 98765-4321")).toBe("5511987654321");
    expect(normalizeWhatsapp("21 3232-1010")).toBe("552132321010");
  });

  it("rejeita números inválidos", () => {
    expect(normalizeWhatsapp("123")).toBeNull();
    expect(normalizeWhatsapp("abc")).toBeNull();
    expect(normalizeWhatsapp("(01) 98765-4321")).toBeNull();
    expect(normalizeWhatsapp("119876543210000")).toBeNull();
  });
});

describe("POST /api/leads", () => {
  let config: AppConfig;
  beforeEach(() => {
    config = makeConfig();
  });

  it("cadastra um lead e devolve o cupom", async () => {
    const app = createApp(config);
    const resp = await request(app)
      .post("/api/leads")
      .send({ name: "Maria", whatsapp: "(11) 98765-4321", source: "ifood-qr" });
    expect(resp.status).toBe(201);
    expect(resp.body.couponCode).toBe("BEIJAFLOR10");
    expect(resp.body.alreadyRegistered).toBe(false);
  });

  it("não duplica o mesmo WhatsApp, só atualiza", async () => {
    const app = createApp(config);
    await request(app).post("/api/leads").send({ name: "Maria", whatsapp: "11987654321" });
    const resp = await request(app)
      .post("/api/leads")
      .send({ name: "Maria Silva", whatsapp: "(11) 98765-4321" });
    expect(resp.status).toBe(200);
    expect(resp.body.alreadyRegistered).toBe(true);

    const leads = await request(app).get("/api/leads").set("X-Admin-Token", "segredo");
    expect(leads.body).toHaveLength(1);
    expect(leads.body[0].name).toBe("Maria Silva");
  });

  it("rejeita nome ou WhatsApp inválidos", async () => {
    const app = createApp(config);
    expect((await request(app).post("/api/leads").send({ name: "M", whatsapp: "11987654321" })).status).toBe(400);
    expect((await request(app).post("/api/leads").send({ name: "Maria", whatsapp: "123" })).status).toBe(400);
    expect((await request(app).post("/api/leads").send({})).status).toBe(400);
  });

  it("persiste os leads no arquivo entre instâncias do app", async () => {
    await request(createApp(config)).post("/api/leads").send({ name: "Maria", whatsapp: "11987654321" });
    const leads = await request(createApp(config)).get("/api/leads").set("X-Admin-Token", "segredo");
    expect(leads.body).toHaveLength(1);
  });
});

describe("GET /api/leads (admin)", () => {
  it("exige o token correto", async () => {
    const app = createApp(makeConfig());
    expect((await request(app).get("/api/leads")).status).toBe(401);
    expect((await request(app).get("/api/leads").set("X-Admin-Token", "errado")).status).toBe(401);
    expect((await request(app).get("/api/leads").set("X-Admin-Token", "segredo")).status).toBe(200);
  });

  it("fica desligada sem ADMIN_TOKEN configurado", async () => {
    const app = createApp(makeConfig({ adminToken: undefined }));
    expect((await request(app).get("/api/leads").set("X-Admin-Token", "qualquer")).status).toBe(503);
  });
});

describe("GET /api/config", () => {
  it("expõe só a config pública da landing", async () => {
    const resp = await request(createApp(makeConfig())).get("/api/config");
    expect(resp.status).toBe(200);
    expect(resp.body).toEqual({
      whatsappNumber: "5511987654321",
      instagramUrl: "https://www.instagram.com/docesbeijaflor/",
      couponCode: "BEIJAFLOR10",
      couponDiscount: "10%",
    });
    expect(resp.body.adminToken).toBeUndefined();
  });
});
