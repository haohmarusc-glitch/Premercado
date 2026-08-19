import { Router, type IRouter } from "express";
import { desc, gte } from "drizzle-orm";
import { db, macroRiskSnapshotsTable } from "@workspace/db";
import { coletarMacroRisk, persistirMacroRisk } from "../lib/macro-risk";
import { logger } from "../lib/logger";

const router: IRouter = Router();

/** Retrato de hoje, recém-coletado, e já persistido. */
router.get("/macro-risk", async (_req, res, next): Promise<void> => {
  try {
    const retrato = await coletarMacroRisk();
    if (retrato.error) {
      res.status(502).json({ error: retrato.error });
      return;
    }
    try {
      await persistirMacroRisk(retrato);
    } catch (err) {
      // Falha de escrita não derruba a resposta: o usuário pediu o retrato, e
      // devolvê-lo sem ter conseguido persistir é melhor que devolver erro. Mas
      // vai para o log -- persistência falhando em silêncio viraria série com
      // buracos que ninguém explica depois.
      logger.error({ err }, "macro-risk: falha ao persistir o retrato do dia");
    }
    res.json(retrato);
  } catch (err) { next(err); }
});

/** Série histórica para o gráfico. `dias` limita a janela. */
router.get("/macro-risk/serie", async (req, res, next): Promise<void> => {
  try {
    const dias = Math.min(Math.max(Number(req.query.dias) || 90, 1), 365);
    const desde = new Date(Date.now() - dias * 86_400_000).toISOString().slice(0, 10);
    const linhas = await db.select({
      snapshotDate: macroRiskSnapshotsTable.snapshotDate,
      aggregateScore: macroRiskSnapshotsTable.aggregateScore,
      coveragePct: macroRiskSnapshotsTable.coveragePct,
      activeFlags: macroRiskSnapshotsTable.activeFlags,
    })
      .from(macroRiskSnapshotsTable)
      .where(gte(macroRiskSnapshotsTable.snapshotDate, desde))
      .orderBy(desc(macroRiskSnapshotsTable.snapshotDate))
      .limit(365);
    res.json({ itens: linhas });
  } catch (err) { next(err); }
});

export default router;
