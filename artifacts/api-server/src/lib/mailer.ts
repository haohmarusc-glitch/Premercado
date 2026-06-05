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
