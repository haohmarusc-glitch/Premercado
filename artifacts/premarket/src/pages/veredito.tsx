import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  useListPortfolioPositions,
  useListExitPlan,
  getListExitPlanQueryKey,
  useGetScenarioAlertSettings, getGetScenarioAlertSettingsQueryKey,
  useGetScenarioProgress, getGetScenarioProgressQueryKey,
  useGetAgentStatus, getGetAgentStatusQueryKey,
  useGetLatestReport, getGetLatestReportQueryKey,
} from "@workspace/api-client-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { MarkdownContent } from "@/components/markdown";
import { formatDateTime, daysUntilBRT, formatarDataBRT } from "@/lib/format";
import { cn } from "@/lib/utils";
import { Sparkles, RefreshCw, Target, Activity, Flag, Calendar, Globe, AlertTriangle } from "lucide-react";
import { useTacticalContext, tacticalSignal } from "@/hooks/use-tactical-context";
import { ExportarRelatorio, cabecalho, itens, tabela } from "@/components/exportar-relatorio";
import { VereditoDecisoes } from "@/components/veredito-decisoes";
import { extrairBlocoDoVeredito } from "@/lib/veredito-bloco";

// ─── Veredito do Dia ─────────────────────────────────────────────────────────
// Duas partes independentes: (1) um painel estruturado que agrega dados já
// calculados por outras telas (Cenários, Técnicos, Plano de Saída, Earnings,
// Macro) SEM custo de IA, sempre visível; (2) um botão opcional que dispara o
// agente (modo "veredito", mesma infra dos outros modos manuais como
// "Reavaliar plano") pra gerar uma síntese em texto cruzando as mesmas
// ferramentas + Backtest, com um veredito curto no topo. O botão só roda
// quando o usuário pede -- não é um relatório agendado automaticamente.

interface EarningsItem {
  ticker: string;
  name: string;
  earningsDate: string | null;
  epsEstimate: number | null;
}

interface MacroData {
  fearGreed?: {
    score?: number | null; ratingPt?: string; ratingEn?: string; error?: string;
    /**
     * Distância até a borda de faixa mais próxima, de `agent/sentimento.py`.
     * O Veredito de 26/08/2026 saiu com "54,9 (neutro)" na prosa e "55.2 ·
     * ganância" no painel: os dois rótulos certos, e 0,3 ponto de deriva
     * intradia atravessando a fronteira dos 55 e trocando a palavra.
     */
    faixa?: { rotulo?: string; distanciaDaFronteira?: number | null; naFronteira?: boolean };
  };
  sectors?: { name: string; ticker: string; changePct?: number | null }[];
}

