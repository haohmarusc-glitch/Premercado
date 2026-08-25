import { useState } from "react";
import { Link } from "wouter";
import { useMutation } from "@tanstack/react-query";
import { Library, ArrowRight, FlaskConical } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * "As 10 Análises" — o índice das dez análises pedidas, com o SELO DE ORIGEM
 * de cada uma.
 *
 * O selo é o ponto da tela. Os dez prompts originais pedem, no mesmo fôlego,
 * coisas que o app mede (reação histórica a earnings, correlação, múltiplos)
 * e coisas que nenhuma fonte aqui tem (market share dos últimos 3 anos,
 * qualidade de gestão, previsão do Fed para 12 meses). Um LLM responde as
 * duas com a mesma fluência — e é assim que estimativa vira "dado" na cabeça
 * de quem lê. Aqui cada análise declara de onde vem:
 *
 *   MEDIDA  — tudo que ela responde sai de dado verificável, já auditado.
 *   PARCIAL — o núcleo é medido; a tela nomeia o que ficou de fora e por quê.
 *   FORA    — não dá para fazer honestamente com o que existe aqui. Fica
 *             listada, com o motivo, em vez de sumir (quem leu o prompt vai
 *             procurar).
 */

type Selo = "MEDIDA" | "PARCIAL" | "FORA";

const SELO_CLASSE: Record<Selo, string> = {
  MEDIDA: "bg-green-500/15 text-green-400 border-green-500/30",
  PARCIAL: "bg-yellow-500/15 text-yellow-400 border-yellow-500/30",
  FORA: "bg-muted text-muted-foreground border-border",
};

export interface Analise {
  n: number;
  titulo: string;
  casa: string;
  pergunta: string;
  selo: Selo;
  medido: string;
  ressalva?: string;
  destinos?: { rotulo: string; href: string }[];
  inline?: boolean;
}

