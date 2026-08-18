import { Router, type IRouter } from "express";
import { coalescer } from "../lib/em-voo";
import path from "path";
import { desc, gte } from "drizzle-orm";
import { db, intradaySpikesTable, agentRunsTable, type IntradaySpike } from "@workspace/db";
import { getPythonBin, agentDir } from "../lib/runner";
import { getOrCreateSettings } from "./settings";
import { logger } from "../lib/logger";
import { spawnPython } from "../lib/python-spawn";
import { comVagaPython } from "../lib/vaga-python";
import { linhaDeGasto, type UsoLlm } from "../lib/ai-spend-record";

const router: IRouter = Router();

// Coalescido no ponto do spawn, não em cada rota: assim toda rota que usa
// runPython herda a proteção, inclusive as que forem adicionadas depois.
function runPython(script: string, payload: object): Promise<unknown> {
  // comVagaPython POR DENTRO do coalescer: requests idênticas já foram
  // fundidas numa só antes de disputar vaga. Invertido, a segunda ocuparia uma
  // vaga só pra esperar a primeira. Ver lib/vaga-python.ts.
  return coalescer(`${script}:${JSON.stringify(payload)}`, () => comVagaPython(script, () => new Promise((resolve, reject) => {
    const scriptPath = path.join(agentDir, "agent", script);
    const py = spawnPython(getPythonBin(), [scriptPath]);
    py.stdin.write(JSON.stringify(payload));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout")); }, 120_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) return reject(new Error(err || "Script failed"));
      try { resolve(JSON.parse(out)); } catch { reject(new Error("Parse error")); }
    });
  })));
}

// get_market_alerts_snapshot.py precisa rodar via `-m agent.xxx` (import
// absoluto do pacote), diferente de runPython() acima (caminho direto do
// script) -- market_alerts.py faz `from .cache import cached`, import
// relativo que só resolve nesse contexto de pacote. Mesmo padrão de
// routes/quotes.ts (get_quotes.py) e routes/chart.ts (get_chart.py).
function runMarketAlertsSnapshot(payload: object): Promise<unknown> {
  // Foi ESTE que apareceu duplicado no log de 04/08: dois GET /api/market-alerts
  // idênticos em voo (ids 72 e 78), 9s cada, dois interpretadores.
  return coalescer(`market_alerts:${JSON.stringify(payload)}`, () => comVagaPython("market_alerts", () => new Promise((resolve, reject) => {
    const py = spawnPython(getPythonBin(), ["-m", "agent.get_market_alerts_snapshot"], {
      cwd: agentDir,
      env: { ...process.env, PYTHONPATH: agentDir },
    });
    py.stdin.write(JSON.stringify(payload));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    // 120s (mesmo timeout de runPython() acima) -- run_all_alerts faz MUITAS
    // chamadas de rede por conta própria (peers, macro, e por ticker:
    // overbought/volume/candles/earnings/analistas/geopolítico/halt), além
    // da busca de manchetes que já é paralela.
    const t = setTimeout(() => { py.kill("SIGTERM"); reject(new Error("timeout")); }, 120_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) return reject(new Error(err || "Script failed"));
      try { resolve(JSON.parse(out)); } catch { reject(new Error("Parse error")); }
    });
  })));
}