function SectionCard({ icon, title, badge, children }: { icon: React.ReactNode; title: string; badge?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="border border-border rounded-lg bg-card overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-4 py-2.5 border-b border-border bg-secondary/30">
        <div className="flex items-center gap-2 text-[11px] font-mono font-bold uppercase tracking-widest text-muted-foreground">
          {icon} {title}
        </div>
        {badge}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

function EmptyNote({ children }: { children: React.ReactNode }) {
  return <p className="text-xs font-mono text-muted-foreground">{children}</p>;
}

export default function VereditoPage() {
  const qc = useQueryClient();

  // ── Carteira (base pra técnicos/earnings) ──────────────────────────────────
  // Só posições ativas e que não sejam ETF -- GET /portfolio devolve também as
  // zeradas (quantity = 0, pra alimentar a seção "Ações Vendidas" da Carteira)
  // e os ETFs (caixa/renda fixa, sem técnico/notícia relevante pra analisar
  // como uma ação); mesmo filtro usado no backend pra Cenários/Painel de Risco
  // (ver isActivePosition + !isEtf em api-server/src/lib/portfolio-math.ts,
  // routes/scenarios.ts e lib/runner.ts).
  const { data: positions } = useListPortfolioPositions();
  const tickers = useMemo(
    () =>
      Array.from(
        new Set((positions ?? []).filter((p) => !p.isEtf && Number(p.quantity) > 0.00001).map((p) => p.ticker)),
      ).sort(),
    [positions],
  );

  // ── Cenários ─────────────────────────────────────────────────────────────
  const { data: scenarioSettings } = useGetScenarioAlertSettings({
    query: { queryKey: getGetScenarioAlertSettingsQueryKey() },
  });
  const { data: scenarioProgress } = useGetScenarioProgress({
    query: { queryKey: getGetScenarioProgressQueryKey(), enabled: !!scenarioSettings?.configured },
  });
  const ultimoSnapshot = scenarioProgress?.snapshots[scenarioProgress.snapshots.length - 1];
  const resolucaoAtual = scenarioProgress?.resolutions.find((r) => r.dataAlvo === scenarioSettings?.dataAlvo);

  // ── Técnicos (por posição) ──────────────────────────────────────────────────
  const ctx = useTacticalContext(tickers);

  // ── Plano de saída ──────────────────────────────────────────────────────────
  const { data: exitPlan } = useListExitPlan({ query: { queryKey: getListExitPlanQueryKey() } });
  const pendentes = (exitPlan ?? []).filter((i) => i.status === "pending");
  const urgentes = pendentes.filter((i) => daysUntilBRT(i.targetDate) <= 3).sort((a, b) => daysUntilBRT(a.targetDate) - daysUntilBRT(b.targetDate));

  // ── Earnings próximos ────────────────────────────────────────────────────────
  const tickersKey = tickers.join(",");
  const { data: earningsData } = useQuery({
    queryKey: ["veredito-earnings", tickersKey],
    queryFn: async () => {
      const r = await fetch(`/api/earnings?tickers=${encodeURIComponent(tickersKey)}`, { credentials: "include" });
      if (!r.ok) throw new Error("Falha ao buscar earnings");
      return (await r.json()) as EarningsItem[];
    },
    enabled: tickersKey.length > 0,
    staleTime: 5 * 60_000,
  });
  const proximosEarnings = (earningsData ?? [])
    .filter((e) => e.earningsDate && daysUntilBRT(e.earningsDate) >= -1)
    .sort((a, b) => daysUntilBRT(a.earningsDate!) - daysUntilBRT(b.earningsDate!))
    .slice(0, 6);

  // ── Macro ────────────────────────────────────────────────────────────────────
  const { data: macroData } = useQuery({
    queryKey: ["macro"],
    queryFn: async () => {
      const r = await fetch("/api/macro", { credentials: "include" });
      if (!r.ok) throw new Error("Falha ao buscar macro");
      return (await r.json()) as MacroData;
    },
    staleTime: 5 * 60_000,
  });

  // ── Gerar veredito com IA (opcional, sob demanda) ───────────────────────────
  const { data: status } = useGetAgentStatus({
    query: { queryKey: getGetAgentStatusQueryKey(), refetchInterval: 5_000 },
  });
  const isAgentRunning = status?.running ?? false;
  const generate = useMutation({
    mutationFn: () =>
      fetch("/api/agent/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "veredito" }),
      }).then((r) => r.json()),
    onSuccess: () => qc.invalidateQueries({ queryKey: getGetAgentStatusQueryKey() }),
  });
  const wasRunningRef = useRef(isAgentRunning);
  useEffect(() => {
    if (wasRunningRef.current && !isAgentRunning) {
      qc.invalidateQueries({ queryKey: getGetLatestReportQueryKey({ mode: "veredito" }) });
    }
    wasRunningRef.current = isAgentRunning;
  }, [isAgentRunning, qc]);

  const { data: veredito, isLoading: loadingVeredito } = useGetLatestReport(
    { mode: "veredito" },
    { query: { queryKey: getGetLatestReportQueryKey({ mode: "veredito" }), retry: false } },
  );

  // O bloco estruturado sai da prosa e vira tabela. Quando não há bloco legível
  // (ou o modelo não o produziu), `decisoes` é null e a prosa segue INTACTA --
  // inclusive com o fence cru, se houver um ilegível. Ver `veredito-bloco.ts`.
  const { prosa: prosaDoVeredito, decisoes } = useMemo(
    () => extrairBlocoDoVeredito(veredito?.content ?? ""),
    [veredito?.content],
  );

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
            <Sparkles className="h-7 w-7 text-primary" /> VEREDITO DO DIA
          </h1>
          <p className="text-muted-foreground font-mono text-sm mt-2">
            Cenários, Técnicos, Plano de Saída, Earnings e Macro num único lugar
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={() => generate.mutate()}
          disabled={isAgentRunning || generate.isPending}
          title="Chama o agente de IA pra ler todos os painéis abaixo (mais Backtest do ticker mais relevante) e escrever uma síntese com veredito -- só roda quando você pede"
        >
          <RefreshCw className={cn("h-4 w-4 mr-1", isAgentRunning && "animate-spin")} />
          {isAgentRunning ? (status?.currentStep ?? "Gerando...") : "Gerar veredito com IA"}
        </Button>
      </div>

      {/* ── Veredito gerado por IA (opcional) ── */}
      {loadingVeredito ? (
        <Skeleton className="h-32 w-full" />
      ) : veredito ? (
        <Card className="bg-card border-primary/30 shadow-none rounded-sm">
          <CardHeader className="border-b border-border bg-primary/5 pb-4">
            <div className="flex items-center justify-between">
              <CardTitle className="font-mono text-lg flex items-center gap-2">
                <Sparkles className="h-4 w-4 text-primary" /> Veredito gerado por IA
              </CardTitle>
              <span className="text-xs text-muted-foreground font-mono">{formatDateTime(veredito.createdAt)}</span>
            </div>
          </CardHeader>
          <CardContent className="pt-6 space-y-5">
            {decisoes && <VereditoDecisoes decisoes={decisoes} />}
            <MarkdownContent content={prosaDoVeredito} />
          </CardContent>
        </Card>
      ) : (
        <div className="border border-dashed border-border rounded-lg p-6 text-center">
          <p className="text-xs font-mono text-muted-foreground">
            Nenhum veredito gerado ainda. Clique em "Gerar veredito com IA" acima -- isso chama o agente e
            tem custo (mostrado em Gastos com IA), por isso não roda sozinho.
          </p>
        </div>
      )}

      {/* ── Painel estruturado (sem custo, sempre atualizado) ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <SectionCard icon={<Target className="h-4 w-4" />} title="Cenários">
          {!scenarioSettings?.configured ? (
            <EmptyNote>Data-alvo não configurada. Veja a tela Cenários.</EmptyNote>
          ) : resolucaoAtual ? (
            <div className="space-y-1">
              <div className={cn("font-mono text-sm font-bold", resolucaoAtual.bateu ? "text-green-400" : "text-red-400")}>
                {resolucaoAtual.bateu ? "✓ Ciclo confirmado" : "✗ Ciclo não confirmado"}
              </div>
              <p className="text-xs text-muted-foreground font-mono">
                Data-alvo {resolucaoAtual.dataAlvo.split("-").reverse().join("/")} encerrada -- defina uma nova em Cenários.
              </p>
            </div>
          ) : ultimoSnapshot ? (
            <div className="space-y-2">
              <div className="flex items-baseline gap-2">
                <span className={cn("font-mono text-2xl font-bold", ultimoSnapshot.pEmpate >= 0.5 ? "text-green-400" : "text-red-400")}>
                  {(ultimoSnapshot.pEmpate * 100).toFixed(0)}%
                </span>
                <span className="text-xs text-muted-foreground font-mono">chance de empatar até {scenarioSettings.dataAlvo.split("-").reverse().join("/")}</span>
              </div>
              <p className="text-xs text-muted-foreground font-mono">
                {ultimoSnapshot.diasRestantes} dias restantes · {scenarioProgress?.snapshots.length ?? 0} dia(s) acompanhado(s)
              </p>
            </div>
          ) : (
            <EmptyNote>Sem snapshot ainda -- o checker roda de hora em hora.</EmptyNote>
          )}
        </SectionCard>

        <SectionCard icon={<Activity className="h-4 w-4" />} title="Técnicos" badge={<span className="text-[10px] font-mono text-muted-foreground">{tickers.length} posições</span>}>
          {tickers.length === 0 ? (
            <EmptyNote>Sem posições na carteira.</EmptyNote>
          ) : (
            <div className="space-y-1.5">
              {tickers.slice(0, 8).map((t) => {
                const signal = tacticalSignal(t, ctx);
                const tech = ctx.technicalsByTicker.get(t);
                return (
                  <div key={t} className="flex items-center justify-between gap-2 text-xs font-mono">
                    <span className="font-bold">{t}</span>
                    <span className="text-muted-foreground truncate text-right">
                      {tech?.rsi != null && `RSI ${tech.rsi.toFixed(0)} · `}
                      {signal?.label ?? "sem sinal relevante"}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </SectionCard>

        <SectionCard
          icon={<Flag className="h-4 w-4" />}
          title="Plano de Saída"
          badge={urgentes.length > 0 ? <span className="text-[10px] font-mono font-bold text-amber-400 flex items-center gap-1"><AlertTriangle className="h-3 w-3" /> {urgentes.length} no prazo curto</span> : undefined}
        >
          {pendentes.length === 0 ? (
            <EmptyNote>Sem itens pendentes.</EmptyNote>
          ) : (
            <div className="space-y-1.5">
              {(urgentes.length > 0 ? urgentes : pendentes).slice(0, 5).map((item) => {
                const d = daysUntilBRT(item.targetDate);
                return (
                  <div key={item.id} className="flex items-center justify-between gap-2 text-xs font-mono">
                    <span className="font-bold">{item.ticker}</span>
                    <span className="text-muted-foreground truncate">{item.action}</span>
                    <span className={cn(d < 0 ? "text-red-400" : d <= 3 ? "text-amber-400" : "text-muted-foreground")}>
                      {d < 0 ? `vencido ${Math.abs(d)}d` : d === 0 ? "hoje" : `${d}d`}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </SectionCard>

        <SectionCard icon={<Calendar className="h-4 w-4" />} title="Earnings próximos">
          {proximosEarnings.length === 0 ? (
            <EmptyNote>Nenhum balanço próximo na carteira.</EmptyNote>
          ) : (
            <div className="space-y-1.5">
              {proximosEarnings.map((e) => {
                const d = e.earningsDate ? daysUntilBRT(e.earningsDate) : null;
                return (
                  <div key={e.ticker} className="flex items-center justify-between gap-2 text-xs font-mono">
                    <span className="font-bold">{e.ticker}</span>
                    <span className="text-muted-foreground">
                      {e.earningsDate ? formatarDataBRT(e.earningsDate) : "—"}
                    </span>
                    <span className={cn(d != null && d <= 3 ? "text-amber-400" : "text-muted-foreground")}>
                      {d == null ? "" : d === 0 ? "hoje" : d < 0 ? `há ${Math.abs(d)}d` : `em ${d}d`}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </SectionCard>

        <SectionCard icon={<Globe className="h-4 w-4" />} title="Macro">
          {!macroData ? (
            <EmptyNote>Carregando...</EmptyNote>
          ) : (
            <div className="space-y-2">
              {macroData.fearGreed?.score != null ? (
                <div className="space-y-0.5">
                  <div className="flex items-center justify-between text-xs font-mono">
                    <span className="text-muted-foreground">Fear &amp; Greed</span>
                    <span className="font-bold">{macroData.fearGreed.score} · {macroData.fearGreed.ratingPt ?? macroData.fearGreed.ratingEn}</span>
                  </div>
                  {/* Este número é LIVE; o do texto acima é a leitura fixada na
                      hora da geração. Os dois respondem perguntas diferentes, e
                      até 26/08/2026 nenhum dos dois dizia qual. */}
                  <p className="text-[10px] font-mono text-muted-foreground/70 text-right">
                    leitura de agora
                    {macroData.fearGreed.faixa?.naFronteira &&
                      macroData.fearGreed.faixa.distanciaDaFronteira != null && (
                      <>
                        {" · "}
                        <span
                          className="text-yellow-400/80"
                          title="Uma variação intradia menor que isso troca o rótulo. O texto do veredito cita a leitura da hora em que foi gerado, que pode cair do outro lado da faixa."
                        >
                          a {macroData.fearGreed.faixa.distanciaDaFronteira.toFixed(1)} da borda da faixa
                        </span>
                      </>
                    )}
                  </p>
                </div>
              ) : (
                <EmptyNote>Fear &amp; Greed indisponível.</EmptyNote>
              )}
              {(macroData.sectors ?? []).slice(0, 5).map((s) => (
                <div key={s.ticker} className="flex items-center justify-between text-xs font-mono">
                  <span className="text-muted-foreground">{s.name}</span>
                  <span className={cn((s.changePct ?? 0) >= 0 ? "text-green-400" : "text-red-400")}>
                    {s.changePct != null ? `${s.changePct >= 0 ? "+" : ""}${s.changePct.toFixed(2)}%` : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </SectionCard>
      </div>

      {/* O veredito da IA, quando existe, entra INTEIRO no relatório: é texto
          que custou chamada de LLM pra produzir e não se regenera de graça.
          O painel estruturado vai junto porque é o dado que sustenta o texto. */}
      <ExportarRelatorio
        titulo="Veredito do Dia"
        mode="tela_veredito"
        tickers={tickers}
        pronto={Boolean(veredito) || tickers.length > 0}
        construir={() => {
          const blocos: string[] = [cabecalho("Veredito do Dia")];

          if (veredito) {
            blocos.push(`## Veredito gerado por IA\n\n_Gerado em ${formatDateTime(veredito.createdAt)}_\n\n${veredito.content}`);
          }

          if (scenarioSettings?.configured && ultimoSnapshot && !resolucaoAtual) {
            blocos.push("## Cenários\n\n" + itens([
              ["Data-alvo", scenarioSettings.dataAlvo.split("-").reverse().join("/")],
              ["Chance de empatar", `${(ultimoSnapshot.pEmpate * 100).toFixed(0)}%`],
              ["Dias restantes", ultimoSnapshot.diasRestantes],
              ["Dias acompanhados", scenarioProgress?.snapshots.length ?? 0],
            ]));
          } else if (resolucaoAtual) {
            blocos.push("## Cenários\n\n" + itens([
              ["Data-alvo", resolucaoAtual.dataAlvo.split("-").reverse().join("/")],
              ["Resultado", resolucaoAtual.bateu ? "ciclo confirmado" : "ciclo não confirmado"],
            ]));
          }

          if (tickers.length) {
            blocos.push("## Técnicos\n\n" + tabela(
              ["Ticker", "RSI", "Sinal"],
              tickers.map((t) => {
                const tech = ctx.technicalsByTicker.get(t);
                return [t, tech?.rsi != null ? tech.rsi.toFixed(0) : "—", tacticalSignal(t, ctx)?.label ?? "sem sinal relevante"];
              }),
            ));
          }

          if (pendentes.length) {
            blocos.push("## Plano de saída (pendentes)\n\n" + tabela(
              ["Ticker", "Prazo", "Dias", "Ação"],
              pendentes.map((i) => [
                i.ticker, i.targetDate, daysUntilBRT(i.targetDate), i.action ?? "—",
              ]),
            ));
          }

          if (proximosEarnings.length) {
            blocos.push("## Earnings próximos\n\n" + tabela(
              ["Ticker", "Data", "Dias", "EPS estimado"],
              proximosEarnings.map((e) => [
                e.ticker, e.earningsDate ?? "—",
                e.earningsDate ? daysUntilBRT(e.earningsDate) : "—",
                e.epsEstimate != null ? e.epsEstimate.toFixed(2) : "—",
              ]),
            ));
          }

          if (macroData) {
            const linhas: [string, string | number | null][] = [];
            if (macroData.fearGreed?.score != null) {
              linhas.push(["Fear & Greed", `${macroData.fearGreed.score} · ${macroData.fearGreed.ratingPt ?? macroData.fearGreed.ratingEn ?? "—"}`]);
            }
            for (const s of macroData.sectors ?? []) {
              linhas.push([s.name, s.changePct != null ? `${s.changePct >= 0 ? "+" : ""}${s.changePct.toFixed(2)}%` : "—"]);
            }
            if (linhas.length) blocos.push("## Macro\n\n" + itens(linhas));
          }

          return blocos.length > 1 ? blocos.join("\n\n") : null;
        }}
      />
    </div>
  );
}