export const ANALISES: Analise[] = [
  {
    n: 1, titulo: "Screening de ações", casa: "Goldman Sachs",
    pergunta: "Quais papéis passam nos meus critérios, e a que preço entrar?",
    selo: "PARCIAL",
    medido: "Técnicos e tendência por ticker, alvo médio de analistas com upside, short interest, e zona de entrada derivada da volatilidade medida.",
    ressalva: "\"Rating de moat\" e \"nota de risco de 1 a 10\" não têm fonte — seriam opinião com cara de número. Preço-alvo bull/bear: o app usa o alvo real dos analistas, não um número inventado para 12 meses.",
    destinos: [{ rotulo: "Screener", href: "/screener" }, { rotulo: "Analistas", href: "/analistas" }],
  },
  {
    n: 2, titulo: "Valuation por DCF", casa: "Morgan Stanley",
    pergunta: "O papel está caro ou barato contra o fluxo de caixa que gera?",
    selo: "PARCIAL",
    medido: "DCF e múltiplos TTM (P/L, P/VP, ROE, EV/EBITDA) via Financial Modeling Prep, no fluxo do agente.",
    ressalva: "Exige FMP_API_KEY configurada. E um DCF é tão bom quanto as premissas de crescimento e WACC: um \"valor justo\" único esconde isso. A leitura honesta é a faixa, não o ponto.",
    destinos: [{ rotulo: "Análise Rápida (usa a ferramenta)", href: "/analise-rapida" }],
  },
  {
    n: 3, titulo: "Risco da carteira", casa: "Bridgewater",
    pergunta: "Onde a carteira quebra, e quanto disso é o mesmo trade repetido?",
    selo: "MEDIDA",
    medido: "Correlação medida entre posições, concentração por cluster e setor, beta, sensibilidade a juros/dólar (análise 9), stress de correlação e dimensionamento de posição.",
    ressalva: "Exposição geográfica por receita não existe como dado aqui — o que se mede é a exposição por preço.",
    destinos: [{ rotulo: "Carteira / Risco", href: "/portfolio" }, { rotulo: "Cenários", href: "/cenarios" }],
  },
  {
    n: 4, titulo: "Prévia de resultados", casa: "JPMorgan",
    pergunta: "O que o histórico diz que acontece quando este papel reporta?",
    selo: "MEDIDA",
    medido: "Reação histórica evento a evento (gap, fechamento, range, volume, run-up de 21 pregões e trajetória pós-balanço), calendário confirmado diariamente, movimento implícito pelas opções e EPS estimado.",
    ressalva: "Receita por segmento e resumo de guidance exigiriam ler 10-K e transcrição — fora do que o app coleta hoje.",
    destinos: [{ rotulo: "Reação a Earnings", href: "/earnings-reaction" }, { rotulo: "Earnings", href: "/earnings" }, { rotulo: "Opções", href: "/opcoes" }],
  },
  {
    n: 5, titulo: "Construção de portfólio", casa: "BlackRock",
    pergunta: "Como dividir o patrimônio entre ações, renda fixa e alternativos?",
    selo: "FORA",
    medido: "Nada: o app não coleta renda fixa, ETFs de outras classes nem dado de conta/tributação.",
    ressalva: "Além da falta de dado, o prompt pede recomendação personalizada a partir de idade, renda e tolerância a risco, com estratégia fiscal — isso é aconselhamento financeiro, não análise de mercado. O app é infraestrutura de análise de semicondutores/IA, e o aviso do README vale aqui.",
  },
  {
    n: 6, titulo: "Análise técnica completa", casa: "Citadel",
    pergunta: "O que o preço está fazendo, e onde ficam entrada, stop e alvo?",
    selo: "MEDIDA",
    medido: "Tendência em vários prazos, MM20/50/200 e cruzamentos, RSI de Wilder, MACD, Bollinger, volume relativo por mediana, suportes e resistências, e entrada/stop/alvo derivados da volatilidade real.",
    ressalva: "Padrões gráficos nomeados (ombro-cabeça-ombro, xícara com alça) não são detectados: reconhecimento visual desses padrões é subjetivo e não reproduzível. O que existe é estrutura de pivôs (topos e fundos ascendentes/descendentes), que é verificável.",
    destinos: [{ rotulo: "Técnicos", href: "/tecnicos" }, { rotulo: "Gráfico", href: "/grafico" }, { rotulo: "Backtest (confluência)", href: "/backtest" }],
  },
  {
    n: 7, titulo: "Renda por dividendos", casa: "Harvard Endowment",
    pergunta: "Que carteira de dividendos gera renda mensal previsível?",
    selo: "FORA",
    medido: "Dividend yield e payout existem no yfinance, mas a carteira monitorada é de semicondutores e IA — papéis que pagam pouco ou nada.",
    ressalva: "A parte fiscal do prompt é a regra americana (qualified dividends, tipo de conta). Aplicar isso a um investidor no Brasil daria conselho errado com aparência de precisão.",
  },
  {
    n: 8, titulo: "Vantagem competitiva", casa: "Bain & Company",
    pergunta: "Quem ganha no setor, e por quê?",
    selo: "PARCIAL",
    medido: "A metade quantitativa: market cap, receita, margens, gasto em P&D e múltiplos dos pares — tudo verificável.",
    ressalva: "Moat por marca/custo/rede/switching, nota de qualidade de gestão e market share dos últimos 3 anos não têm fonte no app: seriam texto convincente sem número atrás. Se um dia entrar, entra como leitura declarada de IA sobre a tabela medida — nunca como medição.",
    destinos: [{ rotulo: "Setor IA", href: "/sector-ai" }],
  },
  {
    n: 9, titulo: "Padrões e anomalias", casa: "Renaissance Technologies",
    pergunta: "Este papel tem padrão explorável — ou é ruído com boa história?",
    selo: "MEDIDA",
    medido: "Sazonalidade mensal, efeito de dia da semana, comportamento em dias de FOMC/CPI/PCE e sensibilidade a fatores (setor, juros, dólar, VIX). Cada padrão vem com amostra, intervalo de confiança por bootstrap e p-valor de permutação, corrigido por Holm-Bonferroni.",
    ressalva: "É a análise mais fácil de fazer errado: varrer ~17 padrões a 5% acha um \"significativo\" por puro acaso. A correção existe para que o veredito possa dizer \"nenhum sobrevive\" — que é a resposta correta na maioria dos papéis.",
    inline: true,
  },
  {
    n: 10, titulo: "Impacto macro", casa: "McKinsey",
    pergunta: "Como o cenário econômico atinge esta carteira?",
    selo: "PARCIAL",
    medido: "CPI, Fed funds, curva de juros, desemprego, Fear & Greed, performance setorial e o risco macro diário de seis fontes — mais o beta medido de cada papel a juros, dólar e setor (análise 9).",
    ressalva: "\"Outlook do Fed para 6–12 meses\" e recomendação de rotação setorial são previsão, não medição. O app mostra o retrato de hoje e a sensibilidade medida; a previsão fica com quem decide.",
    destinos: [{ rotulo: "Macro", href: "/macro" }, { rotulo: "Risco Macro", href: "/macro" }],
  },
];