// Síntese com IA da tela Análise Rápida. Mesmo contexto de pacote do
// runMarketAlertsSnapshot (provider.py tem import relativo). POST porque o
// corpo carrega os painéis já coletados pela tela — o script não refaz
// nenhuma busca de mercado, só transforma número em leitura.
function runAnaliseRapidaIA(payload: object): Promise<unknown> {
  return coalescer(`analise_rapida_ia:${JSON.stringify(payload)}`, () => comVagaPython("analise_rapida_ia", () => new Promise((resolve, reject) => {
    const py = spawnPython(getPythonBin(), ["-m", "agent.analise_rapida_ia"], {
      cwd: agentDir,
      env: { ...process.env, PYTHONPATH: agentDir },
    });
    py.stdin.write(JSON.stringify(payload));
    py.stdin.end();
    let out = "";
    let err = "";
    py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
    py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
    // Orçamento externo. Tem que ser MAIOR que o interno do Python, senão o
    // Node descobre o problema matando o processo e o usuário recebe um 500
    // genérico (playbook §3).
    //
    // Incidente 17/08/2026: aqui eram 90s, e o Python herdava os defaults do
    // agente -- API_TIMEOUT_SECONDS=60 × AGENT_MAX_RETRIES=1 ×
    // AGENT_TRANSIENT_RETRIES=1 = até ~245s por provedor. Uma análise passou
    // em 57,5s (já encostando no timeout de 60s da API) e as seguintes
    // bateram 90s cravados: "Failed: /analise-rapida/ia", 500 na tela.
    //
    // Agora o Python fixa o próprio orçamento (ver analise_rapida_ia.py):
    // 55s por chamada, uma tentativa por provedor. Duas tentativas de
    // provedor + coleta fundamental cabem em 135s; 150s aqui deixa margem.
    // test_orcamento_analise_ia.py lê os dois lados e falha se a invariante
    // (interno < externo) quebrar.
    const t = setTimeout(() => {
      py.kill("SIGTERM");
      // O stderr acumulado tem as linhas [provider] com qual provedor foi
      // tentado e por quê falhou -- descartá-las, como antes, deixava o
      // diagnóstico impossível: o log só dizia "timeout".
      logger.error(
        { stderr: err.slice(-2000), stdoutParcial: out.length },
        "analise_rapida_ia: estourou o orçamento de tempo",
      );
      reject(new Error("timeout"));
    }, 150_000);
    py.on("close", (code) => {
      clearTimeout(t);
      if (code !== 0) return reject(new Error(err || "Script failed"));
      // Parse resiliente. O contrato é "stdout é só o JSON final", mas basta
      // UMA biblioteca imprimir em stdout durante o import para o JSON.parse
      // do bloco inteiro falhar e a análise (já gerada e já paga) ser jogada
      // fora com um "Parse error" que não diz nada.
      //
      // 1ª tentativa: o bloco todo, o caso normal.
      // 2ª: a última linha não-vazia — o JSON final é sempre o último print,
      //     então lixo ANTES dele deixa de ser fatal.
      // Falhando as duas, o log leva as pontas do stdout: sem isso não há
      // como saber o que poluiu o pipe.
      // O script pode sair com codigo 0 E um {"error": ...} valido -- e o que
      // ele faz desde a Tarefa 0, quando nenhum provedor produz texto. Sem
      // registrar o stderr AQUI, todo o diagnostico ([provider] pulando ...,
      // stop_reason, tamanho do raciocinio) morre nesta variavel: trocar um
      // 500 mudo por um erro elegante nao pode significar trocar um erro
      // legivel por um erro bonito e inauditavel. Visto em producao
      // (18/08/2026): a tela mostrou "0 chars" e o log do container nao tinha
      // uma linha sequer sobre a causa.
      //
      // E registrar SO no erro nao basta: quando um provedor tropeca mas o
      // seguinte entrega o texto, o desfecho e sucesso -- e a linha
      // "[provider] pulando ..." era descartada junto. Esse e justamente o
      // caso CARO: a tentativa perdida foi cobrada (tokens de raciocinio
      // contam como saida) e some da auditoria. Visto em producao
      // (18/08/2026): analise a US$ 0,0608 contra os ~US$ 0,015 esperados,
      // sem uma linha no log dizendo por que.
      //
      // Marcas de tropeco, nao stderr inteiro: log de toda execucao bem
      // sucedida viraria ruido e a linha que importa se perderia nele.
      const MARCAS_DE_TROPECO = /\bpulando\b|truncou|toco/i;

      const registrarDiagnostico = (parsed: unknown): void => {
        const cauda = err.slice(-2000);
        if (parsed && typeof parsed === "object" && "error" in parsed) {
          logger.warn(
            { erro: (parsed as { error: unknown }).error, stderr: cauda },
            "analise_rapida_ia: script devolveu erro",
          );
          return;
        }
        if (MARCAS_DE_TROPECO.test(err)) {
          logger.warn(
            { stderr: cauda },
            "analise_rapida_ia: análise saiu, mas não na primeira tentativa",
          );
        }
      };

      try {
        const parsed = JSON.parse(out);
        registrarDiagnostico(parsed);
        return resolve(parsed);
      } catch {
        const ultimaLinha = out.split("\n").filter((l) => l.trim()).pop() ?? "";
        try {
          const parsed = JSON.parse(ultimaLinha);
          registrarDiagnostico(parsed);
          return resolve(parsed);
        } catch {
          logger.error(
            { stdoutHead: out.slice(0, 500), stdoutTail: out.slice(-500), bytes: out.length },
            "analise_rapida_ia: stdout não é JSON",
          );
          return reject(new Error("Parse error"));
        }
      }
    });
  })));
}

