import { Router, type IRouter } from "express";
import path from "path";
import { getPythonBin, agentDir } from "../lib/runner";
import { spawnPython } from "../lib/python-spawn";
import { comVagaPython } from "../lib/vaga-python";
import { coalescer } from "../lib/em-voo";
import { linhaDeGasto, type UsoLlm } from "../lib/ai-spend-record";
import { db, agentRunsTable } from "@workspace/db";
import { logger } from "../lib/logger";

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
  // Referência do excesso na trajetória pós-earnings. Vem da tela (mapa por
  // setor); ausente ou malformado deixa o script cair no padrão dele (SPY).
  const rawBenchmark = String(req.body?.benchmark ?? "").trim().toUpperCase();
  const benchmark = /^[A-Z0-9.^-]{1,10}$/.test(rawBenchmark) ? rawBenchmark : undefined;

  try {
    // ~12s por ticker (chamada de earnings dates + histórico de preço via
    // yfinance) -- 5 tickers (padrão) cai nos mesmos 60s de antes.
    const timeoutMs = 12_000 * Math.max(1, tickers?.length ?? 5);
    const data = await runEarningsReactionScript({ tickers, lookback, benchmark }, timeoutMs);
    res.json(data);
  } catch (e) {
    next(e);
  }
});

// ── interpretação com IA da CESTA ───────────────────────────────────────────
//
// Uma leitura só para todos os tickers, não um botão por papel. A tela já
// interpreta cada um por regra (`interpretResult` no front): classe de
// volatilidade, viés direcional, se o movimento amplia ao longo do pregão.
// O que a regra não alcança é COMPARAÇÃO -- ela é por ticker por construção --
// e é isso que a IA acrescenta. Um botão por papel produziria cinco chamadas
// para redizer o que está escrito ao lado.

// Teto externo. Tem que ser MAIOR que o interno do Python (175s, ver
// reacao_earnings_ia.py), senão o Node descobre o problema matando o processo
// e o usuário recebe um 500 genérico -- playbook §3.
const TIMEOUT_IA_MS = 195_000;

// Mesmo desenho de analysis.ts: a chave é o corpo inteiro, então cesta
// diferente é chave diferente. O TTL só impede o retrato de envelhecer na
// memória; ele nunca serve leitura de dados diferentes dos pedidos.
const CACHE_IA_TTL_MS = 10 * 60_000;
const cacheIA = new Map<string, { valor: unknown; em: number }>();

function guardarIA(chave: string, valor: unknown): void {
  // Erro não entra: ele é do momento (provedor fora, orçamento estourado), e
  // guardá-lo transformaria falha passageira em dez minutos de falha garantida.
  if (valor && typeof valor === "object" && "error" in valor) return;
  cacheIA.set(chave, { valor, em: Date.now() });
  for (const [k, v] of cacheIA) {
    if (Date.now() - v.em > CACHE_IA_TTL_MS) cacheIA.delete(k);
  }
}

// Idade máxima para entrar de carona: embarcar num trabalho que já gastou o
// próprio orçamento não é economia, é herdar uma morte marcada.
const IDADE_MAX_CARONA_MS = 60_000;

/** Preenchido só por quem REALMENTE spawnou o Python. */
interface Execucao { gastoNovo: boolean }

/**
 * `exec.gastoNovo` responde "esta requisição pagou por tokens?", que é
 * diferente de "esta requisição recebeu um texto". Cache e carona devolvem o
 * `usage` junto (a tela mostra o custo), e registrar isso lançaria em
 * agent_runs uma chamada de API que nunca houve -- foi o bug corrigido em
 * analysis.ts em 19/08/2026, com duas linhas de US$ 0,062852 terminando no
 * mesmo instante.
 *
 * Só a closure que o coalescer executa sabe a resposta: quem pega carona nunca
 * roda a dela.
 */
