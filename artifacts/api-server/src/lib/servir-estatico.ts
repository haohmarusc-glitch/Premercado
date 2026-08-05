import express, { type Express } from "express";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { logger } from "./logger";

// Servir o frontend PELO Express só é necessário fora do Replit.
//
// No Replit quem entrega os arquivos estáticos é o roteador da borda: o
// deployment declara `router = "application"` e mapeia as portas dos dois
// artifacts, então o Express nunca viu uma request de HTML. Num container
// único (Docker/VPS) não existe essa borda -- ou o Express serve o
// artifacts/premarket/dist/public, ou não tem frontend.
//
// Fica atrás de SERVE_STATIC=1 justamente pra que o comportamento no Replit
// continue byte a byte o mesmo enquanto a migração não acontece.
export function servirEstaticoLigado(): boolean {
  return process.env["SERVE_STATIC"] === "1";
}

// Caminho do build do frontend.
//
// Derivado da localização do BUNDLE, não do cwd: o dist do api-server mora em
// artifacts/api-server/dist/index.mjs, então dois níveis acima chega-se a
// artifacts/, e de lá em premarket/dist/public. Usar process.cwd() daria o
// mesmo resultado só enquanto ninguém rodasse `node` de outro diretório.
//
// STATIC_DIR sobrescreve, pra quem quiser montar o frontend em outro lugar
// (ex.: um volume separado, ou rodar a partir do fonte em desenvolvimento,
// onde este arquivo não está em dist/ e a derivação acima não vale).
export function diretorioEstatico(): string {
  const explicito = process.env["STATIC_DIR"];
  if (explicito) return path.resolve(explicito);
  const aqui = path.dirname(fileURLToPath(import.meta.url));
  return path.resolve(aqui, "..", "..", "premarket", "dist", "public");
}

// Só o que o Vite emite com hash no nome pode ser imutável. index.html, sw.js
// e o manifest precisam ser sempre revalidados: com registerType "autoUpdate",
// um sw.js cacheado é um app que NUNCA atualiza, e o usuário fica preso numa
// versão antiga sem nenhum sintoma visível além de dados que não batem.
const UM_ANO_S = 60 * 60 * 24 * 365;

function cabecalhosDeCache(res: express.Response, caminho: string): void {
  const nome = path.basename(caminho);
  const imutavel =
    caminho.includes(`${path.sep}assets${path.sep}`) &&
    !nome.endsWith(".html");
  res.setHeader(
    "Cache-Control",
    imutavel ? `public, max-age=${UM_ANO_S}, immutable` : "no-cache",
  );
}

export function montarEstatico(app: Express): void {
  const dir = diretorioEstatico();
  const indexHtml = path.join(dir, "index.html");

  // Falha na subida, de propósito. A alternativa é um container que sobe
  // "saudável" e devolve 404 em toda navegação -- descoberto pelo usuário, não
  // pelo deploy. Com SERVE_STATIC=1 o frontend é parte do contrato do
  // processo; se ele não está lá, o processo está errado.
  if (!fs.existsSync(indexHtml)) {
    throw new Error(
      `SERVE_STATIC=1 mas não há index.html em ${dir}. ` +
        `Rode o build do frontend (pnpm --filter @workspace/premarket run build) ` +
        `ou aponte STATIC_DIR para o diretório correto.`,
    );
  }

  logger.info({ dir }, "Servindo frontend estático");

  app.use(
    express.static(dir, {
      // index: false porque o fallback abaixo é quem decide o que fazer com
      // "/" -- deixar os dois responderem duplicaria a política de cache.
      index: false,
      setHeaders: cabecalhosDeCache,
    }),
  );

  // Fallback de SPA: qualquer GET que não casou com arquivo nem com /api vira
  // index.html, porque as rotas do wouter só existem no cliente.
  //
  // Middleware terminal em vez de app.get("*"): o Express 5 usa path-to-regexp
  // v8, onde "*" sozinho não é mais um padrão válido.
  app.use((req, res, next) => {
    if (req.method !== "GET" && req.method !== "HEAD") return next();
    // /api que chegou até aqui é rota inexistente da API. Precisa continuar
    // sendo um 404 -- devolver o HTML do app faria o cliente tentar dar
    // JSON.parse numa página e reportar um erro que não tem nada a ver.
    if (req.path === "/api" || req.path.startsWith("/api/")) return next();
    res.setHeader("Cache-Control", "no-cache");
    res.sendFile(indexHtml, (err) => {
      if (err) next(err);
    });
  });
}
