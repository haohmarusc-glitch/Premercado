import { db, macroRiskSnapshotsTable } from "@workspace/db";
import { getPythonBin, agentDir } from "./runner";
import { spawnPython } from "./python-spawn";
import { comVagaPython } from "./vaga-python";
import { logger } from "./logger";

// A coleta bate em FRED (3 séries), yfinance (3 tickers + earnings) e no feed
// de notícias. 90s é folgado de propósito: o teto externo tem que ser MAIOR que
// a soma dos internos (playbook §3), e aqui não há orçamento interno -- cada
// fetch tem timeout curto e falha isolada.
export const TIMEOUT_MS = 90_000;

export type RetratoMacro = {
  snapshotDate?: string;
  aggregate_score?: number | null;
  cobertura_pct?: number;
  fontesDegradadas?: Record<string, string>;
  evaluated_at?: string;
  error?: string;
  [k: string]: unknown;
};

export const NOMES_DOS_SINAIS = [
  "RATE_SHOCK", "ASIA_MEMORY_CONTAGION", "PRICED_FOR_PERFECTION",
  "CHINA_COMPETITION_RISK", "OVEREXTENDED_SECTOR", "GEOPOLITICAL_OIL_SHOCK",
];

export function flagsAtivos(r: RetratoMacro): string[] {
  return NOMES_DOS_SINAIS.filter((n) => {
    const s = r[n] as { active?: boolean } | undefined;
    return Boolean(s?.active);
  });
}

/** Roda o coletor Python e devolve o retrato do dia. */
export function coletarMacroRisk(): Promise<RetratoMacro> {
  return comVagaPython("macro-risk", () => new Promise((resolve, reject) => {
    // `-m agent.xxx`, NÃO caminho direto do script.
    //
    // O coletor usa market_alerts (Kospi), tools (notícias) e yfinance via
    // pacote, e esses módulos fazem `from .cache import cached` -- import
    // relativo que só resolve em contexto de pacote. Rodar por caminho põe o
    // diretório agent/ no sys.path, onde existe um agent.py que SOMBREIA o
    // pacote agent/: `from agent import market_alerts` passa a procurar um
    // atributo dentro do módulo errado.
    //
    // Medido em produção 19/08/2026, na primeira coleta pela rota:
    //
    //   "^KS11":    "attempted relative import with no known parent package"
    //   "earnings":  idem
    //   "noticias":  idem
    //
    // Três das seis fontes caíram, e em silêncio: a coleta isola falha por
    // bloco, então o retrato saiu com cobertura 90% e a Ásia avaliada só pelas
    // ações. A verificação por linha de comando não pegou porque eu a rodei
    // com `-m`, que é justamente o modo que funciona.
    //
    // Mesmo padrão de analysis.ts (analise_rapida_ia), quotes.ts e chart.ts.
    const py = spawnPython(getPythonBin(), ["-m", "agent.macro_risk_snapshot"], {
      cwd: agentDir,
      env: { ...process.env, PYTHONPATH: agentDir },
    });
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
        resolve(JSON.parse(out) as RetratoMacro);
      } catch {
        // O stdout vira parte do erro: sem isso, "Parse error" manda o operador
        // rodar o script à mão dentro do container pra descobrir o que veio --
        // foi o que custou horas no NaN de 18/08 (ver technicals.ts).
        reject(new Error(`resposta ilegível. stdout: ${out.slice(0, 300)}`));
      }
    });
  }));
}

/**
 * Grava o retrato do dia. Upsert por data: reavaliar no mesmo pregão
 * SOBRESCREVE em vez de duplicar -- a série é "um retrato por dia", e duas
 * linhas com a mesma data fariam o gráfico contar o dia duas vezes.
 */
export async function persistirMacroRisk(r: RetratoMacro): Promise<void> {
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

/**
 * Coleta e grava — o que o agendador diário faz, e o que a rota faz antes de
 * responder. Existe para os dois NÃO terem cópias divergentes da sequência:
 * era exatamente assim que a ordem de provedores passou a divergir entre
 * provider.py e agent-budget.ts (ver o teste de sincronia lá).
 *
 * Nunca levanta: o agendador roda sem ninguém olhando, e uma exceção não
 * tratada num cron do node-cron derruba o task silenciosamente até o próximo
 * boot. Devolve o retrato quando dá certo, null quando não.
 */
export async function coletarEPersistir(origem: string): Promise<RetratoMacro | null> {
  try {
    const retrato = await coletarMacroRisk();
    if (retrato.error) {
      logger.error({ origem, erro: retrato.error }, "macro-risk: coletor devolveu erro");
      return null;
    }
    await persistirMacroRisk(retrato);
    logger.info({
      origem,
      score: retrato.aggregate_score,
      cobertura: retrato.cobertura_pct,
      flags: flagsAtivos(retrato),
      degradadas: Object.keys(retrato.fontesDegradadas ?? {}),
    }, "macro-risk: retrato do dia gravado");
    return retrato;
  } catch (err) {
    logger.error({ err, origem }, "macro-risk: falha na coleta diária");
    return null;
  }
}