function runReacaoIA(payload: object, exec: Execucao): Promise<unknown> {
  const chave = `reacao_earnings_ia:${JSON.stringify(payload)}`;

  const guardado = cacheIA.get(chave);
  if (guardado && Date.now() - guardado.em <= CACHE_IA_TTL_MS) {
    logger.info({ idadeMs: Date.now() - guardado.em },
      "reacao_earnings_ia: devolvendo interpretação já calculada (mesma cesta)");
    return Promise.resolve(guardado.valor);
  }

  return coalescer(chave, () => comVagaPython("reacao_earnings_ia", () => new Promise((resolve, reject) => {
    // Daqui para baixo é execução de verdade: o Python vai ser spawnado e os
    // tokens vão ser cobrados, dê certo ou não.
    exec.gastoNovo = true;
    // Spawn como MÓDULO do pacote, não por caminho: reacao_earnings_ia.py usa
    // `from agent.x import y`, e por caminho o `agent/agent.py` sombreia o
    // pacote -- o erro é "attempted relative import with no known parent
    // package", já visto em produção com o coletor de risco macro.
    const py = spawnPython(getPythonBin(), ["-m", "agent.reacao_earnings_ia"], {
      cwd: agentDir,
      env: { ...process.env, PYTHONPATH: agentDir },
    });
    py.stdin.write(JSON.stringify(payload));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => {
      py.kill("SIGTERM");
      // O stderr vai junto: as linhas [provider] dizem QUEM foi tentado e por
      // que falhou. Descartá-las deixa o log com um "timeout" que não explica
      // nada -- foi o que aconteceu na Análise Rápida em 17/08/2026.
      logger.error({ stderr: err.slice(-2000) },
        "reacao_earnings_ia: estourou o orçamento de tempo");
      reject(new Error("timeout"));
    }, TIMEOUT_IA_MS);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) return reject(new Error(err || "Script failed"));
      try {
        resolve(JSON.parse(out));
      } catch {
        // Parse resiliente: basta UMA biblioteca imprimir em stdout durante o
        // import para o JSON.parse do bloco inteiro falhar e o texto (já
        // gerado e já pago) ser jogado fora.
        const ultima = out.trim().split("\n").filter(Boolean).pop() ?? "";
        try {
          resolve(JSON.parse(ultima));
        } catch {
          logger.error({ out: out.slice(0, 500), stderr: err.slice(-2000) },
            "reacao_earnings_ia: saída não é JSON");
          reject(new Error("Parse error"));
        }
      }
    });
  })), IDADE_MAX_CARONA_MS).then((valor) => {
    // Guarda DEPOIS de resolver e independentemente de quem estava ouvindo: o
    // caso que importa é justamente aquele em que o cliente já foi embora.
    guardarIA(chave, valor);
    return valor;
  });
}

// Teto do corpo aceito. A cesta cabe com folga; corpo maior é anomalia, não uso
// legítimo -- e viraria custo de token.
const LIMITE_CORPO_IA = 256 * 1024;

// Toda chamada de LLM aparece na tela Gastos com IA, que lê de agent_runs.
// Falha aqui não derruba a resposta: o texto já foi gerado e pago; perder o
// registro é ruim, perder o texto seria pior.
async function registrarGastoIA(
  tickers: string, usage: UsoLlm | undefined, durationMs: number, erro?: string,
): Promise<void> {
  const linha = linhaDeGasto(`reacao_earnings_ia:${tickers}`, usage, durationMs, erro);
  if (!linha) return;
  try {
    await db.insert(agentRunsTable).values(linha);
  } catch (err) {
    logger.warn({ err }, "earnings-reaction/ia: falha ao registrar gasto");
  }
}

router.post("/earnings-reaction/ia", async (req, res): Promise<void> => {
  const inicio = Date.now();
  const exec: Execucao = { gastoNovo: false };
  const results = Array.isArray(req.body?.results) ? req.body.results : [];
  const rotulo = results
    .map((r: unknown) => String((r as { ticker?: unknown })?.ticker ?? "?"))
    .join(",")
    .slice(0, 60);
  try {
    if (!results.length) {
      res.status(400).json({ error: "Rode a análise de reação a earnings antes de pedir a interpretação" });
      return;
    }
    if (JSON.stringify(req.body).length > LIMITE_CORPO_IA) {
      res.status(400).json({ error: "payload grande demais" });
      return;
    }
    const data = await runReacaoIA({
      results,
      lookback: req.body?.lookback ?? null,
      benchmark: req.body?.benchmark ?? null,
    }, exec) as { usage?: UsoLlm; error?: string };
    // Sem gasto novo (cache ou carona) não há lançamento: o livro registra
    // chamadas de API, não entregas de texto.
    if (exec.gastoNovo) {
      await registrarGastoIA(rotulo, data.usage, Date.now() - inicio, data.error);
    }
    res.json(data);
  } catch (err) {
    logger.error({ err }, "Failed: /earnings-reaction/ia");
    if (exec.gastoNovo) {
      await registrarGastoIA(rotulo, undefined, Date.now() - inicio, String(err));
    }
    res.status(500).json({ error: "Falha na interpretação com IA" });
  }
});

export default router;
