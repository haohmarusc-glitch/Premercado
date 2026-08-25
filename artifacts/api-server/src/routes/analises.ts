import { Router, type IRouter } from "express";
import path from "path";
import { coalescer } from "../lib/em-voo";
import { getPythonBin, agentDir } from "../lib/runner";
import { logger } from "../lib/logger";
import { spawnPython } from "../lib/python-spawn";
import { comVagaPython } from "../lib/vaga-python";

const router: IRouter = Router();

// Cache de 30min por (ticker, anos): a análise varre 5 anos de histórico do
// papel MAIS quatro séries de fator, e o resultado só muda de um pregão para
// o outro -- reprocessar a cada abertura de tela seria custo puro.
const CACHE_TTL_MS = 30 * 60_000;
const cache = new Map<string, { data: unknown; fetchedAt: number }>();

// A varredura roda ~17 testes de permutação de 2000 sorteios cada, sobre
// cinco downloads do yfinance. Medido em ~25s no container; o teto dá folga
// de 4x para dia de rede ruim, e fica abaixo do timeout do proxy.
const TIMEOUT_MS = 120_000;
const ANOS_MIN = 2;
const ANOS_MAX = 10;

function runPadroes(ticker: string, anos: number): Promise<unknown> {
  return coalescer(`padroes:${ticker}:${anos}`, () => comVagaPython("padroes", () => new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", "padroes_estatisticos.py");
    const py = spawnPython(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify({ ticker, anos }));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("Análise de padrões expirou")); }, TIMEOUT_MS);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) return reject(new Error(err || "Script failed"));
      try {
        return resolve(JSON.parse(out));
      } catch {
        // Pontas do stdout no log -- mesmo remédio de technicals.ts/analysis.ts
        // (um NaN no meio do JSON derruba a resposta inteira e o erro sozinho
        // não diz onde).
        logger.error(
          { stdoutHead: out.slice(0, 500), stdoutTail: out.slice(-500), bytes: out.length,
            stderr: err.slice(-1000) },
          "padroes: stdout não é JSON",
        );
        return reject(new Error("Parse error"));
      }
    });
    py.on("error", (e) => { clearTimeout(t); reject(e); });
  })));
}

// POST /analises/padroes — sazonalidade, dia da semana, dias de evento macro
// e sensibilidade a fatores, cada padrão com n, IC 95% por bootstrap e
// p-valor de permutação corrigido por Holm. Ver agent/padroes_estatisticos.py
// para o porquê da correção (varrer 17 padrões a 5% acha ~1 por acaso).
router.post("/analises/padroes", async (req, res, next): Promise<void> => {
  const ticker = String(req.body?.ticker ?? "").trim().toUpperCase();
  if (!ticker) {
    res.status(400).json({ error: "ticker é obrigatório" });
    return;
  }
  const anosPedido = Number(req.body?.anos);
  const anos = Number.isFinite(anosPedido)
    ? Math.min(ANOS_MAX, Math.max(ANOS_MIN, Math.round(anosPedido)))
    : 5;

  const chave = `${ticker}:${anos}`;
  const cached = cache.get(chave);
  if (cached && Date.now() - cached.fetchedAt < CACHE_TTL_MS) {
    res.json(cached.data);
    return;
  }

  try {
    const data = await runPadroes(ticker, anos);
    cache.set(chave, { data, fetchedAt: Date.now() });
    res.json(data);
  } catch (e: unknown) {
    next(e);
  }
});

export default router;
