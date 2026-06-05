import nodemailer from "nodemailer";
import { logger } from "./logger";

function createTransport() {
  return nodemailer.createTransport({
    host: "smtp.gmail.com",
    port: 587,
    secure: false,
    auth: {
      user: process.env.SMTP_USER,
      pass: process.env.SMTP_PASS,
    },
  });
}

async function resolveNotifyEmail(): Promise<string | null> {
  // Try DB settings first, fall back to env var
  try {
    const { db, settingsTable } = await import("@workspace/db");
    const [row] = await db.select().from(settingsTable).limit(1);
    if (row?.notifyEmail) return row.notifyEmail;
  } catch (_) {
    // ignore
  }
  return process.env.NOTIFY_EMAIL ?? null;
}

export async function sendAlertEmail(opts: {
  symbol: string;
  condition: string;
  thresholdPct: number;
  currentChangePct: number;
  currentPrice: number | null;
}): Promise<void> {
  const to = await resolveNotifyEmail();
  if (!to) { logger.warn("No notify email — skipping alert"); return; }
  if (!process.env.SMTP_USER || !process.env.SMTP_PASS) { logger.warn("SMTP not configured"); return; }

  const sign = opts.currentChangePct >= 0 ? "+" : "";
  const direction = opts.condition === "above" ? "subiu acima de" : "caiu abaixo de";
  const subject = `🚨 Alerta: ${opts.symbol} ${direction} ${opts.thresholdPct > 0 ? "+" : ""}${opts.thresholdPct}%`;
  const priceStr = opts.currentPrice != null ? `$${opts.currentPrice.toFixed(2)}` : "N/A";

  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{font-family:'Courier New',monospace;background:#111;color:#e0e0e0;padding:24px}
  .ticker{font-size:32px;font-weight:bold;color:#ff8c00}
  .change{font-size:24px;font-weight:bold;color:${opts.currentChangePct >= 0 ? "#22c55e" : "#ef4444"}}
  .box{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:16px;margin:16px 0}
  .footer{margin-top:32px;font-size:11px;color:#555}
</style></head>
<body>
<p style="color:#555;font-size:12px;text-transform:uppercase;">Alerta de Preço — Pré-Mercado Agente</p>
<div class="box">
  <div class="ticker">${opts.symbol}</div>
  <div class="change">${sign}${opts.currentChangePct.toFixed(2)}%</div>
  <p style="margin:8px 0;color:#aaa">Preço atual: <strong style="color:#fff">${priceStr}</strong></p>
  <p style="margin:4px 0;color:#666;font-size:12px">
    Condição: variação ${direction} ${opts.thresholdPct > 0 ? "+" : ""}${opts.thresholdPct}%
  </p>
</div>
<div class="footer">Gerado automaticamente pelo Pré-Mercado Agente. Cooldown: 4h.</div>
</body></html>`;

  try {
    const transporter = createTransport();
    await transporter.sendMail({
      from: `"Pré-Mercado Agente" <${process.env.SMTP_USER}>`,
      to,
      subject,
      html,
    });
    logger.info({ to, subject }, "Alert e-mail sent");
  } catch (err) {
    logger.error({ err }, "Failed to send alert e-mail");
  }
}

export async function sendReportEmail(reportContent: string, date: string): Promise<void> {
  const to = await resolveNotifyEmail();
  if (!to) {
    logger.warn("No notify email configured — skipping e-mail notification");
    return;
  }
  if (!process.env.SMTP_USER || !process.env.SMTP_PASS) {
    logger.warn("SMTP_USER or SMTP_PASS not set — skipping e-mail notification");
    return;
  }

  const subject = `Pré-Mercado ${date} — MU & SMCI`;

  const htmlBody = reportContent
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/^#{1,2} (.+)$/gm, "<h2>$1</h2>")
    .replace(/^#{3,} (.+)$/gm, "<h3>$1</h3>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/\n/g, "<br>");

  const html = `<!DOCTYPE html>
<html>
<head><meta charset="utf-8">
<style>
  body{font-family:'Courier New',monospace;background:#111;color:#e0e0e0;padding:24px}
  h2{color:#ff8c00;border-bottom:1px solid #333;padding-bottom:4px}
  h3{color:#ffaa44}
  strong{color:#fff}
  .footer{margin-top:32px;font-size:11px;color:#555}
</style></head>
<body>
<p style="color:#555;font-size:12px;">ANÁLISE PRÉ-MERCADO — ${date}</p>
${htmlBody}
<div class="footer">Gerado automaticamente pelo Pré-Mercado Agente.</div>
</body></html>`;

  try {
    const transporter = createTransport();
    await transporter.sendMail({
      from: `"Pré-Mercado Agente" <${process.env.SMTP_USER}>`,
      to,
      subject,
      text: reportContent,
      html,
    });
    logger.info({ to, subject }, "Report e-mail sent");
  } catch (err) {
    logger.error({ err }, "Failed to send report e-mail");
  }
}