// Teto do corpo aceito — os painéis da tela cabem com folga; payload maior
// que isso é anomalia, não uso legítimo (e viraria custo de token).
const LIMITE_CORPO_IA = 64 * 1024;

// Toda chamada de LLM tem que aparecer na tela Gastos com IA — que lê de
// agent_runs/chat_messages. Sem este registro, a análise cobrava tokens e
// sumia da contabilidade: o custo aparecia só na tela, no momento do clique,
// e nunca mais. Falha aqui não derruba a resposta (o texto já foi gerado e
// pago); perder o registro é ruim, perder a análise seria pior.
async function registrarGastoIA(
  ticker: string, usage: UsoLlm | undefined, durationMs: number, erro?: string,
): Promise<void> {
  const linha = linhaDeGasto(`analise_rapida_ia:${ticker}`, usage, durationMs, erro);
  if (!linha) return;
  try {
    await db.insert(agentRunsTable).values(linha);
  } catch (err) {
    logger.warn({ err }, "analise-rapida/ia: falha ao registrar gasto");
  }
}

router.post("/analise-rapida/ia", async (req, res): Promise<void> => {
  const inicio = Date.now();
  const ticker = String(req.body?.ticker ?? "").trim().toUpperCase();
  try {
    if (!/^[A-Z0-9.^-]{1,10}$/.test(ticker)) { res.status(400).json({ error: "ticker inválido" }); return; }
    if (JSON.stringify(req.body).length > LIMITE_CORPO_IA) {
      res.status(400).json({ error: "payload grande demais" }); return;
    }
    const data = await runAnaliseRapidaIA({
      ticker,
      benchmark: String(req.body?.benchmark ?? "").trim().toUpperCase() || "SMH",
      trend: req.body?.trend ?? null,
      technicals: req.body?.technicals ?? null,
      snapshot: req.body?.snapshot ?? null,
      reaction: req.body?.reaction ?? null,
    }) as { usage?: UsoLlm; error?: string };
    // O script devolve {error} para falhas de conteúdo (resposta curta,
    // sem painel) — nesses casos o provedor pode já ter sido cobrado, então
    // o registro vale igual, marcado como failed.
    await registrarGastoIA(ticker, data.usage, Date.now() - inicio, data.error);
    res.json(data);
  } catch (err) {
    logger.error({ err }, "Failed: /analise-rapida/ia");
    await registrarGastoIA(ticker, undefined, Date.now() - inicio, String(err));
    res.status(500).json({ error: "Falha na análise com IA" });
  }
});

async function resolveTickers(raw: string): Promise<string[]> {
  const trimmed = raw.trim();
  if (trimmed) return trimmed.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
  const settings = await getOrCreateSettings();
  return settings.tickers;
}

// Simple per-endpoint cache keyed by ticker list
const caches: Record<string, { key: string; data: unknown; at: number }> = {};
function cached(name: string, key: string): unknown | null {
  const c = caches[name];
  if (c && c.key === key && Date.now() - c.at < 60_000) return c.data;
  return null;
}
function setCache(name: string, key: string, data: unknown) {
  caches[name] = { key, data, at: Date.now() };
}

