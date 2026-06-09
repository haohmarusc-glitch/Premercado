/**
 * Teste de DRY-RUN do checker de alertas da carteira.
 *
 * Reusa o stack real (banco + get_quotes), calcula o que DISPARARIA agora,
 * e apenas IMPRIME o relatório. NÃO envia e-mail e NÃO grava em
 * portfolio_alert_firings — é totalmente read-only e seguro.
 *
 * Como rodar (precisa do DATABASE_URL apontando pro mesmo banco do app):
 *   DRY_RUN_ALERTS=1 DATABASE_URL="postgres://..." \
 *     pnpm --filter @workspace/api-server test portfolio-alerts.dryrun
 *
 * Sem a env DRY_RUN_ALERTS o teste é pulado (não roda no CI nem toca a rede).
 */
import { spawn } from "node:child_process";
import { describe, it, expect } from "vitest";

const RUN = !!process.env.DRY_RUN_ALERTS;
const HOLDING_MILESTONES = [30, 60, 90, 180, 365];

interface Quote {
  symbol: string;
  price: number | null;
  error: string | null;
}

describe.runIf(RUN)("portfolio alert checker — dry run (read-only)", () => {
  it(
    "reporta o que dispararia agora sem enviar e-mail nem gravar",
    async () => {
      // Dynamic imports so DATABASE_URL is only required when the test actually runs
      const { db, portfolioPositionsTable, portfolioPurchasesTable, portfolioAlertFiringsTable } =
        await import("@workspace/db");
      const { agentDir } = await import("../lib/runner");

      function fetchPrices(tickers: string[]): Promise<Quote[]> {
        return new Promise((resolve, reject) => {
          const py = spawn("python3", ["-m", "agent.get_quotes", ...tickers], {
            cwd: agentDir,
            env: { ...process.env, PYTHONPATH: agentDir },
          });
          let out = "";
          let err = "";
          py.stdout.on("data", (d: Buffer) => (out += d.toString()));
          py.stderr.on("data", (d: Buffer) => (err += d.toString()));
          py.on("close", (code) => {
            if (code !== 0) return reject(new Error(`get_quotes exited ${code}: ${err}`));
            try { resolve(JSON.parse(out) as Quote[]); }
            catch { reject(new Error(`Bad JSON from get_quotes: ${out}`)); }
          });
        });
      }

      const positions = await db.select().from(portfolioPositionsTable);
      const purchases = await db.select().from(portfolioPurchasesTable);
      const firedRows = await db
        .select({ alertKey: portfolioAlertFiringsTable.alertKey })
        .from(portfolioAlertFiringsTable);
      const fired = new Set(firedRows.map((r) => r.alertKey));

      expect(positions.length).toBeGreaterThan(0);

      const tickers = positions.map((p) => p.ticker);
      const quotes = await fetchPrices(tickers);
      const priceMap = new Map<string, number>(
        quotes.flatMap((q) => (q.price != null ? [[q.symbol, q.price]] : [])),
      );

      const lines: string[] = [];
      let novos = 0;

      lines.push("\n===== DRY-RUN: ALERTAS DE PREÇO =====");
      lines.push("TICKER  PREÇO MÉDIO   ATUAL      VAR%     GATILHOS (status)");
      for (const pos of positions) {
        const price = priceMap.get(pos.ticker);
        if (price == null) {
          lines.push(`${pos.ticker.padEnd(7)} sem preço (get_quotes não retornou)`);
          continue;
        }
        const pct = ((price - pos.avgCost) / pos.avgCost) * 100;
        const hits: string[] = [];
        for (const thr of pos.upAlertPcts) {
          if (pct >= thr) {
            const key = `gain:${pos.ticker}:${thr}`;
            const isNew = !fired.has(key);
            if (isNew) novos++;
            hits.push(`+${thr}%${isNew ? " [NOVO]" : " (já disparado)"}`);
          }
        }
        for (const thr of pos.downAlertPcts) {
          if (pct <= -thr) {
            const key = `loss:${pos.ticker}:${thr}`;
            const isNew = !fired.has(key);
            if (isNew) novos++;
            hits.push(`-${thr}%${isNew ? " [NOVO]" : " (já disparado)"}`);
          }
        }
        lines.push(
          `${pos.ticker.padEnd(7)} ${String(pos.avgCost).padStart(10)}  ${String(
            price,
          ).padStart(9)}  ${pct.toFixed(2).padStart(7)}%   ${
            hits.length ? hits.join(", ") : "—"
          }`,
        );
      }

      lines.push("\n===== DRY-RUN: MARCOS DE TEMPO =====");
      const posById = new Map(positions.map((p) => [p.id, p]));
      const now = Date.now();
      for (const pur of purchases) {
        const pos = posById.get(pur.positionId);
        if (!pos) continue;
        const ageDays = Math.floor(
          (now - new Date(pur.purchaseDate).getTime()) / 86_400_000,
        );
        const ms = HOLDING_MILESTONES.filter((m) => ageDays >= m).map((m) => {
          const key = `holding:${pos.ticker}:${pur.purchaseDate}:${m}`;
          const isNew = !fired.has(key);
          if (isNew) novos++;
          return `${m}d${isNew ? " [NOVO]" : " (já disparado)"}`;
        });
        if (ms.length) {
          lines.push(
            `${pos.ticker.padEnd(7)} lote ${pur.purchaseDate} (${ageDays}d, US$${pur.amount}): ${ms.join(", ")}`,
          );
        }
      }

      lines.push(`\nTotal de alertas NOVOS que seriam enviados agora: ${novos}`);
      lines.push("(dry-run: nada foi enviado nem gravado)\n");

      console.log(lines.join("\n"));
      expect(true).toBe(true);
    },
    60_000, // get_quotes vai à rede (yfinance)
  );
});
