import { Router, type IRouter } from "express";
import path from "path";
import { desc, gte } from "drizzle-orm";
import { db, macroRiskSnapshotsTable } from "@workspace/db";
import { getPythonBin, agentDir } from "../lib/runner";
import { spawnPython } from "../lib/python-spawn";
import { comVagaPython } from "../lib/vaga-python";
import { logger } from "../lib/logger";

const router: IRouter = Router();

// A coleta bate em FRED, yfinance (3 tickers) e no feed de notícias. 90s é
// folgado de propósito: o teto externo do Node tem que ser MAIOR que a soma dos
// internos (playbook §3), e aqui não há orçamento interno -- cada fetch tem seu
// próprio timeout curto e falha isolada.
const TIMEOUT_MS = 90_000;

type Retrato = {
  snapshotDate?: string;
  aggregate_score?: number | null;
  cobertura_pct?: number;
  fontesDegradadas?: Record<string, string>;
  evaluated_at?: string;
  error?: string;
  [k: string]: unknown;
};

function coletar(): Promise<Retrato> {
  return comVagaPython("macro-risk", () => new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", "macro_risk_snapshot.py");
    const py = spawnPython(getPythonBin(), [scriptPath]);
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout na coleta macro")); }, TIMEOUT_MS);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) return reject(new Error(err.slice(-400) || "script falhou"));
      try {
        resolve(JSON.parse(out) as Retrato);
      } catch {
        // O stdout vira parte do erro: sem isso, "Parse error" manda o operador
        // rodar o script à mão dentro do container pra descobrir o que veio --
        // foi o que custou horas no NaN de 18/08 (ver technicals.ts).
        reject(new Error(`resposta ilegível. stdout: ${out.slice(0, 300)}`));
      }
    });
  }));
}

const NOMES_DOS_SINAIS = [
  "RATE_SHOCK", "ASIA_MEMORY_CONTAGION", "PRICED_FOR_PERFECTION",
  "CHINA_COMPETITION_RISK", "OVEREXTENDED_SECTOR", "GEOPOLITICAL_OIL_SHOCK",
];

function flagsAtivos(r: Retrato): string[] {
  return NOMES_DOS_SINAIS.filter((n) => {
    const s = r[n] as { active?: boolean } | undefined;
    return Boolean(s?.active);
  });
}

/**
 * Grava o retrato do dia. Upsert por data: reavaliar no mesmo pregão
 * SOBRESCREVE em vez de duplicar -- a série é "um retrato por dia", e duas
 * linhas com a mesma data fariam o gráfico contar o dia duas vezes.
 *
 * Falha de escrita não derruba a resposta: o usuário pediu o retrato, e
 * devolvê-lo sem ter conseguido persistir é melhor que devolver erro. Mas vai
 * para o log -- persistência falhando em silêncio viraria série com buracos que
 * ninguém explica depois.
 */
async function persistir(r: Retrato): Promise<void> {
  const dia = r.snapshotDate || new Date().toISOString().slice(0, 10);
  const valores = {
    snapshotDate: dia,
    evaluatedAt: r.evaluated_at ? new Date(r.evaluated_at) : new Date(),
    // null quando a cobertura ficou abaixo do mínimo. Coalescer para 0 aqui
    // desfaria, na borda do banco, a distinção que o módulo inteiro protege.
    aggregateScore: r.aggregate_score ?? null,
    coveragePct: r.cobertura_pct ?? 0,
    activeFlags: flagsAtivos(r),
    degradedSources: r.fontesDegradadas ?? {},
    raw: r as Record<string, unknown>,
  };
  await db.insert(macroRiskSnapshotsTable).values(valores)
    .onConflictDoUpdate({ target: macroRiskSnapshotsTable.snapshotDate, set: valores });
}

/** Retrato de hoje, recém-coletado, e já persistido. */
router.get("/macro-risk", async (_req, res, next): Promise<void> => {
  try {
    const retrato = await coletar();
    if (retrato.error) {
      res.status(502).json({ error: retrato.error });
      return;
    }
    try {
      await persistir(retrato);
    } catch (err) {
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
