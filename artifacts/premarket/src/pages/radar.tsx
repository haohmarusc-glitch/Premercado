import { useMemo, useState } from "react";
import { useGetRadar, type RadarSnapshot } from "@workspace/api-client-react";
import { Badge } from "@/components/ui/badge";
import {
  Radar as RadarIcon, CalendarClock, Link2, Flame, AlertTriangle,
  ChevronDown, ChevronUp, TrendingDown,
} from "lucide-react";
import { ExportarRelatorio, cabecalho, tabela, pct } from "@/components/exportar-relatorio";

// Datas chegam como "YYYY-MM-DD" puro -- formatar na string, nunca via
// `new Date(iso)` (meia-noite UTC vira o dia anterior em BRT, mesma
// armadilha documentada em entry-exit-study.tsx).
function fmtDateBR(isoDate: string) {
  const [y, m, d] = isoDate.split("-");
  return `${d}/${m}/${y}`;
}

function hojeISO(): string {
  // Data local do navegador do usuário -- pra exibição de "em Xd" basta;
  // o dado autoritativo (alertas de contágio) usa BRT no servidor.
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function diasAte(isoDate: string): number {
  const alvo = new Date(isoDate + "T00:00:00");
  const hoje = new Date(hojeISO() + "T00:00:00");
  return Math.round((alvo.getTime() - hoje.getTime()) / 86400000);
}

// Precisa cobrir TODO grupo presente em TEMA_IA (agent/radar_ia_2026.py) — há
// um teste que lê o Python e quebra se algum ficar sem rótulo. A chave `rede`
// morreu quando o dado passou a usar `networking`; o rótulo sobreviveu, só
// mudou de chave, e por um tempo ANET e CSCO apareceram como "networking" cru.
export const GRUPO_LABEL: Record<string, string> = {
  memoria: "Memória",
  equipamento: "Equipamento",
  chips: "Chips",
  foundry: "Foundry",
  hardware: "Hardware",
  energia: "Energia",
  networking: "Rede/Interconexão",
  software: "Software",
  hyperscaler: "Hyperscalers",
  neocloud: "Neoclouds",
};

/**
 * Rótulo de exibição de um grupo. O fallback capitaliza em vez de devolver a
 * chave crua: se um grupo novo entrar no radar antes de ganhar rótulo aqui,
 * ele aparece como "Fotônica" e não como "fotonica" no meio de uma coluna de
 * nomes capitalizados. O teste continua cobrando o rótulo próprio.
 */
export function rotuloGrupo(grupo: string | null | undefined): string {
  if (!grupo) return "—";
  return GRUPO_LABEL[grupo] ?? grupo.charAt(0).toUpperCase() + grupo.slice(1);
}

function corrColorClass(c: number) {
  if (c >= 0.7) return "text-red-400";
  if (c >= 0.5) return "text-yellow-400";
  return "text-muted-foreground";
}

// ── Earnings próximos ───────────────────────────────────────────────────────

function EarningsWatch({ data }: { data: RadarSnapshot }) {
  const eventos = useMemo(() => {
    const hoje = hojeISO();
    return Object.entries(data.earnings)
      .filter(([, e]) => e.data >= hoje)
      .map(([ticker, e]) => ({ ticker, ...e, reacao: data.reacao_earnings[ticker] }))
      .sort((a, b) => a.data.localeCompare(b.data));
  }, [data]);

  if (eventos.length === 0) {
    return (
      <div className="text-sm font-mono text-muted-foreground px-4 py-6">
        Nenhum earnings futuro no snapshot — os dados vão até set/2026; hora de atualizar o radar.
      </div>
    );
  }

  return (
    // min-w força a tabela a transbordar num celular em vez de espremer as
    // colunas até o conteúdo sumir (visto em produção: a coluna de status
    // ficava cortada fora da tela, sem rolagem) -- o overflow-x-auto do pai
    // só rola de verdade quando o filho é MAIOR que ele.
    <div className="overflow-x-auto">
      <table className="w-full min-w-[680px] font-mono text-sm">
        <thead className="bg-secondary/20">
          <tr>
            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Data</th>
            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Ticker</th>
            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Setor</th>
            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase" title="BO = antes da abertura (reação no próprio pregão) · AC = após o fechamento (reação no pregão seguinte)">Quando</th>
            <th className="text-right px-4 py-2 text-[10px] text-muted-foreground uppercase" title="Earnings Volatility Rating (OptionSlam, 0-10)">EVR</th>
            <th className="text-right px-4 py-2 text-[10px] text-muted-foreground uppercase" title="Move implícito semanal precificado pelas opções">Move impl.</th>
            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Nota</th>
          </tr>
        </thead>
        <tbody>
          {eventos.map((e, idx) => {
            const dias = diasAte(e.data);
            return (
              <tr key={`${e.ticker}-${e.data}`} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                <td className="px-4 py-2 text-muted-foreground whitespace-nowrap">
                  {fmtDateBR(e.data)}
                  <span className={`ml-2 text-[10px] ${dias <= 2 ? "text-yellow-400" : "text-muted-foreground/60"}`}>
                    {dias === 0 ? "hoje" : `em ${dias}d`}
                  </span>
                </td>
                <td className="px-4 py-2 font-bold text-foreground">{e.ticker}</td>
                <td className="px-4 py-2 text-muted-foreground">{e.setor}</td>
                <td className="px-4 py-2 text-muted-foreground">{e.quando ?? "—"}</td>
                <td className="px-4 py-2 text-right text-muted-foreground">{e.reacao?.evr ?? "—"}</td>
                <td className="px-4 py-2 text-right text-muted-foreground">
                  {e.reacao?.move_impl_sem != null ? `±${e.reacao.move_impl_sem}%` : "—"}
                </td>
                <td className="px-4 py-2 text-[11px] text-yellow-400/80">{e.nota ?? ""}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ── Pares de correlação ─────────────────────────────────────────────────────

function CorrelacoesFortes({ data }: { data: RadarSnapshot }) {
  const [mostrarTodos, setMostrarTodos] = useState(false);
  const pares = useMemo(() => {
    return Object.entries(data.correlacoes)
      .map(([par, c]) => {
        const [a, b] = par.split("|");
        return { a, b, c };
      })
      .sort((x, y) => y.c - x.c);
  }, [data]);

  const visiveis = mostrarTodos ? pares : pares.filter((p) => p.c >= 0.6);

  return (
    <div className="px-4 pb-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5">
        {visiveis.map((p) => (
          <div
            key={`${p.a}|${p.b}`}
            className="flex items-center justify-between border border-border/50 rounded px-3 py-1.5 font-mono text-sm bg-card"
          >
            <span className="text-foreground">
              {p.a} <Link2 className="inline h-3 w-3 text-muted-foreground" /> {p.b}
            </span>
            <span className={`font-bold ${corrColorClass(p.c)}`}>
              {p.c.toFixed(2)}
              {p.c >= 0.7 && (
                <span className="ml-1.5 text-[10px] font-normal text-red-400/80" title="Correlação >= 0.70: sinais nesses dois nomes são o mesmo trade contado duas vezes">
                  mesmo trade
                </span>
              )}
            </span>
          </div>
        ))}
      </div>
      <button
        className="mt-3 text-xs font-mono text-muted-foreground hover:text-foreground flex items-center gap-1"
        onClick={() => setMostrarTodos((v) => !v)}
      >
        {mostrarTodos ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
        {mostrarTodos ? "mostrar só ≥ 0.60" : `mostrar todos os ${pares.length} pares medidos`}
      </button>
    </div>
  );
}

// ── Tema IA (YTD / vol / beta) ──────────────────────────────────────────────

function TemaIA({ data }: { data: RadarSnapshot }) {
  const linhas = useMemo(() => {
    return Object.entries(data.tema_ia)
      .map(([ticker, d]) => ({ ticker, ...d }))
      .sort((a, b) => (b.ytd ?? -Infinity) - (a.ytd ?? -Infinity));
  }, [data]);

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] font-mono text-sm">
        <thead className="bg-secondary/20">
          <tr>
            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Ticker</th>
            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Grupo</th>
            <th className="text-right px-4 py-2 text-[10px] text-muted-foreground uppercase">YTD</th>
            <th className="text-right px-4 py-2 text-[10px] text-muted-foreground uppercase" title="Volatilidade semanal medida; 'est' = estimativa de setor">Vol sem.</th>
            <th className="text-right px-4 py-2 text-[10px] text-muted-foreground uppercase">Beta</th>
            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Driver</th>
          </tr>
        </thead>
        <tbody>
          {linhas.map((l, idx) => (
            <tr key={l.ticker} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
              <td className="px-4 py-2 font-bold text-foreground">{l.ticker}</td>
              <td className="px-4 py-2 text-muted-foreground">{rotuloGrupo(l.grupo)}</td>
              <td className={`px-4 py-2 text-right font-bold ${(l.ytd ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                {l.ytd != null ? `${l.ytd >= 0 ? "+" : ""}${l.ytd.toFixed(1)}%` : "—"}
              </td>
              <td className="px-4 py-2 text-right text-muted-foreground">
                {l.vol_sem != null ? `${l.vol_sem.toFixed(1)}%` : "—"}
                {l.est && <span className="ml-1 text-[10px] text-yellow-400/70" title="Vol estimada pelo setor, não medida">est</span>}
              </td>
              <td className="px-4 py-2 text-right text-muted-foreground">{l.beta != null ? l.beta.toFixed(2) : "—"}</td>
              <td className="px-4 py-2 text-[11px] text-muted-foreground">{l.driver ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Riscos + mínima de 52 semanas ───────────────────────────────────────────

function RiscosEMinimas({ data }: { data: RadarSnapshot }) {
  const tickers = Object.keys(data.riscos).sort();
  return (
    <div className="px-4 pb-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div>
        <div className="text-[10px] font-mono text-muted-foreground uppercase mb-2 flex items-center gap-1">
          <AlertTriangle className="h-3 w-3" /> Riscos mapeados por ticker
        </div>
        <div className="space-y-2">
          {tickers.map((t) => (
            <div key={t} className="border border-border/50 rounded p-2.5 bg-card">
              <div className="font-mono text-sm font-bold text-foreground mb-1">{t}</div>
              <ul className="space-y-0.5">
                {data.riscos[t].map((r, i) => (
                  <li key={i} className={`font-mono text-[11px] ${r.startsWith("positivo") ? "text-green-400/80" : "text-muted-foreground"}`}>
                    › {r}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>
      <div>
        <div className="text-[10px] font-mono text-muted-foreground uppercase mb-2 flex items-center gap-1">
          <TrendingDown className="h-3 w-3" /> Proximidade da mínima de 52 semanas (no snapshot)
        </div>
        {/* Faltava o contêiner de rolagem aqui: num celular a coluna de
            status ficava cortada fora da tela e não havia como alcançá-la. */}
        <div className="overflow-x-auto border border-border/50 rounded">
          <table className="w-full min-w-[460px] font-mono text-sm">
            <thead className="bg-secondary/20">
              <tr>
                <th className="text-left px-3 py-1.5 text-[10px] text-muted-foreground uppercase">Ticker</th>
                <th className="text-right px-3 py-1.5 text-[10px] text-muted-foreground uppercase">Preço ref.</th>
                <th className="text-right px-3 py-1.5 text-[10px] text-muted-foreground uppercase">Mín. 52s</th>
                <th className="text-right px-3 py-1.5 text-[10px] text-muted-foreground uppercase" title="Distância do preço de referência até a mínima de 52 semanas">Dist.</th>
                <th className="text-left px-3 py-1.5 text-[10px] text-muted-foreground uppercase">Status</th>
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.min52).map(([t, m], idx) => {
                const dist = m.preco != null && m.min52 ? (m.preco / m.min52 - 1) * 100 : null;
                return (
                  <tr key={t} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                    <td className="px-3 py-1.5 font-bold text-foreground">{t}</td>
                    <td className="px-3 py-1.5 text-right text-muted-foreground">{m.preco != null ? `$${m.preco.toFixed(2)}` : "—"}</td>
                    <td className="px-3 py-1.5 text-right text-muted-foreground">{m.min52 != null ? `$${m.min52.toFixed(2)}` : "—"}</td>
                    <td className={`px-3 py-1.5 text-right ${dist == null ? "text-muted-foreground" : dist <= 10 ? "text-green-400" : "text-muted-foreground"}`}>
                      {dist != null ? `${dist >= 0 ? "+" : ""}${dist.toFixed(1)}%` : "—"}
                    </td>
                    <td className="px-3 py-1.5">
                      <Badge variant="outline" className={`font-mono text-[10px] whitespace-nowrap ${m.status === "dentro" ? "border-green-500/40 text-green-400" : "border-yellow-500/40 text-yellow-400"}`}>
                        {m.status ?? "—"}
                      </Badge>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="mt-2 font-mono text-[10px] text-muted-foreground/70">
          Preços de referência do snapshot ({fmtDateBR(data.snapshot)}) — conferir cotação atual antes de decidir.
        </p>
      </div>
    </div>
  );
}

// ── Página ──────────────────────────────────────────────────────────────────

// Relatório do radar. Correlações vão só as ≥0.50 — as 109 pares inteiras
// viram 5 páginas de e-mail em que o que importa (o que passou de 0.70) some
// no meio. O limiar é o mesmo que a tela usa pra colorir.
function montarRelatorioRadar(data: RadarSnapshot): string {
  const blocos: string[] = [];
  const janela = data.correlacoes_janela_fim ?? data.snapshot;
  blocos.push(cabecalho(
    "Radar IA 2026",
    `Snapshot ${fmtDateBR(data.snapshot)} · correlações até ${fmtDateBR(janela)}` +
    (data.correlacoes_atualizado_em ? ` (medidas em ${data.correlacoes_atualizado_em})` : ""),
  ));

  const hoje = hojeISO();
  const eventos = Object.entries(data.earnings)
    .filter(([, e]) => e.data >= hoje)
    .sort((a, b) => a[1].data.localeCompare(b[1].data));
  if (eventos.length) {
    blocos.push("## Earnings no radar\n\n" + tabela(
      ["Ticker", "Data", "Quando", "Setor", "EVR", "Move impl. semana"],
      eventos.map(([t, e]) => {
        const r = data.reacao_earnings[t];
        return [
          t, fmtDateBR(e.data), e.quando ?? "—", e.setor,
          r?.evr != null ? r.evr.toFixed(2) : "—",
          r?.move_impl_sem != null ? `${r.move_impl_sem.toFixed(1)}%` : "—",
        ];
      }),
    ));
  }

  const fortes = Object.entries(data.correlacoes)
    .filter(([, c]) => c >= 0.5)
    .sort((a, b) => b[1] - a[1]);
  if (fortes.length) {
    blocos.push(`## Correlações ≥ 0.50 (${fortes.length} de ${Object.keys(data.correlacoes).length} pares)\n\n` + tabela(
      ["Par", "Correlação", "Leitura"],
      fortes.map(([par, c]) => [
        par.replace("|", " × "), c.toFixed(2),
        c >= 0.7 ? "mesmo trade" : "co-movimento relevante",
      ]),
    ));
  }

  const tema = Object.entries(data.tema_ia).sort((a, b) => (b[1].ytd ?? -999) - (a[1].ytd ?? -999));
  if (tema.length) {
    blocos.push("## Tema IA\n\n" + tabela(
      ["Ticker", "Grupo", "YTD", "Vol semanal", "Beta", "Driver"],
      tema.map(([t, v]) => [
        t, rotuloGrupo(v.grupo),
        v.ytd != null ? pct(v.ytd, 1) : "—",
        v.vol_sem != null ? `${v.vol_sem.toFixed(1)}%${v.est ? " (est.)" : ""}` : "—",
        v.beta != null ? v.beta.toFixed(2) : "n/d",
        v.driver ?? "—",
      ]),
    ));
  }

  const min52 = Object.entries(data.min52);
  if (min52.length) {
    blocos.push("## Mínimas de 52 semanas\n\n" + tabela(
      ["Ticker", "Preço", "Mín. 52s", "Distância", "Status"],
      min52.map(([t, v]) => [
        t,
        v.preco != null ? `$${v.preco.toFixed(2)}` : "—",
        v.min52 != null ? `$${v.min52.toFixed(2)}` : "—",
        v.preco != null && v.min52 != null && v.min52 > 0
          ? `${(((v.preco - v.min52) / v.min52) * 100).toFixed(1)}%`
          : "—",
        v.status ?? "—",
      ]),
    ));
  }

  const riscos = Object.entries(data.riscos);
  if (riscos.length) {
    blocos.push("## Riscos\n\n" + riscos
      .map(([tema, lista]) => `**${tema}**\n${lista.map((r) => `- ${r}`).join("\n")}`)
      .join("\n\n"));
  }

  return blocos.join("\n\n");
}

export default function RadarPage() {
  const { data, isLoading, error } = useGetRadar();

  return (
    <div className="max-w-6xl mx-auto px-4 py-6 space-y-6">
      <div className="flex items-center gap-2 flex-wrap">
        <RadarIcon className="h-5 w-5 text-primary" />
        <h1 className="text-lg font-mono font-bold text-foreground">Radar IA 2026</h1>
        {data && (
          <Badge variant="outline" className="font-mono text-[10px] text-muted-foreground border-border">
            snapshot {fmtDateBR(data.snapshot)}
          </Badge>
        )}
      </div>
      <p className="font-mono text-xs text-muted-foreground -mt-3">
        Correlações medidas numa janela de 6 meses e calendário de earnings são recoletados sozinhos;
        EVR e move implícito vêm de transcrição manual do OptionSlam e envelhecem — o selo de cada bloco
        diz qual é o caso. Não é recomendação — é infraestrutura de análise; em stress, correlações reais
        sobem acima do medido.
      </p>

      {isLoading && <div className="font-mono text-sm text-muted-foreground">Carregando radar…</div>}
      {error != null && (
        <div className="font-mono text-sm text-red-400">Falha ao carregar o radar. Tenta recarregar a página.</div>
      )}

      {data && (
        <>
          <section className="border border-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border flex items-center gap-2 flex-wrap">
              <CalendarClock className="h-4 w-4 text-muted-foreground" />
              <h2 className="font-mono text-sm font-bold text-foreground">Earnings no radar</h2>
              {/* O calendário deixou de ser digitado à mão. Sem este selo o
                  usuário leria o "snapshot 14/08" do topo e suporia que as
                  datas também são daquele dia -- que era verdade até o
                  overlay de atualizar_earnings.py existir. */}
              {data.earnings_atualizado_em != null ? (
                <Badge variant="outline" className="font-mono text-[10px] border-green-500/40 text-green-400" title="Calendário coletado da Alpha Vantage — datas mais novas que o snapshot embutido">
                  calendário de {fmtDateBR(data.earnings_atualizado_em)}
                </Badge>
              ) : (
                <Badge variant="outline" className="font-mono text-[10px] border-amber-500/40 text-amber-400" title="Overlay de atualizar_earnings.py ainda não aplicado — as datas são as do snapshot embutido">
                  calendário do snapshot
                </Badge>
              )}
            </div>
            <EarningsWatch data={data} />
          </section>

          <section className="border border-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border flex items-center gap-2 flex-wrap mb-3">
              <Link2 className="h-4 w-4 text-muted-foreground" />
              <h2 className="font-mono text-sm font-bold text-foreground">Correlações medidas</h2>
              <span className="font-mono text-[10px] text-muted-foreground">
                janela de 6m até {fmtDateBR(data.correlacoes_janela_fim ?? data.snapshot)} · ≥0.70 = mesmo trade
              </span>
              {data.correlacoes_janela_fim != null && data.correlacoes_janela_fim > data.snapshot && (
                <Badge variant="outline" className="font-mono text-[10px] border-green-500/40 text-green-400" title="Overlay de atualizar_correlacoes.py aplicado — correlações mais novas que o snapshot embutido">
                  atualizado
                </Badge>
              )}
            </div>
            <CorrelacoesFortes data={data} />
          </section>

          <section className="border border-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border flex items-center gap-2 flex-wrap">
              <Flame className="h-4 w-4 text-muted-foreground" />
              <h2 className="font-mono text-sm font-bold text-foreground">Tema IA — YTD, volatilidade e beta</h2>
            </div>
            <TemaIA data={data} />
          </section>

          <section className="border border-border rounded-lg overflow-hidden">
            <div className="px-4 py-3 border-b border-border mb-3">
              <h2 className="font-mono text-sm font-bold text-foreground">Riscos & mínimas de 52 semanas</h2>
            </div>
            <RiscosEMinimas data={data} />
          </section>

          <ExportarRelatorio
            titulo="Radar IA 2026"
            mode="tela_radar"
            tickers={data.portfolio_default}
            construir={() => montarRelatorioRadar(data)}
          />
        </>
      )}
    </div>
  );
}
