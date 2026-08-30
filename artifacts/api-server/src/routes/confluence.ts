import { Router, type IRouter } from "express";
import { spawnAgente } from "../lib/runner";
import { comVagaPython } from "../lib/vaga-python";

const router: IRouter = Router();

function runConfluence(payload: object): Promise<unknown> {
  // comVagaPython -- teto de Python simultâneo vindo de rota HTTP.
  // Ver lib/vaga-python.ts.
  return comVagaPython("confluence", () => new Promise((resolve, reject) => {
    const py = spawnAgente("confluence_engine.py");
    py.stdin.write(JSON.stringify(payload));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout")); }, 30_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(err || "Script failed")); return; }
      try { resolve(JSON.parse(out)); } catch { reject(new Error("Parse error")); }
    });
  }));
}

// POST /confluence — avalia o ConfluenceEngine (trend/momentum/volatility/
// volume/sector, mais o veto de calendário) para o símbolo pedido, buscando
// OHLCV direto via yfinance dentro do subprocesso Python (não há tabela de
// OHLCV no Postgres -- mesmo padrão de /backtest, /risk/* e /technicals).
router.post("/confluence", async (req, res, next): Promise<void> => {
  const { symbol, period, minVotes, kellyFraction } = req.body;
  if (!symbol) { res.status(400).json({ error: "symbol is required" }); return; }

  try {
    const data = await runConfluence({
      symbol: String(symbol).toUpperCase(),
      period: period ?? "18mo",
      minVotes: minVotes ?? 4,
      kellyFraction: kellyFraction ?? 0.3,
    });
    res.json(data);
  } catch (e: unknown) {
    next(e);
  }
});

export default router;
