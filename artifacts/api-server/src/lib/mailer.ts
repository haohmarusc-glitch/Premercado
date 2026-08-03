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

// Colunas numeric do Postgres chegam como STRING via drizzle (apesar do
// $type<number>) — coage na borda antes de formatar, senão .toFixed lança
// TypeError e o e-mail nunca sai (visto em produção nos alertas 89/97).
function toNum(v: number | string | null | undefined): number | null {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

export async function sendAlertEmail(opts: {
  to: string | null;
  symbol: string;
  indicator?: string; // 'price' (default) | 'rsi' | 'macd' | 'sma20' | 'sma50'
  condition: string;
  thresholdPct: number | string | null;
  thresholdPrice: number | string | null;
  thresholdValue?: number | string | null; // ex: nivel de RSI
  valueAtFiring?: number | string | null; // valor do indicador tecnico no disparo
  currentChangePct: number | string | null;
  currentPrice: number | string | null;
}): Promise<void> {
  const to = opts.to?.trim();
  if (!to) { logger.warn({ symbol: opts.symbol }, "No notify email on record — skipping alert"); return; }
  if (!process.env.SMTP_USER || !process.env.SMTP_PASS) { logger.warn("SMTP not configured"); return; }

  // Todos os campos numeric do drizzle (thresholdPrice/Pct/Value, valueAtFiring,
  // currentPrice/ChangePct) chegam como STRING — coage antes de qualquer
  // .toFixed()/comparação, senão o e-mail nunca sai (mesma classe de bug
  // corrigida nos alertas de preço simples, agora estendida aos indicadores
  // tecnicos adicionados pela sessao mobile).
  const thresholdPrice = toNum(opts.thresholdPrice);
  const thresholdPct = toNum(opts.thresholdPct);
  const thresholdValue = toNum(opts.thresholdValue);
  const valueAtFiring = toNum(opts.valueAtFiring);
  const currentPrice = toNum(opts.currentPrice);
  const currentChangePct = toNum(opts.currentChangePct);

  const indicator = opts.indicator ?? "price";
  const direction = opts.condition === "above" ? "subiu acima de" : "caiu abaixo de";

  let subject: string;
  let conditionSentence: string; // frase completa pra "Condição: <isso>" no corpo do email
  if (indicator === "rsi") {
    const dir = opts.condition === "above" ? "acima de" : "abaixo de";
    const thresholdStr = thresholdValue != null ? thresholdValue.toFixed(0) : "—";
    const currentStr = valueAtFiring != null ? valueAtFiring.toFixed(1) : "—";
    conditionSentence = `RSI(14) ${dir} ${thresholdStr} (atual: ${currentStr})`;
    subject = `🚨 Alerta: ${opts.symbol} RSI(14) ${dir} ${thresholdStr}`;
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
    const thresholdStr = thresholdPrice != null
      ? `$${thresholdPrice.toFixed(2)}`
      : `${(thresholdPct ?? 0) > 0 ? "+" : ""}${thresholdPct}%`;
    const conditionLabel = thresholdPrice != null ? "preço" : "variação";
    conditionSentence = `${conditionLabel} ${direction} ${thresholdStr}`;
    subject = `🚨 Alerta: ${opts.symbol} ${direction} ${thresholdStr}`;
  }

  const priceStr = currentPrice != null ? `$${currentPrice.toFixed(2)}` : "N/A";
  const sign = (currentChangePct ?? 0) >= 0 ? "+" : "";
  const changeStr = currentChangePct != null ? `${sign}${currentChangePct.toFixed(2)}%` : "—";

  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{font-family:'Courier New',monospace;background:#111;color:#e0e0e0;padding:24px}
  .ticker{font-size:32px;font-weight:bold;color:#ff8c00}
  .change{font-size:24px;font-weight:bold;color:${(currentChangePct ?? 0) >= 0 ? "#22c55e" : "#ef4444"}}
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

export async function sendBounceAlertEmail(opts: {
  to: string | null;
  ticker: string;
  direction: "up" | "down"; // "up" = repique dentro de queda maior | "down" = realização de lucro dentro de alta maior
  changeTodayPct: number | string;
  title: string;   // já vem pronto de market_alerts.py::check_dead_cat_bounce -- não reimplementa o rótulo aqui
  detail: string;  // idem: explicação completa (inclui a comparação vs. semana passada) já formatada pelo Python
}): Promise<void> {
  const to = opts.to?.trim();
  if (!to) { logger.warn({ ticker: opts.ticker }, "No notify email on record — skipping bounce alert"); return; }
  if (!process.env.SMTP_USER || !process.env.SMTP_PASS) { logger.warn("SMTP not configured"); return; }

  const changeTodayPct = toNum(opts.changeTodayPct) ?? 0;
  const isUp = opts.direction === "up";
  const changeStr = `${changeTodayPct >= 0 ? "+" : ""}${changeTodayPct.toFixed(2)}%`;
  const subject = `↩️ ${opts.ticker}: ${opts.title} (${changeStr} hoje)`;

  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{font-family:'Courier New',monospace;background:#111;color:#e0e0e0;padding:24px}
  .ticker{font-size:32px;font-weight:bold;color:#ff8c00}
  .change{font-size:24px;font-weight:bold;color:${isUp ? "#22c55e" : "#ef4444"}}
  .box{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:16px;margin:16px 0}
  .footer{margin-top:32px;font-size:11px;color:#555}
</style></head>
<body>
<p style="color:#555;font-size:12px;text-transform:uppercase;">Alerta de Repique — Pré-Mercado Agente</p>
<div class="box">
  <div class="ticker">${opts.ticker}</div>
  <div class="change">${changeStr} hoje</div>
  <p style="margin:8px 0;color:#aaa">${opts.title}</p>
  <p style="margin:12px 0 0;color:#666;font-size:12px">${opts.detail}</p>
</div>
<div class="footer">Gerado automaticamente pelo Pré-Mercado Agente. Sinal técnico preliminar -- não é recomendação de investimento.</div>
</body></html>`;

  try {
    const transporter = createTransport();
    await transporter.sendMail({
      from: `"Pré-Mercado Agente" <${process.env.SMTP_USER}>`,
      to,
      subject,
      html,
    });
    logger.info({ to, subject }, "Bounce alert e-mail sent");
  } catch (err) {
    logger.error({ err }, "Failed to send bounce alert e-mail");
  }
}

// Uma linha de requisito da seção de squeeze/reversão -- ✓ (batido) ou
// — (faltando), mesmo estilo visual dos dois grupos.
function _reqList(items: string[], hit: boolean): string {
  if (!items.length) return "";
  const mark = hit ? `<span style="color:#22c55e">✓</span>` : `<span style="color:#5A7679">—</span>`;
  return items.map((label) => `<div style="margin:3px 0;padding-left:4px">${mark} ${label}</div>`).join("");
}

export async function sendSqueezeAlertEmail(opts: {
  to: string | null;
  ticker: string;
  tier: "near" | "confirmed"; // "near" = falta 1-2 dos 4 requisitos | "confirmed" = squeeze_setup_detected
  price: number | string;
  totalMissing: number; // 0 quando confirmed
  nDangerous: number; // sinais de risco perigosos batidos (de 4, precisa 2+)
  presentRiskSignals: string[];
  missingRiskSignals: string[];
  confirmCount: number; // confirmações de reversão batidas (de 4, precisa 2+)
  presentConfirmSignals: string[]; // já vêm com descrição pronta de check_squeeze_setup (ex.: "candle Martelo (2026-07-20)")
  missingConfirmSignals: string[];
  excludedEarningsReactionSignals: string[]; // confirmações descartadas por coincidir com reação a earnings
  earningsImminent: boolean; // earnings em 0-14 dias -- nunca deixa o tier chegar a "confirmed"
  missingEventSignals: string[]; // nota sobre o earnings iminente, mesmo formato dos outros "missing"
}): Promise<void> {
  const to = opts.to?.trim();
  if (!to) { logger.warn({ ticker: opts.ticker }, "No notify email on record — skipping squeeze alert"); return; }
  if (!process.env.SMTP_USER || !process.env.SMTP_PASS) { logger.warn("SMTP not configured"); return; }

  const price = toNum(opts.price);
  const isConfirmed = opts.tier === "confirmed";
  const priceStr = price != null ? `$${price.toFixed(2)}` : "N/A";

  const subject = isConfirmed
    ? `🎯 ${opts.ticker}: setup de squeeze confirmado`
    : `👀 ${opts.ticker}: quase lá -- falta ${opts.totalMissing} requisito${opts.totalMissing === 1 ? "" : "s"} pro squeeze`;

  const headline = isConfirmed
    ? "Squeeze confirmado"
    : `Faltam ${opts.totalMissing} requisito${opts.totalMissing === 1 ? "" : "s"}`;

  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{font-family:'Courier New',monospace;background:#111;color:#e0e0e0;padding:24px}
  .ticker{font-size:32px;font-weight:bold;color:#ff8c00}
  .headline{font-size:20px;font-weight:bold;color:${isConfirmed ? "#22c55e" : "#E3A63C"}}
  .box{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:16px;margin:16px 0}
  .section-title{font-size:11px;color:#84A0A0;text-transform:uppercase;letter-spacing:.05em;margin:0 0 6px}
  .footer{margin-top:32px;font-size:11px;color:#555}
</style></head>
<body>
<p style="color:#555;font-size:12px;text-transform:uppercase;">Alerta de Squeeze — Pré-Mercado Agente</p>
<div class="box">
  <div class="ticker">${opts.ticker}</div>
  <div class="headline">${headline}</div>
  <p style="margin:8px 0;color:#aaa">Preço atual: <strong style="color:#fff">${priceStr}</strong></p>

  <div style="margin-top:16px">
    <p class="section-title">Risco de squeeze (${opts.nDangerous}/4 sinais, precisa 2+)</p>
    ${_reqList(opts.presentRiskSignals, true)}
    ${_reqList(opts.missingRiskSignals, false)}
  </div>

  <div style="margin-top:16px">
    <p class="section-title">Reversão técnica (${opts.confirmCount}/4 confirmações, precisa 2+)</p>
    ${_reqList(opts.presentConfirmSignals, true)}
    ${_reqList(opts.missingConfirmSignals, false)}
  </div>

  ${opts.earningsImminent || opts.excludedEarningsReactionSignals.length
    ? `<div style="margin-top:16px">
    <p class="section-title">Earnings</p>
    ${_reqList(opts.missingEventSignals, false)}
    ${opts.excludedEarningsReactionSignals
      .map((label) => `<div style="margin:3px 0;padding-left:4px;color:#5A7679">⚠ ${label}</div>`)
      .join("")}
  </div>`
    : ""}
</div>
<div class="footer">Gerado automaticamente pelo Pré-Mercado Agente. Sinal técnico preliminar -- cruzar com notícias antes de tratar como confirmação. Não é recomendação de investimento.</div>
</body></html>`;

  try {
    const transporter = createTransport();
    await transporter.sendMail({
      from: `"Pré-Mercado Agente" <${process.env.SMTP_USER}>`,
      to,
      subject,
      html,
    });
    logger.info({ to, subject, tier: opts.tier }, "Squeeze alert e-mail sent");
  } catch (err) {
    logger.error({ err }, "Failed to send squeeze alert e-mail");
  }
}

export async function sendPortfolioHoldingEmail(opts: {
  to: string | null;
  ticker: string;
  purchaseDate: string;
  milestone: number;
  amount: number | string;
}): Promise<void> {
  const to = opts.to?.trim();
  if (!to) { logger.warn({ ticker: opts.ticker }, "No notify email on record — skipping holding alert"); return; }
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
  <p style="margin:4px 0;color:#aaa">Valor investido: <strong style="color:#fff">$${(toNum(opts.amount) ?? 0).toFixed(2)}</strong></p>
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
  to: string | null;
  ticker: string;
  salePrice: number | string;
  currentPrice: number | string;
  dropPct: number | string;       // queda % vs. preço de venda (valor positivo)
  thresholdPct: number | string;  // limiar que disparou
}): Promise<void> {
  const to = opts.to?.trim();
  if (!to) { logger.warn({ ticker: opts.ticker }, "No notify email on record — skipping recompra alert"); return; }
  if (!process.env.SMTP_USER || !process.env.SMTP_PASS) { logger.warn("SMTP not configured"); return; }

  const salePrice = toNum(opts.salePrice) ?? 0;
  const currentPrice = toNum(opts.currentPrice) ?? 0;
  const dropPct = toNum(opts.dropPct) ?? 0;
  const thresholdPct = toNum(opts.thresholdPct) ?? 0;

  const subject = `🔄 Recompra? ${opts.ticker} caiu ${dropPct.toFixed(1)}% abaixo do preço de venda`;
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
  <div class="change">▼ ${dropPct.toFixed(2)}%</div>
  <p style="margin:8px 0;color:#aaa">Preço de venda: <strong style="color:#fff">$${salePrice.toFixed(2)}</strong></p>
  <p style="margin:4px 0;color:#aaa">Preço atual: <strong style="color:#fff">$${currentPrice.toFixed(2)}</strong></p>
  <p style="margin:4px 0;color:#666;font-size:12px">
    Caiu mais de ${thresholdPct}% abaixo do preço em que você vendeu — possível ponto de recompra.
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

export async function sendScenarioAlertEmail(opts: {
  to: string | null;
  dataAlvo: string; // YYYY-MM-DD
  thresholdPct: number | string;
  pEmpatePct: number; // probabilidade atual de empatar, já em %
  caixa: number;
  risco: number;
  custoTotal: number;
}): Promise<void> {
  const to = opts.to?.trim();
  if (!to) { logger.warn("No notify email on record — skipping scenario alert"); return; }
  if (!process.env.SMTP_USER || !process.env.SMTP_PASS) { logger.warn("SMTP not configured"); return; }

  const thresholdPct = toNum(opts.thresholdPct) ?? 50;
  const dataAlvoBr = opts.dataAlvo.split("-").reverse().join("/");

  const subject = `⚠️ Painel de Cenários: ${opts.pEmpatePct.toFixed(0)}% de chance de empatar até ${dataAlvoBr}`;
  const html = `<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body{font-family:'Courier New',monospace;background:#111;color:#e0e0e0;padding:24px}
  .pct{font-size:32px;font-weight:bold;color:#ef4444}
  .box{background:#1a1a1a;border:1px solid #333;border-radius:8px;padding:16px;margin:16px 0}
  .footer{margin-top:32px;font-size:11px;color:#555}
</style></head>
<body>
<p style="color:#555;font-size:12px;text-transform:uppercase;">Alerta de Cenário — Pré-Mercado Agente</p>
<div class="box">
  <div class="pct">${opts.pEmpatePct.toFixed(0)}% de chance de empatar</div>
  <p style="margin:8px 0;color:#aaa">Data-alvo: <strong style="color:#fff">${dataAlvoBr}</strong></p>
  <p style="margin:4px 0;color:#aaa">Limiar configurado: <strong style="color:#fff">${thresholdPct.toFixed(0)}%</strong></p>
  <p style="margin:4px 0;color:#aaa">Garantido em caixa: <strong style="color:#fff">$${opts.caixa.toFixed(2)}</strong></p>
  <p style="margin:4px 0;color:#aaa">Em risco: <strong style="color:#fff">$${opts.risco.toFixed(2)}</strong></p>
  <p style="margin:4px 0;color:#aaa">Break-even (total investido): <strong style="color:#fff">$${opts.custoTotal.toFixed(2)}</strong></p>
  <p style="margin:12px 0 0;color:#666;font-size:12px">
    Cenário neutro (sem movimento de setor, volatilidade base, nenhuma posição travada em caixa
    manualmente). Ferramenta de dimensionamento de risco — não é recomendação de compra ou venda.
  </p>
</div>
<div class="footer">Gerado automaticamente pelo Pré-Mercado Agente. Reenvia no máximo 1x/24h enquanto a condição persistir.</div>
</body></html>`;

  try {
    const transporter = createTransport();
    await transporter.sendMail({
      from: `"Pré-Mercado Agente" <${process.env.SMTP_USER}>`,
      to,
      subject,
      html,
    });
    logger.info({ to, subject }, "Scenario alert e-mail sent");
  } catch (err) {
    logger.error({ err }, "Failed to send scenario alert e-mail");
  }
}

/**
 * Prefixo do assunto por MODO de execução.
 *
 * Todos os fluxos usavam "Pré-Mercado ..." no assunto, então a caixa de
 * entrada misturava relatório diário, Veredito do Dia, revisão de plano de
 * saída e varredura de notícias sob o mesmo rótulo. Revendo 7 e-mails
 * seguidos, só 3 eram de fato o relatório pré-mercado -- os outros eram
 * fluxos diferentes, com prompt, ferramentas e critérios próprios. O arquivo
 * fica ilegível depois de alguns dias, e comparar um com o outro mede ruído.
 */
const ASSUNTO_POR_MODO: Record<string, string> = {
  daily: "Pré-Mercado",
  premarket: "Flash Pré-Mercado",
  veredito: "Veredito do Dia",
  exit_plan: "Plano de Saída",
  portfolio: "Carteira",
  news: "Notícias",
  alerts: "Alertas",
  coal: "Setor Carvão",
  ai: "Setor IA",
};

export async function sendReportEmail(
  reportContent: string,
  date: string,
  tickers?: string[],
  mode = "daily",
): Promise<void> {
  const to = await resolveNotifyEmail();
  if (!to) {
    logger.warn("No notify email configured — skipping e-mail notification");
    return;
  }
  if (!process.env.SMTP_USER || !process.env.SMTP_PASS) {
    logger.warn("SMTP_USER or SMTP_PASS not set — skipping e-mail notification");
    return;
  }

  const prefixo = ASSUNTO_POR_MODO[mode] ?? "Pré-Mercado";
  const subject = `${prefixo} ${date}${tickers && tickers.length ? ` — ${tickers.join(", ")}` : ""}`;

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
