import { Router, type IRouter } from "express";
import path from "path";
import { getPythonBin, agentDir } from "../lib/runner";
import { spawnPython } from "../lib/python-spawn";
import { comVagaPython } from "../lib/vaga-python";

const router: IRouter = Router();

function runEarningsReactionScript(payload: object, timeoutMs = 60_000): Promise<unknown> {
  // comVagaPython -- teto de Python simultâneo vindo de rota HTTP.
  // Ver lib/vaga-python.ts.
  return comVagaPython("earnings_reaction", () => new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", "earnings_reaction_analysis.py");
    const py = spawnPython(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify(payload));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    // Script determinístico (sem LLM) -- só faz chamadas yfinance por
    // ticker, então 60s cobre com folga até a cesta padrão de 5 tickers.
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("Earnings reaction analysis timed out")); }, timeoutMs);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) { reject(new Error(err || "Script failed")); return; }
      try { resolve(JSON.parse(out)); } catch { reject(new Error("Failed to parse script output")); }
    });
  }));
}

// Sem estado no servidor de propósito -- os dados do yfinance mudam pouco
// (só em dia de earnings novo), então cada clique no botão "Rodar análise"
// já busca fresco; não precisa de tabela/cache no banco pra isso.
router.post("/earnings-reaction/run", async (req, res, next): Promise<void> => {
  const rawTickers = req.body?.tickers;
  const tickers = Array.isArray(rawTickers)
    ? rawTickers.map((t: unknown) => String(t).trim().toUpperCase()).filter(Boolean)
    : undefined; // undefined/vazio -> script usa DEFAULT_TICKERS
  const lookback = typeof req.body?.lookback === "number" ? req.body.lookback : 8;

  try {
    // ~12s por ticker (chamada de earnings dates + histórico de preço via
    // yfinance) -- 5 tickers (padrão) cai nos mesmos 60s de antes.
    const timeoutMs = 12_000 * Math.max(1, tickers?.length ?? 5);
    const data = await runEarningsReactionScript({ tickers, lookback }, timeoutMs);
    res.json(data);
  } catch (e) {
    next(e);
  }
});

export default router;
