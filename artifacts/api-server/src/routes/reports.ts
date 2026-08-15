import { Router, type IRouter } from "express";
import { and, desc, eq, isNull, or } from "drizzle-orm";
import { db, reportsTable, usersTable } from "@workspace/db";
import {
  GetReportParams,
  GetReportResponse,
  GetLatestReportResponse,
  ListReportsResponse,
} from "@workspace/api-zod";
import { sendRelatorioDeTelaEmail } from "../lib/mailer";
import { parseExportBody } from "../lib/report-export-params";
import { logger } from "../lib/logger";

const router: IRouter = Router();

type ReportRow = typeof reportsTable.$inferSelect;

function serializeReport(row: ReportRow) {
  return { ...row, createdAt: row.createdAt.toISOString() };
}

// Visível pra um usuário: relatório "de casa" (userId null -- daily/premarket/
// coal/ai/news/alerts/manual/scheduled, compartilhados desde sempre) OU
// relatório gerado a partir da PRÓPRIA carteira dele (portfolio/veredito, ver
// reportsTable.userId e runner.ts). Sem isso, /reports devolvia a tabela
// inteira pra qualquer usuário logado -- incluindo o veredito/análise de
// carteira gerado por OUTRO usuário.
function visibleToUser(userId: number) {
  return or(isNull(reportsTable.userId), eq(reportsTable.userId, userId));
}

router.get("/reports", async (req, res): Promise<void> => {
  const rows = await db
    .select()
    .from(reportsTable)
    .where(visibleToUser(req.userId!))
    .orderBy(desc(reportsTable.createdAt));
  res.json(ListReportsResponse.parse(rows.map(serializeReport)));
});

// Returns the latest report of a given mode (default "daily", used by the
// dashboard main view; "veredito" is used by the tela Veredito do Dia).
router.get("/reports/latest", async (req, res): Promise<void> => {
  const mode = typeof req.query.mode === "string" && req.query.mode ? req.query.mode : "daily";
  const [row] = await db
    .select()
    .from(reportsTable)
    .where(and(eq(reportsTable.mode, mode), visibleToUser(req.userId!)))
    .orderBy(desc(reportsTable.createdAt))
    .limit(1);
  if (!row) {
    res.status(404).json({ error: "No reports yet" });
    return;
  }
  res.json(GetLatestReportResponse.parse(serializeReport(row)));
});

// POST /reports/export — salva no histórico o que a tela montou e,
// opcionalmente, envia por e-mail.
//
// Sempre grava com `userId` de quem clicou, mesmo pras telas que não derivam
// de carteira. Relatório exportado é retrato pessoal do que a pessoa estava
// olhando, com os filtros dela; publicar como relatório "de casa" (userId
// null) o deixaria visível pra todos os usuários — o vazamento que o comentário
// de `visibleToUser` acima registra ter acontecido.
router.post("/reports/export", async (req, res): Promise<void> => {
  const parsed = parseExportBody(req.body);
  if (!parsed.ok) {
    res.status(parsed.status).json({ error: parsed.erro });
    return;
  }
  const { titulo, markdown, mode, tickers, enviar } = parsed.valor;

  const date = new Date().toISOString().slice(0, 10);

  const [row] = await db
    .insert(reportsTable)
    .values({ date, content: markdown, tickers, mode, userId: req.userId! })
    .returning();

  if (!enviar) {
    res.json({ id: row.id, date: row.date, enviado: false });
    return;
  }

  // O destinatário é o e-mail de login de quem clicou, não o notifyEmail
  // compartilhado das configurações (ver sendRelatorioDeTelaEmail).
  const [dono] = await db
    .select({ email: usersTable.email })
    .from(usersTable)
    .where(eq(usersTable.id, req.userId!))
    .limit(1);

  if (!dono?.email) {
    res.status(200).json({
      id: row.id, date: row.date, enviado: false,
      erroEnvio: "Sua conta não tem e-mail cadastrado — o relatório foi salvo no histórico.",
    });
    return;
  }

  try {
    await sendRelatorioDeTelaEmail({ to: dono.email, titulo, markdown, date });
    res.json({ id: row.id, date: row.date, enviado: true, email: dono.email });
  } catch (err) {
    // O relatório JÁ está gravado. Falha de SMTP não pode devolver erro seco,
    // senão a tela diz "falhou" e a pessoa reclica, duplicando no histórico.
    logger.error({ err, reportId: row.id }, "Failed to e-mail exported screen report");
    res.status(200).json({
      id: row.id, date: row.date, enviado: false,
      erroEnvio: "Salvo no histórico, mas o envio por e-mail falhou. Veja em Histórico.",
    });
  }
});

router.get("/reports/:id", async (req, res): Promise<void> => {
  const params = GetReportParams.safeParse(req.params);
  if (!params.success) {
    res.status(400).json({ error: params.error.message });
    return;
  }
  const [row] = await db
    .select()
    .from(reportsTable)
    .where(and(eq(reportsTable.id, params.data.id), visibleToUser(req.userId!)));
  if (!row) {
    res.status(404).json({ error: "Report not found" });
    return;
  }
  res.json(GetReportResponse.parse(serializeReport(row)));
});

export default router;
