/**
 * Deploy Panel — painel web para gerenciar o Premercado no VPS
 * Git pull/push, docker logs, compose up --build
 * Funciona no PC e no celular (UI responsiva)
 */
const express = require("express");
const session = require("express-session");
const { spawn, execFile } = require("child_process");
const path = require("path");
const fs = require("fs");

const app = express();
const PORT = process.env.PORT || 3090;
const PANEL_PASSWORD = process.env.PANEL_PASSWORD || "troque-esta-senha";
const APP_DIR = process.env.APP_DIR || "/opt/premercado";
const SESSION_SECRET = process.env.SESSION_SECRET || require("crypto").randomBytes(32).toString("hex");

const ALLOWED = {
  "git-status": { cmd: "git", args: ["status", "-sb"], cwd: APP_DIR },
  "git-pull": { cmd: "git", args: ["pull", "--ff-only"], cwd: APP_DIR },
  "git-push": { cmd: "git", args: ["push"], cwd: APP_DIR },
  "git-log": { cmd: "git", args: ["log", "-5", "--oneline", "--decorate"], cwd: APP_DIR },
  "compose-ps": { cmd: "docker", args: ["compose", "ps"], cwd: APP_DIR },
  "compose-logs": { cmd: "docker", args: ["compose", "logs", "--tail=80", "--no-color"], cwd: APP_DIR },
  "compose-logs-app": { cmd: "docker", args: ["compose", "logs", "--tail=100", "--no-color", "app"], cwd: APP_DIR },
  "compose-up": { cmd: "docker", args: ["compose", "up", "-d", "--build"], cwd: APP_DIR },
  "compose-restart": { cmd: "docker", args: ["compose", "restart", "app"], cwd: APP_DIR },
  "health": { cmd: "curl", args: ["-sS", "-o", "/dev/null", "-w", "%{http_code}", "http://127.0.0.1/api/healthz", "-H", "Host: premercadosc.com"], cwd: APP_DIR },
};

app.use(express.json());
app.use(express.urlencoded({ extended: false }));
app.use(
  session({
    secret: SESSION_SECRET,
    resave: false,
    saveUninitialized: false,
    cookie: {
      httpOnly: true,
      sameSite: "lax",
      maxAge: 7 * 24 * 60 * 60 * 1000,
      secure: process.env.NODE_ENV === "production",
    },
  })
);
app.use(express.static(path.join(__dirname, "public")));

function requireAuth(req, res, next) {
  if (req.session && req.session.ok) return next();
  return res.status(401).json({ error: "Nao autenticado" });
}

app.post("/api/login", (req, res) => {
  const { password } = req.body || {};
  if (password && password === PANEL_PASSWORD) {
    req.session.ok = true;
    return res.json({ ok: true });
  }
  return res.status(401).json({ error: "Senha incorreta" });
});

app.post("/api/logout", (req, res) => {
  req.session.destroy(() => res.json({ ok: true }));
});

app.get("/api/me", (req, res) => {
  res.json({ ok: !!(req.session && req.session.ok), appDir: APP_DIR });
});

function runCommand(key) {
  return new Promise((resolve) => {
    const spec = ALLOWED[key];
    if (!spec) {
      resolve({ code: 1, stdout: "", stderr: "Comando nao permitido" });
      return;
    }
    if (!fs.existsSync(spec.cwd)) {
      resolve({ code: 1, stdout: "", stderr: `Diretorio nao existe: ${spec.cwd}` });
      return;
    }
    const child = spawn(spec.cmd, spec.args, {
      cwd: spec.cwd,
      env: process.env,
      timeout: 600000,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (d) => {
      stdout += d.toString();
      if (stdout.length > 200000) stdout = stdout.slice(-150000);
    });
    child.stderr.on("data", (d) => {
      stderr += d.toString();
      if (stderr.length > 200000) stderr = stderr.slice(-150000);
    });
    child.on("error", (err) => {
      resolve({ code: 1, stdout, stderr: String(err.message || err) });
    });
    child.on("close", (code) => {
      resolve({ code: code ?? 1, stdout, stderr });
    });
  });
}

app.post("/api/run", requireAuth, async (req, res) => {
  const { action } = req.body || {};
  if (!action || !ALLOWED[action]) {
    return res.status(400).json({ error: "Acao invalida", allowed: Object.keys(ALLOWED) });
  }
  const started = Date.now();
  const result = await runCommand(action);
  res.json({
    action,
    code: result.code,
    ms: Date.now() - started,
    stdout: result.stdout,
    stderr: result.stderr,
  });
});

app.get("/api/actions", requireAuth, (_req, res) => {
  res.json({
    actions: [
      { id: "git-status", label: "Git status", group: "git" },
      { id: "git-pull", label: "Git pull", group: "git" },
      { id: "git-push", label: "Git push", group: "git" },
      { id: "git-log", label: "Git log (5)", group: "git" },
      { id: "compose-ps", label: "Containers (ps)", group: "docker" },
      { id: "compose-logs", label: "Logs (todos)", group: "docker" },
      { id: "compose-logs-app", label: "Logs (app)", group: "docker" },
      { id: "compose-up", label: "Publicar (up --build)", group: "deploy" },
      { id: "compose-restart", label: "Restart app", group: "deploy" },
      { id: "health", label: "Health check", group: "deploy" },
    ],
  });
});

app.get("*", (_req, res) => {
  res.sendFile(path.join(__dirname, "public", "index.html"));
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`Deploy Panel em http://0.0.0.0:${PORT}`);
  console.log(`APP_DIR=${APP_DIR}`);
});