function makeTickerRoute(routePath: string, script: string, cacheName: string, extra: object = {}) {
  router.get(routePath, async (req, res): Promise<void> => {
    try {
      const tickers = await resolveTickers(String(req.query.tickers ?? ""));
      if (!tickers.length) { res.json({ items: [] }); return; }
      const key = tickers.join(",");
      const hit = cached(cacheName, key);
      if (hit) { res.json(hit); return; }
      const data = await runPython(script, { tickers, ...extra });
      setCache(cacheName, key, data);
      res.json(data);
    } catch (err) {
      logger.error({ err }, `Failed: ${routePath}`);
      res.status(500).json({ error: `Failed to fetch ${routePath}` });
    }
  });
}

makeTickerRoute("/fundamentals", "get_fundamentals.py", "fundamentals");
makeTickerRoute("/trend", "get_trend.py", "trend");
makeTickerRoute("/options", "get_options_chain.py", "options");
makeTickerRoute("/news", "get_news_feed.py", "news", { maxItems: 5 });
// Congress trading (Quiver Quant) + dark pool (Unusual Whales) — cada seção
// funciona só se a env var de chave correspondente estiver configurada
// (QUIVER_API_KEY / UNUSUAL_WHALES_API_KEY); sem chave, volta
// {configured: false} em vez de erro.
makeTickerRoute("/alt-data", "get_alt_data.py", "alt-data");
// Ciclo de volatilidade (tela Previsão de Vol): fase por ticker
// (COMPRIMIDA/GATILHO/EXPANSAO/DECAIMENTO/NORMAL) + banda de amanhã.
// Sem tickers na query cai na carteira (settings), como os demais.
makeTickerRoute("/vol-cycle", "ciclo_volatilidade.py", "vol-cycle");

// Retrato rápido de um ticker avulso (tela Análise Rápida): preço/52s/MMs ao
// vivo + vol/beta vs benchmark via get_scenario_params. Um ticker por vez de
// propósito — a tela é de investigação pontual, não de varredura.
const TICKER_RE = /^[A-Z0-9.^-]{1,10}$/;
router.get("/ticker-snapshot", async (req, res): Promise<void> => {
  try {
    const ticker = String(req.query.ticker ?? "").trim().toUpperCase();
    const benchmark = String(req.query.benchmark ?? "").trim().toUpperCase() || "SMH";
    if (!TICKER_RE.test(ticker)) { res.status(400).json({ error: "ticker inválido" }); return; }
    if (!TICKER_RE.test(benchmark)) { res.status(400).json({ error: "benchmark inválido" }); return; }
    const key = `${ticker}:${benchmark}`;
    const hit = cached("ticker-snapshot", key);
    if (hit) { res.json(hit); return; }
    const data = await runPython("get_ticker_snapshot.py", { ticker, benchmark });
    setCache("ticker-snapshot", key, data);
    res.json(data);
  } catch (err) {
    logger.error({ err }, "Failed: /ticker-snapshot");
    res.status(500).json({ error: "Failed to fetch ticker snapshot" });
  }
});

// Macro — no tickers, single cache
router.get("/macro", async (_req, res): Promise<void> => {
  try {
    const hit = cached("macro", "_");
    if (hit) { res.json(hit); return; }
    const data = await runPython("get_macro.py", {});
    setCache("macro", "_", data);
    res.json(data);
  } catch (err) {
    logger.error({ err }, "Failed: /macro");
    res.status(500).json({ error: "Failed to fetch macro" });
  }
});

