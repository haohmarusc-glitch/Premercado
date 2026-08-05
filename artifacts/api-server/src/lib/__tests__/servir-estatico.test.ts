import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import express from "express";
import request from "supertest";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

vi.mock("../logger", () => ({
  logger: { info: () => {}, warn: () => {}, error: () => {} },
}));

const { montarEstatico, servirEstaticoLigado, diretorioEstatico } =
  await import("../servir-estatico");

/**
 * Monta um build de frontend de mentira em disco. O que está sendo testado é
 * roteamento e cabeçalho de cache, não o conteúdo -- então basta que os
 * arquivos existam nos caminhos que o Vite realmente usa (assets/ com nome
 * hasheado, index.html e sw.js na raiz).
 */
function criarBuildFalso(): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "estatico-"));
  fs.mkdirSync(path.join(dir, "assets"));
  fs.writeFileSync(path.join(dir, "index.html"), "<html>app</html>");
  fs.writeFileSync(path.join(dir, "sw.js"), "// service worker");
  fs.writeFileSync(path.join(dir, "assets", "main-a1b2c3.js"), "console.log(1)");
  return dir;
}

/** Mesma ordem de registro do app.ts: /api primeiro, estático depois. */
function montarApp(dir: string) {
  const app = express();
  const router = express.Router();
  router.get("/existe", (_req, res) => {
    res.json({ ok: true });
  });
  app.use("/api", router);
  process.env["STATIC_DIR"] = dir;
  montarEstatico(app);
  return app;
}

let criados: string[] = [];

beforeEach(() => {
  criados = [];
});

afterEach(() => {
  delete process.env["STATIC_DIR"];
  delete process.env["SERVE_STATIC"];
  for (const dir of criados) fs.rmSync(dir, { recursive: true, force: true });
});

function build(): string {
  const dir = criarBuildFalso();
  criados.push(dir);
  return dir;
}

describe("servirEstaticoLigado", () => {
  it("só liga com SERVE_STATIC=1", () => {
    delete process.env["SERVE_STATIC"];
    expect(servirEstaticoLigado()).toBe(false);
    process.env["SERVE_STATIC"] = "0";
    expect(servirEstaticoLigado()).toBe(false);
    // "true" NÃO liga de propósito: a flag é lida em dois lugares (aqui e na
    // CSP do app.ts) e um valor único evita que os dois discordem.
    process.env["SERVE_STATIC"] = "true";
    expect(servirEstaticoLigado()).toBe(false);
    process.env["SERVE_STATIC"] = "1";
    expect(servirEstaticoLigado()).toBe(true);
  });
});

describe("diretorioEstatico", () => {
  it("STATIC_DIR sobrescreve a derivação pelo caminho do bundle", () => {
    process.env["STATIC_DIR"] = "/qualquer/lugar";
    expect(diretorioEstatico()).toBe("/qualquer/lugar");
  });

  it("sem STATIC_DIR aponta para o build do premarket", () => {
    delete process.env["STATIC_DIR"];
    expect(diretorioEstatico().endsWith(
      path.join("premarket", "dist", "public"),
    )).toBe(true);
  });
});

describe("montarEstatico", () => {
  it("recusa subir quando não há index.html", () => {
    const vazio = fs.mkdtempSync(path.join(os.tmpdir(), "vazio-"));
    criados.push(vazio);
    process.env["STATIC_DIR"] = vazio;
    expect(() => montarEstatico(express())).toThrow(/index\.html/);
  });

  it("serve arquivo existente", async () => {
    const res = await request(montarApp(build())).get("/assets/main-a1b2c3.js");
    expect(res.status).toBe(200);
    expect(res.text).toContain("console.log(1)");
  });

  it("marca só os assets hasheados como imutáveis", async () => {
    const app = montarApp(build());
    const asset = await request(app).get("/assets/main-a1b2c3.js");
    expect(asset.headers["cache-control"]).toContain("immutable");

    // sw.js cacheado com registerType "autoUpdate" é um app que nunca
    // atualiza -- o sintoma aparece como dado velho, não como erro.
    const sw = await request(app).get("/sw.js");
    expect(sw.headers["cache-control"]).toBe("no-cache");
  });

  it("devolve o index.html em rota do cliente", async () => {
    const res = await request(montarApp(build())).get("/portfolio");
    expect(res.status).toBe(200);
    expect(res.text).toContain("app");
    expect(res.headers["cache-control"]).toBe("no-cache");
  });

  it("não sombreia a API", async () => {
    const res = await request(montarApp(build())).get("/api/existe");
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ ok: true });
  });

  it("rota inexistente de /api continua 404, não vira HTML", async () => {
    const res = await request(montarApp(build())).get("/api/naoexiste");
    expect(res.status).toBe(404);
    expect(res.text).not.toContain("<html>");
  });

  it("POST em rota desconhecida não vira index.html", async () => {
    const res = await request(montarApp(build())).post("/portfolio");
    expect(res.status).toBe(404);
  });
});
