import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { FlaskConical, Layers } from "lucide-react";
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from "recharts";
import { Badge } from "@/components/ui/badge";
import { ExportarRelatorio, cabecalho, itens, tabela, pct } from "@/components/exportar-relatorio";

interface Trade {
  entryDate: string;
  exitDate: string;
  entryPrice: number;
  exitPrice: number;
  pnl: number;
  win: boolean;
  closedOpen: boolean;
  exitReason?: "signal" | "stop_loss" | "take_profit" | "period_end";
}

interface EquityPoint {
  date: string;
  equity: number;
  buyHoldEquity: number;
}

/** IC de 95% por bootstrap dos trades (semente fixa no Python — o mesmo
 * histórico produz o mesmo intervalo, requisito para ser auditável). Vem só
 * com `aviso` quando a amostra de trades não sustenta intervalo nenhum. */
interface BootstrapResumo {
  aviso?: string;
  nTrades?: number;
  amostras?: number;
  compostoIc95?: [number, number];
  winRateIc95?: [number, number];
}

interface BacktestResult {
  ticker: string;
  strategy: string;
  start: string;
  end: string;
  initialCapital: number;
  finalValue: number;
  totalReturn: number;
  buyAndHoldReturn: number;
  cagr: number;
  sharpe: number;
  maxDrawdown: number;
  // Métricas de auditoria (20/08/2026). Opcionais e anuláveis: resultado de
  // cesta antigo ou payload de versão anterior não as tem, e `null` é como o
  // Python declara "não computável" (ex.: profit factor sem perdas).
  sortino?: number | null;
  calmar?: number | null;
  profitFactor?: number | null;
  expectancy?: number | null;
  payoff?: number | null;
  bootstrap?: BootstrapResumo | null;
  totalTrades: number;
  winRate: number;
  avgWin: number;
  avgLoss: number;
  trades: Trade[];
  equityCurve: EquityPoint[];
  error?: string;
}

/** "—" para métrica não computável — a régua declara ausência, não inventa. */
function fmtMetrica(v: number | null | undefined, casas = 2): string {
  return v == null || !Number.isFinite(v) ? "—" : v.toFixed(casas);
}

const EXIT_REASON_LABEL: Record<string, string> = {
  signal: "sinal",
  stop_loss: "stop loss",
  take_profit: "take profit",
  period_end: "fim período",
};

interface SensitivityRun {
  param?: string;
  value?: number;
  totalReturn: number;
  buyAndHoldReturn: number;
  cagr: number;
  sharpe: number;
  maxDrawdown: number;
  totalTrades: number;
  winRate: number;
  error?: string;
}

interface SensitivityResult {
  ticker: string;
  strategy: string;
  start: string;
  end: string;
  baseline: SensitivityRun;
  variations: SensitivityRun[];
  error?: string;
}

