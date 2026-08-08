const $ = (sel) => document.querySelector(sel);
const loginEl = $("#login");
const mainEl = $("#main");
const outputEl = $("#output");
const statusEl = $("#status");
const loginError = $("#login-error");

async function api(path, opts = {}) {
  const res = await fetch(path, {
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
    ...opts,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function showMain(me) {
  loginEl.classList.add("hidden");
  mainEl.classList.remove("hidden");
  if (me.appDir) $("#app-dir").textContent = me.appDir;
}
function showLogin() {
  mainEl.classList.add("hidden");
  loginEl.classList.remove("hidden");
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  loginError.classList.add("hidden");
  try {
    await api("/api/login", { method: "POST", body: JSON.stringify({ password: $("#password").value }) });
    showMain(await api("/api/me"));
  } catch (err) {
    loginError.textContent = err.message || "Falha no login";
    loginError.classList.remove("hidden");
  }
});

$("#btn-logout").addEventListener("click", async () => {
  try { await api("/api/logout", { method: "POST", body: "{}" }); } catch (_) {}
  showLogin();
});

document.querySelectorAll(".tab").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.add("hidden"));
    $(`#tab-${btn.dataset.tab}`).classList.remove("hidden");
  });
});

let busy = false;
async function runAction(action) {
  if (busy) return;
  busy = true;
  document.querySelectorAll("[data-action]").forEach((b) => (b.disabled = true));
  statusEl.textContent = `Rodando: ${action}...`;
  outputEl.classList.remove("err");
  outputEl.classList.add("running");
  outputEl.textContent = `> ${action}\n...`;
  try {
    const data = await api("/api/run", { method: "POST", body: JSON.stringify({ action }) });
    const parts = [];
    if (data.stdout) parts.push(data.stdout.trimEnd());
    if (data.stderr) parts.push("--- stderr ---\n" + data.stderr.trimEnd());
    parts.push(`\n[exit ${data.code} | ${data.ms} ms]`);
    outputEl.textContent = parts.join("\n\n");
    outputEl.classList.remove("running");
    if (data.code !== 0) outputEl.classList.add("err");
    statusEl.textContent = data.code === 0 ? "OK" : `Erro (exit ${data.code})`;
    outputEl.scrollTop = outputEl.scrollHeight;
  } catch (err) {
    outputEl.classList.remove("running");
    outputEl.classList.add("err");
    outputEl.textContent = String(err.message || err);
    statusEl.textContent = "Falha";
  } finally {
    busy = false;
    document.querySelectorAll("[data-action]").forEach((b) => (b.disabled = false));
  }
}

document.querySelectorAll("[data-action]").forEach((btn) => {
  btn.addEventListener("click", () => runAction(btn.dataset.action));
});
$("#btn-clear").addEventListener("click", () => {
  outputEl.textContent = "";
  outputEl.classList.remove("err", "running");
  statusEl.textContent = "Pronto";
});
api("/api/me").then((me) => { if (me.ok) showMain(me); }).catch(() => showLogin());