// Filings 13F de gestores institucionais acompanhados — no tickers, mesmo
// cache de 60s dos demais endpoints desta rota (o dado em si só muda a cada
// trimestre, o cache aqui é só pra não bater na SEC a cada clique).
router.get("/institutional-filings", async (_req, res): Promise<void> => {
  try {
    const hit = cached("institutional-filings", "_");
    if (hit) { res.json(hit); return; }
    const data = await runPython("get_institutional_filings.py", {});
    setCache("institutional-filings", "_", data);
    res.json(data);
  } catch (err) {
    logger.error({ err }, "Failed: /institutional-filings");
    res.status(500).json({ error: "Failed to fetch institutional filings" });
  }
});

interface MarketAlertItem {
  ticker: string;
  category: string;
  severity: "info" | "atencao" | "critico";
  title: string;
  detail: string;
  value: number | null;
  timestamp: string;
}

interface MarketAlertsPayload {
  total: number;
  criticalCount: number;
  alerts: MarketAlertItem[];
}

const SEVERITY_ORDER: Record<string, number> = { critico: 0, atencao: 1, info: 2 };
// Janela de exibição dos picos intraday persistidos (alert-checker.ts insere
// a cada 5min, cooldown de dedup de 15min) -- 45min cobre alguns ciclos de
// poll mesmo se algum for perdido, sem deixar picos velhos/irrelevantes
// acumulando no card indefinidamente.
const INTRADAY_SPIKE_WINDOW_MS = 45 * 60_000;

// Mescla os picos intraday (candle de 1min, persistidos pelo poller de
// background em alert-checker.ts) nos alertas de market_alerts.py (que são
// computados sob demanda a cada request, sem estado) -- sem isso, um pico
// que aconteceu entre duas visitas ao Dashboard nunca apareceria.
async function mergeIntradaySpikes(base: MarketAlertsPayload): Promise<MarketAlertsPayload> {
  let rows: IntradaySpike[] = [];
  try {
    rows = await db
      .select()
      .from(intradaySpikesTable)
      .where(gte(intradaySpikesTable.firedAt, new Date(Date.now() - INTRADAY_SPIKE_WINDOW_MS)))
      .orderBy(desc(intradaySpikesTable.firedAt));
  } catch (err) {
    logger.warn({ err }, "market-alerts: failed to load intraday spikes");
    return base;
  }
  if (!rows.length) return base;

  const spikeAlerts: MarketAlertItem[] = rows.map((r) => ({
    ticker: r.ticker,
    category: "tecnico",
    severity: r.severity as MarketAlertItem["severity"],
    title: r.title,
    detail: r.detail,
    value: r.value,
    timestamp: r.firedAt.toISOString(),
  }));

  const alerts = [...base.alerts, ...spikeAlerts].sort(
    (a, b) => (SEVERITY_ORDER[a.severity] ?? 3) - (SEVERITY_ORDER[b.severity] ?? 3),
  );

  return {
    total: alerts.length,
    criticalCount: alerts.filter((a) => a.severity === "critico").length,
    alerts,
  };
}

// Snapshot ao vivo dos alertas de market_alerts.py (setor, macro, técnico,
// geopolítico -- inclui as categorias de risco macro: petróleo, Taiwan,
// Irã/Ormuz, Coreia do Norte, independência do Fed, rating soberano) pro
// card "Alertas de Mercado" do Dashboard. NÃO passa pelo loop do agente/LLM
// -- é o mesmo check_market_alerts que o agente chama, só que direto via
// HTTP, sem custo de token. Também mescla os picos intraday de volume/preço
// detectados em background (ver mergeIntradaySpikes acima).
router.get("/market-alerts", async (_req, res): Promise<void> => {
  try {
    const tickers = await resolveTickers(String(_req.query.tickers ?? ""));
    const key = tickers.join(",");
    let data = cached("market-alerts", key) as MarketAlertsPayload | null;
    if (!data) {
      data = (await runMarketAlertsSnapshot({ tickers })) as MarketAlertsPayload;
      setCache("market-alerts", key, data);
    }
    res.json(await mergeIntradaySpikes(data));
  } catch (err) {
    logger.error({ err }, "Failed: /market-alerts");
    res.status(500).json({ error: "Failed to fetch market alerts" });
  }
});

export default router;
