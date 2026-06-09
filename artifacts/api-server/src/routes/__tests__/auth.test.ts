import { describe, it, expect, afterEach } from "vitest";
import express from "express";
import request from "supertest";
import session from "express-session";
import authRouter from "../auth.js";

function createApp() {
  const app = express();
  app.use(express.json());
  app.use(
    session({ secret: "test-secret", resave: false, saveUninitialized: false }),
  );
  app.use("/api", authRouter);
  return app;
}

describe("GET /api/auth/me", () => {
  it("retorna authenticated: false sem sessão", async () => {
    const res = await request(createApp()).get("/api/auth/me");
    expect(res.status).toBe(200);
    expect(res.body.authenticated).toBe(false);
  });
});

describe("POST /api/auth/login", () => {
  const originalPassword = process.env.OPERATOR_PASSWORD;

  afterEach(() => {
    if (originalPassword === undefined) {
      delete process.env.OPERATOR_PASSWORD;
    } else {
      process.env.OPERATOR_PASSWORD = originalPassword;
    }
  });

  it("retorna 503 quando OPERATOR_PASSWORD não está configurado", async () => {
    delete process.env.OPERATOR_PASSWORD;
    const res = await request(createApp())
      .post("/api/auth/login")
      .send({ password: "qualquer" });
    expect(res.status).toBe(503);
  });

  it("retorna 401 com senha incorreta", async () => {
    process.env.OPERATOR_PASSWORD = "senha-correta";
    const res = await request(createApp())
      .post("/api/auth/login")
      .send({ password: "senha-errada" });
    expect(res.status).toBe(401);
  });

  it("retorna authenticated: true com senha correta", async () => {
    process.env.OPERATOR_PASSWORD = "senha-correta";
    const res = await request(createApp())
      .post("/api/auth/login")
      .send({ password: "senha-correta" });
    expect(res.status).toBe(200);
    expect(res.body.authenticated).toBe(true);
  });
});

describe("POST /api/auth/logout", () => {
  it("retorna authenticated: false e limpa cookie", async () => {
    const res = await request(createApp()).post("/api/auth/logout");
    expect(res.status).toBe(200);
    expect(res.body.authenticated).toBe(false);
  });
});