// ── resultado da análise 9 ───────────────────────────────────────────────────

interface Padrao {
  rotulo: string;
  n: number;
  retorno_medio_pct: number | null;
  positivos_pct: number | null;
  ic95_pct: [number, number] | null;
  p_valor: number | null;
  sobrevive: boolean;
  nota?: string;
}

interface Fator {
  fator: string;
  modo: string;
  beta: number | null;
  r2: number | null;
  n: number;
  relevante?: boolean;
  nota?: string;
}

interface PadroesResult {
  ticker: string;
  inicio: string;
  fim: string;
  pregoes: number;
  alfa: number;
  permutacoes: number;
  sazonalidade: Padrao[];
  diaDaSemana: Padrao[];
  eventosMacro: Padrao[];
  fatores: Fator[];
  testados: number;
  sobreviventes: number;
  veredito: string;
  leituraFatores: string;
  error?: string;
}

function fmt(v: number | null | undefined, casas = 2, sufixo = ""): string {
  return v == null || !Number.isFinite(v) ? "—" : `${v >= 0 ? "+" : ""}${v.toFixed(casas)}${sufixo}`;
}

function TabelaPadroes({ titulo, linhas }: { titulo: string; linhas: Padrao[] }) {
  if (!linhas.length) return null;
  return (
    <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
      <div className="px-4 py-2.5 border-b border-border bg-secondary/30 text-[10px] font-mono text-muted-foreground uppercase tracking-widest">
        {titulo}
      </div>
      <table className="w-full font-mono text-xs">
        <thead>
          <tr className="border-b border-border/60 text-[10px] text-muted-foreground uppercase">
            <th className="text-left px-3 py-2">Período</th>
            <th className="text-right px-3 py-2">n</th>
            <th className="text-right px-3 py-2">Retorno médio</th>
            <th className="text-right px-3 py-2">% positivos</th>
            <th className="text-right px-3 py-2">IC 95%</th>
            <th className="text-right px-3 py-2">p</th>
            <th className="text-right px-3 py-2">Sobrevive?</th>
          </tr>
        </thead>
        <tbody>
          {linhas.map((l) => (
            <tr key={l.rotulo} className="border-b border-border/40 last:border-0">
              <td className="px-3 py-2 text-foreground">{l.rotulo}</td>
              <td className="px-3 py-2 text-right text-muted-foreground">{l.n}</td>
              <td className={cn("px-3 py-2 text-right", (l.retorno_medio_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400")}>
                {fmt(l.retorno_medio_pct, 3, "%")}
              </td>
              <td className="px-3 py-2 text-right text-muted-foreground">
                {l.positivos_pct == null ? "—" : `${l.positivos_pct.toFixed(0)}%`}
              </td>
              <td className="px-3 py-2 text-right text-muted-foreground">
                {l.ic95_pct ? `${l.ic95_pct[0].toFixed(2)}% a ${l.ic95_pct[1].toFixed(2)}%` : "—"}
              </td>
              <td className="px-3 py-2 text-right text-muted-foreground" title={l.nota ?? ""}>
                {l.p_valor == null ? (l.nota ? "amostra curta" : "—") : l.p_valor.toFixed(4)}
              </td>
              <td className={cn("px-3 py-2 text-right font-bold", l.sobrevive ? "text-green-400" : "text-muted-foreground")}>
                {l.p_valor == null ? "—" : l.sobrevive ? "sim" : "não"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AnalisesPage() {
  const [ticker, setTicker] = useState("NVDA");
  const [anos, setAnos] = useState("5");
  const [padroes, setPadroes] = useState<PadroesResult | null>(null);

  const rodar = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/analises/padroes", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: ticker.toUpperCase(), anos: parseInt(anos, 10) || 5 }),
      });
      const data = await r.json();
      if (!r.ok || data.error) throw new Error(data.error || "Falha na análise de padrões");
      return data as PadroesResult;
    },
    onSuccess: (d) => setPadroes(d),
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
          <Library className="h-7 w-7 text-primary" /> AS 10 ANÁLISES
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          As dez análises, com o selo de origem de cada uma — o que é medido, o que é parcial e o que não dá para fazer honestamente aqui.
        </p>
      </div>

      <div className="border border-border rounded-lg bg-card p-4 font-mono text-xs text-muted-foreground space-y-1">
        <div><span className={cn("px-1.5 py-0.5 rounded border mr-2", SELO_CLASSE.MEDIDA)}>MEDIDA</span> tudo que a análise responde sai de dado verificável.</div>
        <div><span className={cn("px-1.5 py-0.5 rounded border mr-2", SELO_CLASSE.PARCIAL)}>PARCIAL</span> o núcleo é medido; o que ficou de fora está nomeado.</div>
        <div><span className={cn("px-1.5 py-0.5 rounded border mr-2", SELO_CLASSE.FORA)}>FORA</span> sem fonte no app — fica listada com o motivo, em vez de virar texto convincente.</div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        {ANALISES.map((a) => (
          <div key={a.n} className="border border-border rounded-lg bg-card p-4 space-y-2">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="font-mono text-sm font-bold text-foreground">{a.n}. {a.titulo}</div>
                <div className="font-mono text-[10px] text-muted-foreground uppercase tracking-widest">{a.casa}</div>
              </div>
              <span className={cn("shrink-0 px-2 py-0.5 rounded border font-mono text-[10px] font-bold", SELO_CLASSE[a.selo])}>
                {a.selo}
              </span>
            </div>
            <p className="font-mono text-xs text-foreground/90">{a.pergunta}</p>
            <p className="font-mono text-[11px] text-muted-foreground">{a.medido}</p>
            {a.ressalva && (
              <p className="font-mono text-[11px] text-yellow-400/80 border-l-2 border-yellow-500/30 pl-2">{a.ressalva}</p>
            )}
            {a.destinos && (
              <div className="flex flex-wrap gap-2 pt-1">
                {a.destinos.map((d) => (
                  <Link key={d.href + d.rotulo} href={d.href}>
                    <a className="inline-flex items-center gap-1 px-2 py-1 rounded border border-border font-mono text-[11px] text-foreground hover:border-primary/50">
                      {d.rotulo} <ArrowRight className="h-3 w-3" />
                    </a>
                  </Link>
                ))}
              </div>
            )}
            {a.inline && (
              <div className="pt-1 font-mono text-[11px] text-primary">↓ roda aqui embaixo</div>
            )}
          </div>
        ))}
      </div>

      {/* Análise 9 — a única que não existe em nenhuma outra tela */}
      <div className="border border-border rounded-lg bg-card p-5 space-y-4">
        <div>
          <h2 className="font-mono text-sm font-bold text-foreground flex items-center gap-2">
            <FlaskConical className="h-4 w-4 text-primary" /> 9. PADRÕES E ANOMALIAS
          </h2>
          <p className="font-mono text-[11px] text-muted-foreground mt-1">
            Sazonalidade, dia da semana, dias de evento macro e sensibilidade a fatores — cada padrão com amostra,
            IC de 95% por bootstrap e p-valor de permutação corrigido por Holm-Bonferroni.
          </p>
        </div>

        <div className="flex flex-wrap gap-3 items-end">
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Ticker</label>
            <input
              type="text" value={ticker}
              onChange={(e) => setTicker(e.target.value.toUpperCase())}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary w-32"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Anos de histórico</label>
            <input
              type="number" min="2" max="10" value={anos}
              onChange={(e) => setAnos(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary w-24"
            />
          </div>
          <button
            onClick={() => rodar.mutate()}
            disabled={rodar.isPending || !ticker.trim()}
            className="px-6 py-2 bg-primary text-primary-foreground rounded font-mono text-sm font-bold disabled:opacity-50 flex items-center gap-2"
          >
            {rodar.isPending ? (
              <>
                <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full" />
                Medindo...
              </>
            ) : "Medir padrões"}
          </button>
        </div>
        {rodar.isError && <p className="text-sm text-red-400 font-mono">{String(rodar.error)}</p>}

        {padroes && !padroes.error && (
          <div className="space-y-4">
            <div className={cn(
              "border rounded-lg p-4 font-mono text-sm",
              padroes.sobreviventes > 0
                ? "border-yellow-500/30 bg-yellow-500/5 text-yellow-300"
                : "border-border bg-secondary/20 text-foreground/90",
            )}>
              <div className="text-[10px] uppercase tracking-widest text-muted-foreground mb-1">Veredito</div>
              {padroes.veredito}
            </div>

            <div className="border border-border rounded-lg bg-secondary/10 p-3 font-mono text-xs text-muted-foreground flex gap-4 flex-wrap">
              <span>{padroes.ticker}</span>
              <span>{padroes.inicio} → {padroes.fim}</span>
              <span>{padroes.pregoes} pregões</span>
              <span>{padroes.testados} padrões testados · {padroes.sobreviventes} sobrevive(m)</span>
              <span>α={padroes.alfa} · {padroes.permutacoes} permutações</span>
            </div>

            <TabelaPadroes titulo="Sazonalidade por mês" linhas={padroes.sazonalidade} />
            <TabelaPadroes titulo="Dia da semana" linhas={padroes.diaDaSemana} />
            {padroes.eventosMacro.length > 0 && (
              <TabelaPadroes titulo="Dias de evento macro" linhas={padroes.eventosMacro} />
            )}

            <div className="border border-border rounded-lg overflow-hidden overflow-x-auto">
              <div className="px-4 py-2.5 border-b border-border bg-secondary/30 text-[10px] font-mono text-muted-foreground uppercase tracking-widest">
                Sensibilidade a fatores
              </div>
              <table className="w-full font-mono text-xs">
                <thead>
                  <tr className="border-b border-border/60 text-[10px] text-muted-foreground uppercase">
                    <th className="text-left px-3 py-2">Fator</th>
                    <th className="text-right px-3 py-2">Beta</th>
                    <th className="text-right px-3 py-2">R²</th>
                    <th className="text-right px-3 py-2">n</th>
                    <th className="text-right px-3 py-2">Explica?</th>
                  </tr>
                </thead>
                <tbody>
                  {padroes.fatores.map((f) => (
                    <tr key={f.fator} className="border-b border-border/40 last:border-0">
                      <td className="px-3 py-2 text-foreground">{f.fator}</td>
                      <td className="px-3 py-2 text-right text-muted-foreground">{fmt(f.beta, 2)}</td>
                      <td className="px-3 py-2 text-right text-muted-foreground">{f.r2 == null ? "—" : f.r2.toFixed(3)}</td>
                      <td className="px-3 py-2 text-right text-muted-foreground">{f.n}</td>
                      <td className={cn("px-3 py-2 text-right", f.relevante ? "text-green-400" : "text-muted-foreground")}>
                        {f.nota ? f.nota : f.relevante ? "sim" : "quase nada"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <p className="font-mono text-[11px] text-muted-foreground border-l-2 border-primary/40 pl-3">
              {padroes.leituraFatores}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
