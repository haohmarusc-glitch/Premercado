import nodemailer from "nodemailer";
import { db, settingsTable } from "@workspace/db";
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

// Query direta (não getOrCreateSettings) para evitar ciclo de import:
// routes/settings → lib/scheduler → lib/mailer.
async function resolveNotifyEmail(): Promise<string | null> {
  try {
    const [s] = await db
      .select({ notifyEmail: settingsTable.notifyEmail })
      .from(settingsTable)
      .limit(1);
    const email = s?.notifyEmail?.trim();
    if (email) return email;
  } catch (err) {
    logger.error({ err }, "Failed to read notify email from settings");
  }
  return process.env.NOTIFY_EMAIL?.trim() || null;
}

export async function sendAlertEmail(opts: {
  symbol: string;
  indicator?: string; // 'price' (default) | 'rsi' | 'macd' | 'sma20' | 'sma50'
  condition: string;
  thresholdPct: number | null;
  thresholdPrice: number | null;
  thresholdValue?: number | null; // ex: nivel de RSI
  valueAtFiring?: number | null; // valor do indicador tecnico no disparo
  currentChangePct: number | null;
  currentPrice: number | null;
}): Promise<void> {
  const to = await resolveNotifyEmail();
  if (!to) { logger.warn("No notify email — skipping alert"); return; }
  if (!process.env.SMTP_USER || !process.env.SMTP_PASS) { logger.warn("SMTP not configured"); return; }

  const indicator = opts.indicator ?? "price";
  const direction = opts.condition === "above" ? "subiu acima de" : "caiu abaixo de";

  let subject: string;
  let conditionSentence: string; // frase completa pra "Condição: <isso>" no corpo do email
  if (indicator === "rsi") {
    const dir = opts.condition === "above" ? "acima de" : "abaixo de";
    conditionSentence = `RSI(14) ${dir} ${opts.thresholdValue ?? "—"} (atual: ${opts.valueAtFiring ?? "—"})`;
    subject = `🚨 Alerta: ${opts.symbol} RSI(14) ${dir} ${opts.thresholdValue ?? "—"}`;
  } else if (indicator === "macd") {
    const trend = opts.condition === "above" ? "bullish" : "bearish";
    conditionSentence = `MACD virou ${trend} (histograma ${opts.condition === "above" ? ">" : "<"} 0)`;
    subject = `🚨 Alerta: ${opts.symbol} MACD virou ${trend}`;
  } else if (indicator === "sma20" || indicator === "sma50") {
    const period = indicator === "sma20" ? "SMA20" : "SMA50";
    const dir = opts.condition === "above" ? "cruzou acima da" : "cruzou abaixo da";
    conditionSentence = `preço ${dir} ${period}`;
    subject = `🚨 Alerta: ${opts.symbol} preço ${dir} ${period}`;
  } else {
    const thresholdStr = opts.thresholdPrice != null
      ? `$${opts.thresholdPrice.toFixed(2)}`
      : `${(opts.thresholdPct ?? 0) > 0 ? "+" : ""}${opts.thresholdPct}%`;
    const conditionLabel = opts.thresholdPrice != null ? "preço" : "variação";
    conditionSentence = `${conditionLabel} ${direction} ${thresholdStr}`;
    subject = `🚨 Alerta: ${opts.symbol} ${direction} ${thresholdStr}`;
  }

  const priceStr = opts.currentPrice != null ? `$${opts.currentPrice.toFixed(2)}` : "N/A";
  const sign = (opts.currentChangePct ?? 0) >= 0 ? "+" : "";
  const changeStr = opts.currentChangePct != null ? `${sign}${opts.currentChangePct.toFixed(2)}%` : "—";

  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{font-family:'Courier New',monospace;background:#111;color:#e0e0e0;padding:24px}
  .ticker{font-size:32px;font-weight:bold;color:#ff8c00}
  .change{font-size:24px;font-weight:bold;color:${(opts.currentChangePct ?? 0) >= 0 ? "#22c55e" : "#ef4444"}}
  .box{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:16px;margin:16px 0}
  .footer{margin-top:32px;font-size:11px;color:#555}
</style></head>
<body>
<p style="color:#555;font-size:12px;text-transform:uppercase;">Alerta de Preço — Pré-Mercado Agente</p>
<div class="box">
  <div class="ticker">${opts.symbol}</div>
  <div class="change">${changeStr}</div>
  <p style="margin:8px 0;color:#aaa">Preço atual: <strong style="color:#fff">${priceStr}</strong></p>
  <p style="margin:4px 0;color:#666;font-size:12px">
    Condição: ${conditionSentence}
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

export async function sendPortfolioHoldingEmail(opts: {
  ticker: string;
  purchaseDate: string;
  milestone: number;
  amount: number;
}): Promise<void> {
  const to = await resolveNotifyEmail();
  if (!to) { logger.warn("No notify email — skipping holding alert"); return; }
  if (!process.env.SMTP_USER || !process.env.SMTP_PASS) { logger.warn("SMTP not configured"); return; }

  const subject = `📅 ${opts.ticker} — lote de ${opts.milestone} dias (compra ${opts.purchaseDate})`;
  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{font-family:'Courier New',monospace;background:#111;color:#e0e0e0;padding:24px}
  .ticker{font-size:32px;font-weight:bold;color:#ff8c00}
  .box{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:16px;margin:16px 0}
  .footer{margin-top:32px;font-size:11px;color:#555}
</style></head>
<body>
<p style="color:#555;font-size:12px;text-transform:uppercase;">Alerta de Holding — Pré-Mercado Agente</p>
<div class="box">
  <div class="ticker">${opts.ticker}</div>
  <p style="margin:8px 0;color:#aaa">Lote de <strong style="color:#fff">${opts.milestone} dias</strong> atingido</p>
  <p style="margin:4px 0;color:#aaa">Data da compra: <strong style="color:#fff">${opts.purchaseDate}</strong></p>
  <p style="margin:4px 0;color:#aaa">Valor investido: <strong style="color:#fff">$${opts.amount.toFixed(2)}</strong></p>
</div>
<div class="footer">Gerado automaticamente pelo Pré-Mercado Agente.</div>
</body></html>`;

  try {
    const transporter = createTransport();
    await transporter.sendMail({
      from: `"Pré-Mercado Agente" <${process.env.SMTP_USER}>`,
      to,
      subject,
      html,
    });
    logger.info({ to, subject }, "Holding alert e-mail sent");
  } catch (err) {
    logger.error({ err }, "Failed to send holding alert e-mail");
  }
}

export async function sendRecompraEmail(opts: {
  ticker: string;
  salePrice: number;
  currentPrice: number;
  dropPct: number;       // queda % vs. preço de venda (valor positivo)
  thresholdPct: number;  // limiar que disparou
}): Promise<void> {
  const to = await resolveNotifyEmail();
  if (!to) { logger.warn("No notify email — skipping recompra alert"); return; }
  if (!process.env.SMTP_USER || !process.env.SMTP_PASS) { logger.warn("SMTP not configured"); return; }

  const subject = `🔄 Recompra? ${opts.ticker} caiu ${opts.dropPct.toFixed(1)}% abaixo do preço de venda`;
  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{font-family:'Courier New',monospace;background:#111;color:#e0e0e0;padding:24px}
  .ticker{font-size:32px;font-weight:bold;color:#ff8c00}
  .change{font-size:24px;font-weight:bold;color:#22c55e}
  .box{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:16px;margin:16px 0}
  .footer{margin-top:32px;font-size:11px;color:#555}
</style></head>
<body>
<p style="color:#555;font-size:12px;text-transform:uppercase;">Oportunidade de Recompra — Pré-Mercado Agente</p>
<div class="box">
  <div class="ticker">${opts.ticker}</div>
  <div class="change">▼ ${opts.dropPct.toFixed(2)}%</div>
  <p style="margin:8px 0;color:#aaa">Preço de venda: <strong style="color:#fff">$${opts.salePrice.toFixed(2)}</strong></p>
  <p style="margin:4px 0;color:#aaa">Preço atual: <strong style="color:#fff">$${opts.currentPrice.toFixed(2)}</strong></p>
  <p style="margin:4px 0;color:#666;font-size:12px">
    Caiu mais de ${opts.thresholdPct}% abaixo do preço em que você vendeu — possível ponto de recompra.
  </p>
</div>
<div class="footer">Gerado automaticamente pelo Pré-Mercado Agente. Não é recomendação de investimento.</div>
</body></html>`;

  try {
    const transporter = createTransport();
    await transporter.sendMail({
      from: `"Pré-Mercado Agente" <${process.env.SMTP_USER}>`,
      to,
      subject,
      html,
    });
    logger.info({ to, subject }, "Recompra e-mail sent");
  } catch (err) {
    logger.error({ err }, "Failed to send recompra e-mail");
  }
}

export async function sendReportEmail(reportContent: string, date: string, tickers?: string[]): Promise<void> {
  const to = await resolveNotifyEmail();
  if (!to) {
    logger.warn("No notify email configured — skipping e-mail notification");
    return;
  }
  if (!process.env.SMTP_USER || !process.env.SMTP_PASS) {
    logger.warn("SMTP_USER or SMTP_PASS not set — skipping e-mail notification");
    return;
  }

  const subject = `Pré-Mercado ${date}${tickers && tickers.length ? ` — ${tickers.join(", ")}` : ""}`;

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