// Percentual que pode vir null (janela sem resultado) -- "—" em vez de
// "0.00%", que seria lido como "deu zero" quando o caso é "não há número".
function fmtPctNum(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "—";
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

interface WalkForwardFold {
  treinoInicio: string;
  treinoFim: string;
  testeInicio: string;
  testeFim: string;
  melhorParams?: Record<string, number>;
  inSample?: SensitivityRun;
  outOfSample?: SensitivityRun & { error?: string };
  semSinalNoTreino?: boolean;
}

interface WalkForwardResult {
  ticker: string;
  strategy: string;
  objetivo: string;
  treinoPregoes: number;
  testePregoes: number;
  /** Pregões descartados entre treino e teste (embargo anti-vazamento,
   * 20/08/2026). Ausente em payload de versão anterior. */
  embargoPregoes?: number;
  combinacoesTestadas: number;
  folds: WalkForwardFold[];
  resumo: {
    nFolds: number;
    aviso?: string;
    retornoMedioInSample?: number | null;
    retornoMedioOutOfSample?: number | null;
    degradacao?: number | null;
    sharpeMedioOutOfSample?: number | null;
    maxDrawdownMedioOutOfSample?: number | null;
    buyAndHoldMedioOutOfSample?: number | null;
    foldsPositivos?: number;
    foldsQueVenceramBuyHold?: number;
    parametrosDistintosEscolhidos?: number;
    parametroEstavel?: boolean;
    foldsSemSinalNoTreino?: number;
  };
  error?: string;
}

const SENSITIVITY_PARAM_LABEL: Record<string, string> = {
  rsiOversold: "RSI Sobrevendido",
  rsiOverbought: "RSI Sobrecomprado",
  scoreThreshold: "Threshold do Score",
  stopLossPct: "Stop Loss",
  takeProfitPct: "Take Profit",
};

function formatSensitivityValue(param: string, value: number): string {
  if (param === "stopLossPct" || param === "takeProfitPct") return `${(value * 100).toFixed(0)}%`;
  return String(value);
}

interface SectorAggregate {
  sector: string;
  label: string;
  tickerCount: number;
  avgTotalReturn: number;
  avgBuyAndHoldReturn: number;
  avgWinRate: number;
  totalTrades: number;
  beatBuyAndHoldCount: number;
}

interface BasketResult {
  strategy: string;
  start: string;
  end: string;
  tickersRequested: number;
  tickersOk: number;
  aggregate?: {
    avgTotalReturn: number;
    avgBuyAndHoldReturn: number;
    avgWinRate: number;
    totalTrades: number;
    beatBuyAndHoldCount: number;
  };
  bySector?: SectorAggregate[];
  results: BacktestResult[];
  failed: { ticker: string; error: string }[];
  error?: string;
}

const today = new Date().toISOString().split("T")[0];
const oneYearAgo = new Date(Date.now() - 365 * 86400000).toISOString().split("T")[0];
const sixMonthsAgo = new Date(Date.now() - 182 * 86400000).toISOString().split("T")[0];

// Interpretação em texto puro, calculada em cima dos mesmos campos que o
// backtest já retorna -- sem chamada de LLM, mesmo princípio já usado na
// análise de reação a earnings (ver earnings-reaction.tsx).
function interpretTickerResult(result: BacktestResult): string[] {
  const notes: string[] = [];
  const diff = result.totalReturn - result.buyAndHoldReturn;

  if (Math.abs(diff) < 1) {
    notes.push(
      `A estratégia ficou praticamente empatada com o buy & hold (diferença de ${diff >= 0 ? "+" : ""}${diff.toFixed(2)}pp) — nesse período, comprar e segurar teria dado o mesmo resultado sem o trabalho de operar.`,
    );
  } else if (diff > 0) {
    notes.push(
      `A estratégia superou o buy & hold em ${diff.toFixed(2)}pp (${result.totalReturn.toFixed(2)}% vs ${result.buyAndHoldReturn.toFixed(2)}%) nesse período específico.`,
    );
  } else {
    notes.push(
      `A estratégia ficou ${Math.abs(diff).toFixed(2)}pp ATRÁS do buy & hold (${result.totalReturn.toFixed(2)}% vs ${result.buyAndHoldReturn.toFixed(2)}%) — nesse período, só comprar e segurar teria performado melhor.`,
    );
  }

  if (result.sharpe >= 1) {
    notes.push(`Sharpe de ${result.sharpe.toFixed(2)}: retorno bom em relação à volatilidade assumida.`);
  } else if (result.sharpe >= 0) {
    notes.push(`Sharpe de ${result.sharpe.toFixed(2)}: retorno positivo, mas mediano frente ao risco — a volatilidade do caminho consumiu boa parte do ganho.`);
  } else {
    notes.push(`Sharpe negativo (${result.sharpe.toFixed(2)}): o risco assumido não foi compensado pelo retorno nesse período.`);
  }

  if (result.maxDrawdown <= -25) {
    notes.push(`Drawdown máximo de ${result.maxDrawdown.toFixed(2)}% é severo — exige tolerância alta a perda temporária de capital pra manter a estratégia até a recuperação.`);
  } else if (result.maxDrawdown <= -15) {
    notes.push(`Drawdown máximo de ${result.maxDrawdown.toFixed(2)}% é moderado — vale considerar o tamanho de posição em relação a isso.`);
  }

  if (result.totalTrades > 0) {
    if (result.winRate < 50 && result.avgWin > Math.abs(result.avgLoss) * 1.3) {
      notes.push(
        `Win rate de ${result.winRate}% é baixo, mas a média de ganho (+${result.avgWin.toFixed(2)}%) é bem maior que a média de perda (${result.avgLoss.toFixed(2)}%) — a estratégia pode ter expectativa positiva mesmo perdendo mais vezes do que ganha.`,
      );
    } else if (result.winRate >= 50 && result.avgWin < Math.abs(result.avgLoss)) {
      notes.push(
        `Apesar do win rate de ${result.winRate}% acima de 50%, a média de perda (${result.avgLoss.toFixed(2)}%) é maior que a média de ganho (+${result.avgWin.toFixed(2)}%) — vale revisar o stop/take profit.`,
      );
    }

    const stopLossExits = result.trades.filter((t) => t.exitReason === "stop_loss").length;
    if (stopLossExits > 0 && stopLossExits / result.totalTrades >= 0.4) {
      notes.push(
        `${stopLossExits} de ${result.totalTrades} operações (${Math.round((stopLossExits / result.totalTrades) * 100)}%) saíram via stop loss — o stop pode estar apertado demais pra volatilidade do ativo, ou a estratégia está entrando contra a tendência com frequência.`,
      );
    }

    if (result.totalTrades < 5) {
      notes.push(`Amostra pequena (${result.totalTrades} operação${result.totalTrades === 1 ? "" : "ões"}) — os números acima têm baixa significância estatística; considere um período mais longo.`);
    }

    // A pergunta que o retorno sozinho não responde: esse número sobrevive a
    // reembaralhar os próprios trades? IC cruzando o zero = indistinguível de
    // sorte de sequência COM ESTA amostra — não prova que não há edge, prova
    // que esta amostra não o demonstra.
    const ic = result.bootstrap?.compostoIc95;
    if (ic && ic[0] <= 0 && ic[1] >= 0) {
      notes.push(`O IC de 95% do composto dos trades (${ic[0]}% a ${ic[1]}%, bootstrap) cruza o zero — com essa amostra, o resultado não se distingue de sorte de sequência.`);
    } else if (ic && ic[1] < 0) {
      notes.push(`O IC de 95% do composto dos trades (${ic[0]}% a ${ic[1]}%, bootstrap) é inteiro negativo — a perda não é azar de sequência; é o sinal.`);
    }

    const lastTrade = result.trades[result.trades.length - 1];
    if (lastTrade?.closedOpen) {
      notes.push(`A última operação segue aberta no fim do período — o P&L dela é uma marcação a mercado, não um resultado realizado.`);
    }
  } else {
    notes.push(`Nenhuma operação foi executada nesse período com esses parâmetros — a estratégia nunca disparou um sinal de entrada.`);
  }

  return notes;
}

function interpretBasketResult(basketResult: BasketResult): string[] {
  const notes: string[] = [];
  const agg = basketResult.aggregate;
  if (!agg) return notes;

  const diff = agg.avgTotalReturn - agg.avgBuyAndHoldReturn;
  if (Math.abs(diff) < 1) {
    notes.push(`Em média, a estratégia ficou praticamente empatada com o buy & hold na cesta (diferença de ${diff >= 0 ? "+" : ""}${diff.toFixed(2)}pp).`);
  } else if (diff > 0) {
    notes.push(`Em média, a estratégia superou o buy & hold em ${diff.toFixed(2)}pp na cesta (${agg.avgTotalReturn.toFixed(2)}% vs ${agg.avgBuyAndHoldReturn.toFixed(2)}%).`);
  } else {
    notes.push(`Em média, a estratégia ficou ${Math.abs(diff).toFixed(2)}pp atrás do buy & hold na cesta — nesse período, o conjunto de ativos performou melhor sem operar.`);
  }

  const beatFraction = basketResult.tickersOk > 0 ? agg.beatBuyAndHoldCount / basketResult.tickersOk : 0;
  notes.push(
    `A estratégia bateu o buy & hold em ${agg.beatBuyAndHoldCount} de ${basketResult.tickersOk} tickers (${Math.round(beatFraction * 100)}%)` +
    (beatFraction < 0.4 ? " — resultado concentrado em poucos ativos, não um padrão consistente na cesta." : beatFraction > 0.6 ? " — padrão consistente na maioria dos ativos, não só em outliers." : "."),
  );

  const sorted = [...basketResult.results].filter((r) => !r.error).sort((a, b) => b.totalReturn - a.totalReturn);
  if (sorted.length > 1) {
    const best = sorted[0];
    const worst = sorted[sorted.length - 1];
    notes.push(
      `Melhor resultado: ${best.ticker} (${best.totalReturn >= 0 ? "+" : ""}${best.totalReturn.toFixed(2)}%). Pior resultado: ${worst.ticker} (${worst.totalReturn >= 0 ? "+" : ""}${worst.totalReturn.toFixed(2)}%) — a dispersão entre os ativos mostra o quanto o resultado agregado depende de poucos nomes.`,
    );
  }

  if (basketResult.failed.length > 0) {
    const n = basketResult.failed.length;
    notes.push(`${n} ticker${n === 1 ? "" : "s"} ${n === 1 ? "ficou" : "ficaram"} de fora por falta de dados suficientes no período — o agregado reflete só os ${basketResult.tickersOk} que rodaram.`);
  }

  return notes;
}

// Pregões aproximados entre duas datas: ~252 dias úteis por ano civil. Serve só
// pra avisar antes de rodar — quem decide de verdade é o Python, contando as
// linhas que o yfinance devolveu (feriado, IPO recente, ticker novo).
export function pregoesAproximados(start: string, end: string): number | null {
  const a = new Date(start).getTime();
  const b = new Date(end).getTime();
  if (!Number.isFinite(a) || !Number.isFinite(b) || b <= a) return null;
  const dias = (b - a) / 86_400_000;
  return Math.floor((dias * 252) / 365.25);
}

export default function BacktestPage() {
  const [mode, setMode] = useState<"ticker" | "basket">("ticker");
  const [ticker, setTicker] = useState("NVDA");
  const [start, setStart] = useState(oneYearAgo);
  const [end, setEnd] = useState(today);
  const [strategy, setStrategy] = useState("rsi");
  const [positionFraction, setPositionFraction] = useState("1.0");
  const [commissionPct, setCommissionPct] = useState("0.001");
  const [slippagePct, setSlippagePct] = useState("0.0005");
  const [stopLossPct, setStopLossPct] = useState("");
  const [takeProfitPct, setTakeProfitPct] = useState("");
  const [rsiOversold, setRsiOversold] = useState("30");
  const [rsiOverbought, setRsiOverbought] = useState("70");
  const [scoreThreshold, setScoreThreshold] = useState("60");
  const [treinoPregoes, setTreinoPregoes] = useState("252");
  const [testePregoes, setTestePregoes] = useState("63");
  const [objetivo, setObjetivo] = useState("sharpe");
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [basketResult, setBasketResult] = useState<BasketResult | null>(null);
  const [sensitivityResult, setSensitivityResult] = useState<SensitivityResult | null>(null);
  const [walkForwardResult, setWalkForwardResult] = useState<WalkForwardResult | null>(null);

  function switchToBasket() {
    setMode("basket");
    setStrategy("confluencia");
    setStart(sixMonthsAgo);
  }
  function switchToTicker() {
    setMode("ticker");
    setStrategy("rsi");
    setStart(oneYearAgo);
  }

  function riskParams() {
    return {
      positionFraction: parseFloat(positionFraction),
      commissionPct: parseFloat(commissionPct),
      slippagePct: parseFloat(slippagePct),
      stopLossPct: stopLossPct ? parseFloat(stopLossPct) / 100 : undefined,
      takeProfitPct: takeProfitPct ? parseFloat(takeProfitPct) / 100 : undefined,
      rsiOversold: parseFloat(rsiOversold),
      rsiOverbought: parseFloat(rsiOverbought),
      scoreThreshold: parseFloat(scoreThreshold),
    };
  }

  // O backend faz o clamp final (60–1260 / 20–504); aqui só espelhamos os
  // limites pra que o aviso na tela case com o que o Python vai contar.
  const treinoNum = Math.min(1260, Math.max(60, parseInt(treinoPregoes, 10) || 252));
  const testeNum = Math.min(504, Math.max(20, parseInt(testePregoes, 10) || 63));
  const minimoPregoes = treinoNum + testeNum;
  const pregoesNoPeriodo = pregoesAproximados(start, end);
  const foldsEstimados = pregoesNoPeriodo === null
    ? 0
    : Math.max(0, Math.floor((pregoesNoPeriodo - treinoNum) / testeNum));
  const avisoJanela = pregoesNoPeriodo !== null && pregoesNoPeriodo < minimoPregoes
    ? `O período escolhido rende ~${pregoesNoPeriodo} pregões, abaixo dos ${minimoPregoes} que treino + teste exigem. Estenda a data de início ou reduza as janelas.`
    : null;

  // Monta o relatório com o que estiver na tela — backtest, sensibilidade e
  // walk-forward são independentes, e é comum rodar só um deles. A leitura
  // interpretada (interpretTickerResult) vai junto: sem ela o relatório é uma
  // planilha de números sem a conclusão que a tela já sabe tirar.
  function montarRelatorioBacktest(): string | null {
    const blocos: string[] = [];
    const alvo = mode === "basket" ? "cesta" : ticker.toUpperCase();
    blocos.push(cabecalho(
      `Backtest — ${alvo}`,
      `Estratégia ${strategy.toUpperCase()} · período ${start} a ${end}`,
    ));

    if (mode === "ticker" && result && !result.error) {
      blocos.push("## Resultado\n\n" + itens([
        ["Retorno total", pct(result.totalReturn)],
        ["Buy & hold", pct(result.buyAndHoldReturn)],
        ["Diferença", `${(result.totalReturn - result.buyAndHoldReturn).toFixed(2)}pp`],
        ["CAGR", pct(result.cagr)],
        ["Sharpe", result.sharpe.toFixed(2)],
        ["Sortino", fmtMetrica(result.sortino)],
        ["Calmar", fmtMetrica(result.calmar)],
        ["Profit factor", fmtMetrica(result.profitFactor)],
        ["Expectancy por trade", result.expectancy == null ? "—" : pct(result.expectancy)],
        ["Drawdown máximo", pct(result.maxDrawdown)],
        ["Operações", result.totalTrades],
        // winRate já chega em % do Python; multiplicar por 100 de novo
        // exportava "+5500.0%" (bug até 20/08/2026).
        ["Taxa de acerto", pct(result.winRate, 1)],
        ["IC 95% do composto (bootstrap)",
         result.bootstrap?.compostoIc95
           ? `${result.bootstrap.compostoIc95[0]}% a ${result.bootstrap.compostoIc95[1]}%`
           : (result.bootstrap?.aviso ?? "—")],
      ]));
      if (result.trades.length) {
        blocos.push("### Operações\n\n" + tabela(
          ["Entrada", "Saída", "Preço entrada", "Preço saída", "P&L", "Motivo"],
          result.trades.map((t) => [
            t.entryDate, t.exitDate,
            `$${t.entryPrice.toFixed(2)}`, `$${t.exitPrice.toFixed(2)}`,
            pct(t.pnl),
            (EXIT_REASON_LABEL[t.exitReason ?? ""] ?? "—") + (t.closedOpen ? " (aberta)" : ""),
          ]),
        ));
      }
      blocos.push("## Leitura\n\n" + interpretTickerResult(result).map((n) => `- ${n}`).join("\n"));
    }

    if (mode === "basket" && basketResult) {
      blocos.push("## Cesta\n\n" + tabela(
        ["Ticker", "Retorno", "Buy & hold", "Sharpe", "Operações"],
        basketResult.results.filter((r) => !r.error).map((r) => [
          r.ticker, pct(r.totalReturn), pct(r.buyAndHoldReturn), r.sharpe.toFixed(2), r.totalTrades,
        ]),
      ));
      blocos.push("## Leitura\n\n" + interpretBasketResult(basketResult).map((n) => `- ${n}`).join("\n"));
    }

    if (sensitivityResult && !sensitivityResult.error) {
      blocos.push("## Sensibilidade\n\n" + tabela(
        ["Parâmetro", "Valor", "Retorno", "Sharpe", "Drawdown", "Operações"],
        sensitivityResult.variations.map((v) => [
          v.param ?? "—", v.value ?? "—", pct(v.totalReturn), v.sharpe.toFixed(2),
          pct(v.maxDrawdown), v.totalTrades,
        ]),
      ));
    }

    if (walkForwardResult && !walkForwardResult.error) {
      const r = walkForwardResult.resumo;
      blocos.push("## Walk-forward (out-of-sample)\n\n" + itens([
        ["Janelas", r.nFolds],
        ["Treino / teste (pregões)", `${walkForwardResult.treinoPregoes} / ${walkForwardResult.testePregoes}`
          + (walkForwardResult.embargoPregoes != null ? ` (embargo ${walkForwardResult.embargoPregoes})` : "")],
        ["Objetivo", walkForwardResult.objetivo],
        ["Retorno médio in-sample", fmtPctNum(r.retornoMedioInSample)],
        ["Retorno médio out-of-sample", fmtPctNum(r.retornoMedioOutOfSample)],
        ["Degradação", fmtPctNum(r.degradacao)],
        ["Buy & hold out-of-sample", fmtPctNum(r.buyAndHoldMedioOutOfSample)],
        ["Janelas positivas", `${r.foldsPositivos ?? 0} de ${r.nFolds}`],
        ["Bateram o buy & hold", `${r.foldsQueVenceramBuyHold ?? 0} de ${r.nFolds}`],
        ["Parâmetro estável", r.parametroEstavel ? "sim" : `não (${r.parametrosDistintosEscolhidos} conjuntos venceram)`],
      ]));
    }

    // Só o cabeçalho significa que nenhuma análise rodou ainda.
    return blocos.length > 1 ? blocos.join("\n\n") : null;
  }

  const temResultadoParaExportar = Boolean(
    (mode === "ticker" && result && !result.error) ||
    (mode === "basket" && basketResult) ||
    (sensitivityResult && !sensitivityResult.error) ||
    (walkForwardResult && !walkForwardResult.error),
  );

  const run = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/backtest", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: ticker.toUpperCase(), start, end, strategy, ...riskParams() }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed");
      return data as BacktestResult;
    },
    onSuccess: (data) => setResult(data),
  });

  const runBasket = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/backtest/basket", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ start, end, strategy, ...riskParams() }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed");
      return data as BasketResult;
    },
    onSuccess: (data) => setBasketResult(data),
  });

  const runSensitivity = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/backtest/sensitivity", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: ticker.toUpperCase(), start, end, strategy, ...riskParams() }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed");
      return data as SensitivityResult;
    },
    onSuccess: (data) => setSensitivityResult(data),
  });

  const runWalkForward = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/backtest/walk-forward", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker: ticker.toUpperCase(), start, end, strategy, ...riskParams(),
          treinoPregoes: parseInt(treinoPregoes, 10),
          testePregoes: parseInt(testePregoes, 10),
          objetivo,
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed");
      return data as WalkForwardResult;
    },
    onSuccess: (data) => setWalkForwardResult(data),
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
          <FlaskConical className="h-7 w-7 text-primary" /> BACKTESTING
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">Simular estratégias em dados históricos</p>
      </div>

      {/* Form */}
      <div className="border border-border rounded-lg bg-card p-5 space-y-4">
        <div className="flex items-center justify-between">
          <p className="text-xs font-mono text-muted-foreground uppercase tracking-widest">Parâmetros</p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={switchToTicker}
              className={`px-3 py-1.5 rounded border font-mono text-xs transition-colors ${
                mode === "ticker" ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:border-primary/50"
              }`}
            >
              Ticker único
            </button>
            <button
              type="button"
              onClick={switchToBasket}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded border font-mono text-xs transition-colors ${
                mode === "basket" ? "bg-primary text-primary-foreground border-primary" : "border-border text-muted-foreground hover:border-primary/50"
              }`}
            >
              <Layers className="h-3 w-3" /> Cesta inteira
            </button>
          </div>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {mode === "ticker" && (
            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-muted-foreground uppercase">Ticker</label>
              <input
                type="text"
                value={ticker}
                onChange={(e) => setTicker(e.target.value.toUpperCase())}
                className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
          )}
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Data Início</label>
            <input
              type="date"
              value={start}
              onChange={(e) => setStart(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Data Fim</label>
            <input
              type="date"
              value={end}
              onChange={(e) => setEnd(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Estratégia</label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            >
              <option value="rsi">RSI (30/70)</option>
              <option value="ma_cross">MA Cross (20/50)</option>
              <option value="confluencia">Confluência (técnico, sem notícias)</option>
            </select>
          </div>
        </div>
        {strategy === "confluencia" && (
          <p className="text-[11px] font-mono text-muted-foreground border border-dashed border-border rounded px-3 py-2">
            Reproduz o score técnico do sinal (SMA20×50, preço×SMA200, estrutura, MACD, RSI) sem a camada de notícias
            — não dá pra reconstruir com fidelidade o que era manchete em cada dia do passado. Compra/venda nos
            thresholds do score configurados abaixo (padrão ±60).
          </p>
        )}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          {[
            { label: "Fração Posição (0.1–1.0)", val: positionFraction, set: setPositionFraction, step: "0.1" },
            { label: "Comissão (ex: 0.001 = 0.1%)", val: commissionPct, set: setCommissionPct, step: "0.0001" },
            { label: "Slippage (ex: 0.0005)", val: slippagePct, set: setSlippagePct, step: "0.0001" },
          ].map(({ label, val, set, step }) => (
            <div key={label} className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-muted-foreground uppercase">{label}</label>
              <input
                type="number" step={step} value={val}
                onChange={(e) => set(e.target.value)}
                className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
          ))}
        </div>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 border-t border-border/40 pt-4">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Stop Loss % (opcional)</label>
            <input
              type="number" step="0.5" min="0" placeholder="ex: 8"
              value={stopLossPct}
              onChange={(e) => setStopLossPct(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Take Profit % (opcional)</label>
            <input
              type="number" step="0.5" min="0" placeholder="ex: 15"
              value={takeProfitPct}
              onChange={(e) => setTakeProfitPct(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          {strategy === "rsi" && (
            <>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-mono text-muted-foreground uppercase">RSI Sobrevendido</label>
                <input
                  type="number" step="1" min="1" max="49" value={rsiOversold}
                  onChange={(e) => setRsiOversold(e.target.value)}
                  className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-mono text-muted-foreground uppercase">RSI Sobrecomprado</label>
                <input
                  type="number" step="1" min="51" max="99" value={rsiOverbought}
                  onChange={(e) => setRsiOverbought(e.target.value)}
                  className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
            </>
          )}
          {strategy === "confluencia" && (
            <div className="flex flex-col gap-1">
              <label className="text-[10px] font-mono text-muted-foreground uppercase">Threshold do Score</label>
              <input
                type="number" step="5" min="5" max="100" value={scoreThreshold}
                onChange={(e) => setScoreThreshold(e.target.value)}
                className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
              />
            </div>
          )}
        </div>
        {mode === "ticker" && (
          <div className="border-t border-border/40 pt-4 space-y-2">
            <p className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest">Janela do walk-forward</p>
            <div className="grid grid-cols-2 md:grid-cols-3 gap-4">
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-mono text-muted-foreground uppercase">Treino (pregões, 60–1260)</label>
                <input
                  type="number" step="21" min="60" max="1260" value={treinoPregoes}
                  onChange={(e) => setTreinoPregoes(e.target.value)}
                  className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-mono text-muted-foreground uppercase">Teste (pregões, 20–504)</label>
                <input
                  type="number" step="21" min="20" max="504" value={testePregoes}
                  onChange={(e) => setTestePregoes(e.target.value)}
                  className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-[10px] font-mono text-muted-foreground uppercase">Objetivo da otimização</label>
                <select
                  value={objetivo}
                  onChange={(e) => setObjetivo(e.target.value)}
                  className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
                >
                  <option value="sharpe">Sharpe</option>
                  <option value="totalReturn">Retorno total</option>
                  <option value="cagr">CAGR</option>
                </select>
              </div>
            </div>
            <p className={`text-[11px] font-mono ${avisoJanela ? "text-yellow-400" : "text-muted-foreground"}`}>
              {avisoJanela ?? `Precisa de ao menos ${minimoPregoes} pregões; o período escolhido tem ~${pregoesNoPeriodo} — dá para ~${foldsEstimados} janela(s) de teste. Só o walk-forward usa esses campos.`}
            </p>
          </div>
        )}
        <div className="flex flex-wrap gap-3">
          <button
            onClick={() => mode === "basket" ? runBasket.mutate() : run.mutate()}
            disabled={mode === "basket" ? runBasket.isPending : (run.isPending || !ticker.trim())}
            className="px-6 py-2 bg-primary text-primary-foreground rounded font-mono text-sm font-bold disabled:opacity-50 flex items-center gap-2"
          >
            {(mode === "basket" ? runBasket.isPending : run.isPending) ? (
              <>
                <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full" />
                Executando...
              </>
            ) : (
              <><FlaskConical className="h-4 w-4" /> Executar</>
            )}
          </button>
          {mode === "ticker" && (
            <button
              onClick={() => runSensitivity.mutate()}
              disabled={runSensitivity.isPending || !ticker.trim()}
              className="px-6 py-2 border border-border rounded font-mono text-sm font-bold text-foreground disabled:opacity-50 flex items-center gap-2 hover:border-primary/50"
            >
              {runSensitivity.isPending ? (
                <>
                  <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-border border-t-foreground rounded-full" />
                  Testando...
                </>
              ) : (
                "Análise de Sensibilidade"
              )}
            </button>
          )}
          {mode === "ticker" && (
            <button
              onClick={() => runWalkForward.mutate()}
              disabled={runWalkForward.isPending || !ticker.trim()}
              title="Escolhe o parâmetro numa janela de treino e mede na janela seguinte, que o otimizador não viu — diferente da sensibilidade, que varia parâmetro sobre o mesmo período em que mede"
              className="px-6 py-2 border border-border rounded font-mono text-sm font-bold text-foreground disabled:opacity-50 flex items-center gap-2 hover:border-primary/50"
            >
              {runWalkForward.isPending ? (
                <>
                  <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-border border-t-foreground rounded-full" />
                  Validando...
                </>
              ) : (
                "Walk-Forward (out-of-sample)"
              )}
            </button>
          )}
        </div>
        {mode === "ticker" && run.isError && (
          <p className="text-sm text-red-400 font-mono">{String(run.error)}</p>
        )}
        {mode === "basket" && runBasket.isError && (
          <p className="text-sm text-red-400 font-mono">{String(runBasket.error)}</p>
        )}
        {mode === "ticker" && runSensitivity.isError && (
          <p className="text-sm text-red-400 font-mono">{String(runSensitivity.error)}</p>
        )}
        {temResultadoParaExportar && (
          <div className="border-t border-border/40 pt-4">
            <ExportarRelatorio
              titulo={`Backtest ${mode === "basket" ? "cesta" : ticker.toUpperCase()}`}
              mode="tela_backtest"
              tickers={mode === "basket" ? [] : [ticker.toUpperCase()]}
              construir={montarRelatorioBacktest}
            />
          </div>
        )}
      </div>

      {/* Basket results */}
      {mode === "basket" && basketResult && (
        basketResult.error ? (
          <div className="p-6 border border-red-500/30 rounded-lg bg-red-500/5 font-mono text-red-400 text-sm">
            {basketResult.error}
          </div>
        ) : (
          <div className="space-y-4">
            {basketResult.aggregate && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {[
                  { label: "Retorno Médio", value: `${basketResult.aggregate.avgTotalReturn >= 0 ? "+" : ""}${basketResult.aggregate.avgTotalReturn.toFixed(2)}%`, color: basketResult.aggregate.avgTotalReturn >= 0 ? "text-green-400" : "text-red-400" },
                  { label: "Buy & Hold Médio", value: `${basketResult.aggregate.avgBuyAndHoldReturn >= 0 ? "+" : ""}${basketResult.aggregate.avgBuyAndHoldReturn.toFixed(2)}%`, color: basketResult.aggregate.avgBuyAndHoldReturn >= 0 ? "text-green-400" : "text-red-400" },
                  { label: "Win Rate Médio", value: `${basketResult.aggregate.avgWinRate}%`, color: basketResult.aggregate.avgWinRate > 50 ? "text-green-400" : "text-yellow-400" },
                  { label: "Bateu Buy&Hold", value: `${basketResult.aggregate.beatBuyAndHoldCount}/${basketResult.tickersOk}`, color: "text-foreground" },
                ].map(({ label, value, color }) => (
                  <div key={label} className="border border-border rounded-lg bg-card p-4">
                    <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">{label}</div>
                    <div className={`text-xl font-bold font-mono ${color}`}>{value}</div>
                  </div>
                ))}
              </div>
            )}

            <div className="border border-border rounded-lg bg-card p-3 font-mono text-xs text-muted-foreground flex gap-4 flex-wrap">
              <span>{basketResult.strategy.toUpperCase()}</span>
              <span>{basketResult.start} → {basketResult.end}</span>
              <span>{basketResult.tickersOk}/{basketResult.tickersRequested} tickers com dados suficientes</span>
            </div>

            <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
              <div className="px-4 py-2.5 border-b border-border bg-secondary/30 text-xs font-mono text-muted-foreground uppercase tracking-widest">
                Por ticker (ordenado por retorno)
              </div>
              <table className="w-full font-mono text-sm">
                <thead className="bg-secondary/20">
                  <tr>
                    <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Ticker</th>
                    <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Retorno</th>
                    <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Buy&Hold</th>
                    <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Trades</th>
                    <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Win Rate</th>
                    <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Max DD</th>
                  </tr>
                </thead>
                <tbody>
                  {basketResult.results.map((r, idx) => (
                    <tr key={r.ticker} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                      <td className="px-4 py-2.5 font-bold text-primary">{r.ticker}</td>
                      <td className={`px-4 py-2.5 text-right font-bold ${r.totalReturn >= 0 ? "text-green-400" : "text-red-400"}`}>
                        {r.totalReturn >= 0 ? "+" : ""}{r.totalReturn.toFixed(2)}%
                      </td>
                      <td className={`px-4 py-2.5 text-right ${r.buyAndHoldReturn >= 0 ? "text-green-400" : "text-red-400"}`}>
                        {r.buyAndHoldReturn >= 0 ? "+" : ""}{r.buyAndHoldReturn.toFixed(2)}%
                      </td>
                      <td className="px-4 py-2.5 text-right text-muted-foreground">{r.totalTrades}</td>
                      <td className="px-4 py-2.5 text-right text-muted-foreground">{r.totalTrades > 0 ? `${r.winRate}%` : "—"}</td>
                      <td className="px-4 py-2.5 text-right text-red-400">{r.maxDrawdown.toFixed(2)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {basketResult.bySector && basketResult.bySector.length > 0 && (
              <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
                <div className="px-4 py-2.5 border-b border-border bg-secondary/30 text-xs font-mono text-muted-foreground uppercase tracking-widest">
                  Por setor (ordenado por retorno médio)
                </div>
                <table className="w-full font-mono text-sm">
                  <thead className="bg-secondary/20">
                    <tr>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Setor</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Tickers</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Retorno Médio</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Buy&Hold Médio</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Trades</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Win Rate</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Bateu B&H</th>
                    </tr>
                  </thead>
                  <tbody>
                    {basketResult.bySector.map((s, idx) => (
                      <tr key={s.sector} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                        <td className="px-4 py-2.5 font-bold text-primary">{s.label}</td>
                        <td className="px-4 py-2.5 text-right text-muted-foreground">{s.tickerCount}</td>
                        <td className={`px-4 py-2.5 text-right font-bold ${s.avgTotalReturn >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {s.avgTotalReturn >= 0 ? "+" : ""}{s.avgTotalReturn.toFixed(2)}%
                        </td>
                        <td className={`px-4 py-2.5 text-right ${s.avgBuyAndHoldReturn >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {s.avgBuyAndHoldReturn >= 0 ? "+" : ""}{s.avgBuyAndHoldReturn.toFixed(2)}%
                        </td>
                        <td className="px-4 py-2.5 text-right text-muted-foreground">{s.totalTrades}</td>
                        <td className="px-4 py-2.5 text-right text-muted-foreground">{s.totalTrades > 0 ? `${s.avgWinRate}%` : "—"}</td>
                        <td className="px-4 py-2.5 text-right text-muted-foreground">{s.beatBuyAndHoldCount}/{s.tickerCount}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {basketResult.failed.length > 0 && (
              <p className="text-xs font-mono text-muted-foreground">
                Sem dados para: {basketResult.failed.map((f) => f.ticker).join(", ")}.
              </p>
            )}

            {(() => {
              const notes = interpretBasketResult(basketResult);
              if (notes.length === 0) return null;
              return (
                <div className="border border-border rounded-lg bg-card p-4">
                  <div className="text-[10px] font-mono text-muted-foreground uppercase mb-2">Interpretação</div>
                  <ul className="space-y-1.5">
                    {notes.map((note, idx) => (
                      <li key={idx} className="font-mono text-xs text-muted-foreground leading-relaxed flex gap-2">
                        <span className="text-primary shrink-0">›</span>
                        <span>{note}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })()}
          </div>
        )
      )}

      {/* Results */}
      {mode === "ticker" && result && (
        result.error ? (
          <div className="p-6 border border-red-500/30 rounded-lg bg-red-500/5 font-mono text-red-400 text-sm">
            {result.error}
          </div>
        ) : (
          <div className="space-y-4">
            {/* Summary cards */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                { label: "Retorno Total", value: `${result.totalReturn >= 0 ? "+" : ""}${result.totalReturn.toFixed(2)}%`, color: result.totalReturn >= 0 ? "text-green-400" : "text-red-400" },
                { label: "Buy & Hold", value: `${result.buyAndHoldReturn >= 0 ? "+" : ""}${result.buyAndHoldReturn.toFixed(2)}%`, color: result.buyAndHoldReturn >= 0 ? "text-green-400" : "text-red-400" },
                { label: "CAGR", value: `${result.cagr >= 0 ? "+" : ""}${result.cagr.toFixed(2)}%`, color: result.cagr >= 0 ? "text-green-400" : "text-red-400" },
                { label: "Sharpe Ratio", value: result.sharpe.toFixed(2), color: result.sharpe >= 1 ? "text-green-400" : result.sharpe >= 0 ? "text-yellow-400" : "text-red-400" },
              ].map(({ label, value, color }) => (
                <div key={label} className="border border-border rounded-lg bg-card p-4">
                  <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">{label}</div>
                  <div className={`text-xl font-bold font-mono ${color}`}>{value}</div>
                </div>
              ))}
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                { label: "Max Drawdown", value: `${result.maxDrawdown.toFixed(2)}%`, color: "text-red-400" },
                { label: "Win Rate", value: `${result.winRate}%`, color: result.winRate > 50 ? "text-green-400" : "text-yellow-400" },
                { label: "Média Ganho", value: `+${result.avgWin.toFixed(2)}%`, color: "text-green-400" },
                { label: "Média Perda", value: `${result.avgLoss.toFixed(2)}%`, color: "text-red-400" },
              ].map(({ label, value, color }) => (
                <div key={label} className="border border-border rounded-lg bg-card p-4">
                  <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">{label}</div>
                  <div className={`text-xl font-bold font-mono ${color}`}>{value}</div>
                </div>
              ))}
            </div>

            {/* Métricas de auditoria — presentes só em payload novo; "—" é
                métrica não computável (ex.: Sortino sem dia negativo). */}
            {result.sortino !== undefined && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                {[
                  { label: "Sortino", value: fmtMetrica(result.sortino), color: (result.sortino ?? 0) >= 1 ? "text-green-400" : "text-yellow-400" },
                  { label: "Calmar", value: fmtMetrica(result.calmar), color: (result.calmar ?? 0) >= 0 ? "text-green-400" : "text-red-400" },
                  { label: "Profit Factor", value: fmtMetrica(result.profitFactor), color: (result.profitFactor ?? 0) >= 1 ? "text-green-400" : "text-red-400" },
                  { label: "Expectancy", value: result.expectancy == null ? "—" : `${result.expectancy >= 0 ? "+" : ""}${result.expectancy.toFixed(2)}%`, color: (result.expectancy ?? 0) >= 0 ? "text-green-400" : "text-red-400" },
                ].map(({ label, value, color }) => (
                  <div key={label} className="border border-border rounded-lg bg-card p-4">
                    <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">{label}</div>
                    <div className={`text-xl font-bold font-mono ${value === "—" ? "text-muted-foreground" : color}`}>{value}</div>
                  </div>
                ))}
              </div>
            )}

            {result.bootstrap && (
              <div className="border border-border rounded-lg bg-card p-3 font-mono text-xs text-muted-foreground">
                {result.bootstrap.aviso ? (
                  <span>Bootstrap: {result.bootstrap.aviso}.</span>
                ) : (
                  <span>
                    IC 95% (bootstrap, {result.bootstrap.amostras} reamostras de {result.bootstrap.nTrades} trades):
                    {" "}composto {result.bootstrap.compostoIc95?.[0]}% a {result.bootstrap.compostoIc95?.[1]}%
                    {" "}· win rate {result.bootstrap.winRateIc95?.[0]}% a {result.bootstrap.winRateIc95?.[1]}%
                  </span>
                )}
              </div>
            )}

            <div className="border border-border rounded-lg bg-card p-3 font-mono text-xs text-muted-foreground flex gap-4 flex-wrap">
              <span>{result.ticker} · {result.strategy.toUpperCase()}</span>
              <span>{result.start} → {result.end}</span>
              <span>Capital inicial: $10,000</span>
              <span className={`font-bold ${result.finalValue >= 10000 ? "text-green-400" : "text-red-400"}`}>Final: ${result.finalValue.toLocaleString()}</span>
            </div>

            {/* Equity curve: estratégia vs buy & hold */}
            {result.equityCurve && result.equityCurve.length > 0 && (
              <div className="border border-border rounded-lg bg-card p-4">
                <div className="text-xs font-mono text-muted-foreground uppercase tracking-widest mb-3">
                  Equity Curve — Estratégia vs Buy &amp; Hold
                </div>
                <ResponsiveContainer width="100%" height={240}>
                  <AreaChart data={result.equityCurve} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                    <defs>
                      <linearGradient id="equityGradient" x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor="#f97316" stopOpacity={0.25} />
                        <stop offset="95%" stopColor="#f97316" stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis
                      dataKey="date"
                      tick={{ fontSize: 10, fontFamily: "monospace", fill: "#6b7280" }}
                      tickLine={false}
                      axisLine={false}
                      interval="preserveStartEnd"
                      minTickGap={60}
                    />
                    <YAxis
                      tick={{ fontSize: 10, fontFamily: "monospace", fill: "#6b7280" }}
                      tickLine={false}
                      axisLine={false}
                      width={64}
                      tickFormatter={(v: number) => `$${(v / 1000).toFixed(1)}k`}
                    />
                    <Tooltip
                      contentStyle={{
                        background: "hsl(var(--card))",
                        border: "1px solid hsl(var(--border))",
                        borderRadius: "6px",
                        fontFamily: "monospace",
                        fontSize: "12px",
                      }}
                      labelStyle={{ color: "hsl(var(--muted-foreground))", marginBottom: 4 }}
                      formatter={(val: number, name: string) => [`$${val.toLocaleString()}`, name === "equity" ? "Estratégia" : "Buy & Hold"]}
                    />
                    <Legend
                      formatter={(value: string) => (value === "equity" ? "Estratégia" : "Buy & Hold")}
                      wrapperStyle={{ fontFamily: "monospace", fontSize: "11px" }}
                    />
                    <Area type="monotone" dataKey="equity" stroke="#f97316" strokeWidth={1.5} fill="url(#equityGradient)" dot={false} isAnimationActive={false} />
                    <Area type="monotone" dataKey="buyHoldEquity" stroke="#6b7280" strokeWidth={1.5} fill="none" dot={false} strokeDasharray="4 3" isAnimationActive={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Trades table */}
            {result.trades.length > 0 && (
              <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
                <div className="px-4 py-2.5 border-b border-border bg-secondary/30 text-xs font-mono text-muted-foreground uppercase tracking-widest">
                  Últimas {result.trades.length} Operações
                </div>
                <table className="w-full font-mono text-sm">
                  <thead className="bg-secondary/20">
                    <tr>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Entrada</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Saída</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Preço Entr.</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Preço Saída</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">P&L%</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Saída via</th>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Resultado</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trades.map((trade, idx) => (
                      <tr key={idx} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                        <td className="px-4 py-2.5 text-muted-foreground">{trade.entryDate}</td>
                        <td className="px-4 py-2.5 text-muted-foreground">{trade.exitDate}</td>
                        <td className="px-4 py-2.5 text-foreground">${trade.entryPrice.toFixed(2)}</td>
                        <td className="px-4 py-2.5 text-foreground">${trade.exitPrice.toFixed(2)}</td>
                        <td className={`px-4 py-2.5 font-bold ${trade.pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                          {trade.pnl >= 0 ? "+" : ""}{trade.pnl.toFixed(2)}%
                        </td>
                        <td className="px-4 py-2.5 text-muted-foreground">
                          {trade.exitReason ? EXIT_REASON_LABEL[trade.exitReason] ?? trade.exitReason : "—"}
                        </td>
                        <td className="px-4 py-2.5 flex items-center gap-1">
                          <Badge variant="outline" className={trade.win ? "text-green-500 border-green-500/30 bg-green-500/10 text-[10px] font-mono" : "text-red-500 border-red-500/30 bg-red-500/10 text-[10px] font-mono"}>
                            {trade.win ? "WIN" : "LOSS"}
                          </Badge>
                          {trade.closedOpen && (
                            <Badge variant="outline" className="text-yellow-500 border-yellow-500/30 bg-yellow-500/10 text-[10px] font-mono">ABERTO</Badge>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {(() => {
              const notes = interpretTickerResult(result);
              if (notes.length === 0) return null;
              return (
                <div className="border border-border rounded-lg bg-card p-4">
                  <div className="text-[10px] font-mono text-muted-foreground uppercase mb-2">Interpretação</div>
                  <ul className="space-y-1.5">
                    {notes.map((note, idx) => (
                      <li key={idx} className="font-mono text-xs text-muted-foreground leading-relaxed flex gap-2">
                        <span className="text-primary shrink-0">›</span>
                        <span>{note}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })()}
          </div>
        )
      )}

      {/* Sensitivity analysis */}
      {mode === "ticker" && walkForwardResult && (
        walkForwardResult.error ? (
          <div className="border border-red-500/40 rounded-lg p-4 font-mono text-sm text-red-400">
            {walkForwardResult.error}
          </div>
        ) : (
          <div className="border border-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border flex items-center gap-2 flex-wrap font-mono text-sm">
              <span className="font-bold text-foreground">
                Walk-forward — {walkForwardResult.ticker} · {walkForwardResult.strategy.toUpperCase()}
              </span>
              <span className="text-muted-foreground text-xs">
                treino {walkForwardResult.treinoPregoes} pregões → teste {walkForwardResult.testePregoes} ·
                {" "}{walkForwardResult.combinacoesTestadas} combinações · objetivo {walkForwardResult.objetivo}
              </span>
            </div>

            {walkForwardResult.resumo.aviso ? (
              <div className="px-4 py-4 font-mono text-sm text-yellow-400">
                {walkForwardResult.resumo.aviso}
              </div>
            ) : (
              <>
                {/* A degradação é o número central: quanto do backtest tradicional
                    era ajuste ao próprio período de avaliação. */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 p-4 font-mono">
                  <div>
                    <div className="text-[10px] text-muted-foreground uppercase">In-sample (treino)</div>
                    <div className="text-lg font-bold text-muted-foreground">
                      {fmtPctNum(walkForwardResult.resumo.retornoMedioInSample)}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] text-muted-foreground uppercase" title="O número honesto: medido em janela que o otimizador nunca viu">
                      Out-of-sample
                    </div>
                    <div className={`text-lg font-bold ${(walkForwardResult.resumo.retornoMedioOutOfSample ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {fmtPctNum(walkForwardResult.resumo.retornoMedioOutOfSample)}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] text-muted-foreground uppercase" title="In-sample menos out-of-sample: o quanto do resultado era ajuste de ruído">
                      Degradação
                    </div>
                    <div className={`text-lg font-bold ${(walkForwardResult.resumo.degradacao ?? 0) > 5 ? "text-red-400" : "text-yellow-400"}`}>
                      {fmtPctNum(walkForwardResult.resumo.degradacao)}
                    </div>
                  </div>
                  <div>
                    <div className="text-[10px] text-muted-foreground uppercase" title="Buy & hold nas MESMAS janelas de teste">
                      Buy &amp; hold (OOS)
                    </div>
                    <div className="text-lg font-bold text-muted-foreground">
                      {fmtPctNum(walkForwardResult.resumo.buyAndHoldMedioOutOfSample)}
                    </div>
                  </div>
                </div>

                <div className="px-4 pb-4 font-mono text-xs text-muted-foreground space-y-1">
                  <div>
                    › {walkForwardResult.resumo.foldsQueVenceramBuyHold ?? 0} de {walkForwardResult.resumo.nFolds} janelas
                    bateram o buy &amp; hold · {walkForwardResult.resumo.foldsPositivos ?? 0} tiveram retorno positivo
                  </div>
                  <div className={walkForwardResult.resumo.parametroEstavel ? "" : "text-yellow-400"}>
                    › {walkForwardResult.resumo.parametroEstavel
                        ? "O mesmo parâmetro venceu em todas as janelas — sinal de regularidade, não de ruído."
                        : `${walkForwardResult.resumo.parametrosDistintosEscolhidos} conjuntos de parâmetro diferentes venceram entre as janelas — quando o "melhor" muda a cada período, a busca está perseguindo ruído.`}
                  </div>
                  {(walkForwardResult.resumo.foldsSemSinalNoTreino ?? 0) > 0 && (
                    <div className="text-yellow-400">
                      › {walkForwardResult.resumo.foldsSemSinalNoTreino} janela(s) sem nenhum negócio no treino — sem base para escolher parâmetro, ficaram de fora.
                    </div>
                  )}
                </div>

                <div className="overflow-x-auto border-t border-border">
                  <table className="w-full min-w-[720px] font-mono text-sm">
                    <thead className="bg-secondary/20">
                      <tr>
                        <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Treino</th>
                        <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Parâmetro escolhido</th>
                        <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Teste (não visto)</th>
                        <th className="text-right px-4 py-2 text-[10px] text-muted-foreground uppercase">In-sample</th>
                        <th className="text-right px-4 py-2 text-[10px] text-muted-foreground uppercase">Out-of-sample</th>
                        <th className="text-right px-4 py-2 text-[10px] text-muted-foreground uppercase">B&amp;H</th>
                      </tr>
                    </thead>
                    <tbody>
                      {walkForwardResult.folds.map((f, i) => (
                        <tr key={`${f.testeInicio}-${i}`} className={i % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                          <td className="px-4 py-2 text-muted-foreground whitespace-nowrap text-xs">
                            {f.treinoInicio} → {f.treinoFim}
                          </td>
                          <td className="px-4 py-2 text-foreground text-xs">
                            {f.semSinalNoTreino
                              ? <span className="text-yellow-400">sem negócio no treino</span>
                              : Object.entries(f.melhorParams ?? {}).map(([k, v]) => `${k}=${v}`).join(" · ") || "—"}
                          </td>
                          <td className="px-4 py-2 text-muted-foreground whitespace-nowrap text-xs">
                            {f.testeInicio} → {f.testeFim}
                          </td>
                          <td className="px-4 py-2 text-right text-muted-foreground">
                            {fmtPctNum(f.inSample?.totalReturn)}
                          </td>
                          <td className={`px-4 py-2 text-right font-bold ${(f.outOfSample?.totalReturn ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                            {f.outOfSample?.error ? "—" : fmtPctNum(f.outOfSample?.totalReturn)}
                          </td>
                          <td className="px-4 py-2 text-right text-muted-foreground">
                            {fmtPctNum(f.outOfSample?.buyAndHoldReturn)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>
        )
      )}

      {mode === "ticker" && sensitivityResult && (
        sensitivityResult.error ? (
          <div className="p-6 border border-red-500/30 rounded-lg bg-red-500/5 font-mono text-red-400 text-sm">
            {sensitivityResult.error}
          </div>
        ) : (
          <div className="space-y-4">
            <div className="border border-border rounded-lg bg-card p-3 font-mono text-xs text-muted-foreground flex gap-4 flex-wrap">
              <span>Sensibilidade — {sensitivityResult.ticker} · {sensitivityResult.strategy.toUpperCase()}</span>
              <span>{sensitivityResult.start} → {sensitivityResult.end}</span>
              {!sensitivityResult.baseline.error && (
                <span>
                  Config. atual: <span className={sensitivityResult.baseline.totalReturn >= 0 ? "text-green-400" : "text-red-400"}>
                    {sensitivityResult.baseline.totalReturn >= 0 ? "+" : ""}{sensitivityResult.baseline.totalReturn.toFixed(2)}%
                  </span>
                </span>
              )}
            </div>

            {Object.entries(
              sensitivityResult.variations.reduce<Record<string, SensitivityRun[]>>((acc, v) => {
                const key = v.param ?? "?";
                (acc[key] ??= []).push(v);
                return acc;
              }, {})
            ).map(([param, rows]) => (
              <div key={param} className="border border-border rounded-lg overflow-hidden overflow-x-auto">
                <div className="px-4 py-2.5 border-b border-border bg-secondary/30 text-xs font-mono text-muted-foreground uppercase tracking-widest">
                  {SENSITIVITY_PARAM_LABEL[param] ?? param}
                </div>
                <table className="w-full font-mono text-sm">
                  <thead className="bg-secondary/20">
                    <tr>
                      <th className="text-left px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Valor</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Retorno</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Sharpe</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Max DD</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Trades</th>
                      <th className="text-right px-4 py-2.5 text-[10px] text-muted-foreground uppercase">Win Rate</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row, idx) => (
                      <tr key={idx} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                        <td className="px-4 py-2.5 font-bold text-primary">
                          {formatSensitivityValue(param, row.value ?? 0)}
                        </td>
                        {row.error ? (
                          <td colSpan={5} className="px-4 py-2.5 text-muted-foreground">{row.error}</td>
                        ) : (
                          <>
                            <td className={`px-4 py-2.5 text-right font-bold ${row.totalReturn >= 0 ? "text-green-400" : "text-red-400"}`}>
                              {row.totalReturn >= 0 ? "+" : ""}{row.totalReturn.toFixed(2)}%
                            </td>
                            <td className="px-4 py-2.5 text-right text-muted-foreground">{row.sharpe.toFixed(2)}</td>
                            <td className="px-4 py-2.5 text-right text-red-400">{row.maxDrawdown.toFixed(2)}%</td>
                            <td className="px-4 py-2.5 text-right text-muted-foreground">{row.totalTrades}</td>
                            <td className="px-4 py-2.5 text-right text-muted-foreground">{row.totalTrades > 0 ? `${row.winRate}%` : "—"}</td>
                          </>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )
      )}
    </div>
  );
}
