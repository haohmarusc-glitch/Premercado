import { useState } from "react";
import {
  useListEntryExitStudies, getListEntryExitStudiesQueryKey,
  useCreateEntryExitStudy, useDeleteEntryExitStudy, useUpdateEntryExitStudy,
  useGetEntryExitStudy, getGetEntryExitStudyQueryKey,
  type EntryExitStudyHistory, type EntryExitStudyNewsItem,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/hooks/use-toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Plus, Trash2, Target, ChevronDown, ChevronUp, History, TrendingDown, Calendar,
  Pencil, Check, X, Newspaper, CheckCircle2, XCircle, ExternalLink, CalendarClock,
} from "lucide-react";

function fmtPct(n: number | null | undefined) {
  if (n == null) return "—";
  return `${(n * 100).toFixed(0)}%`;
}

function fmtUsd(n: number | null | undefined) {
  if (n == null) return "—";
  return `$${n.toFixed(2)}`;
}

// targetDate/calcDate chegam como "YYYY-MM-DD" puro (sem hora) -- formatar
// direto na string em vez de `new Date(iso)` evita o problema clássico de
// fuso (new Date("2026-09-12") é meia-noite UTC, que em BRT já é o dia
// anterior à tarde).
function fmtDateBR(isoDate: string) {
  const [y, m, d] = isoDate.split("-");
  return `${d}/${m}/${y}`;
}

function probColorClass(p: number | null | undefined) {
  if (p == null) return "text-muted-foreground";
  if (p >= 0.5) return "text-green-400";
  if (p >= 0.25) return "text-yellow-400";
  return "text-red-400";
}

function daysUntil(isoDate: string): number {
  const hoje = new Date();
  const alvo = new Date(isoDate + "T00:00:00");
  return Math.max(0, Math.round((alvo.getTime() - hoje.getTime()) / 86400000));
}

// Sparkline da probabilidade dia a dia.
//
// Escala ANCORADA NO ZERO com teto adaptativo, não 0-100% fixo nem
// min-max automático. Os dois extremos falham de formas opostas: com teto
// fixo em 100%, uma série que vive na faixa de 15-30% (normal pra um alvo
// ambicioso) vira uma linha reta que não mostra movimento nenhum; com
// min-max, uma variação de 24% pra 26% preenche o gráfico inteiro e finge
// um salto que não houve. Ancorar no zero mantém a altura proporcional à
// probabilidade de verdade (é o que dá sentido à área preenchida), e o teto
// que acompanha a série mantém a variação visível.
function ProbSparkline({ history }: { history: EntryExitStudyHistory[] }) {
  const pontos = history.filter((h) => h.probReachTarget != null);
  if (pontos.length < 2) return null;

  const W = 300;
  const H = 64;
  const PAD = 4;
  const maxProb = Math.max(...pontos.map((h) => h.probReachTarget!));
  // Piso de 20% no teto pra uma série toda perto de zero não virar ruído
  // amplificado; 1.25× de folga pro pico não encostar na borda de cima.
  const teto = Math.min(1, Math.max(0.2, maxProb * 1.25));

  const x = (i: number) => PAD + (i * (W - PAD * 2)) / (pontos.length - 1);
  const y = (p: number) => H - PAD - (p / teto) * (H - PAD * 2);

  const linha = pontos.map((h, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(h.probReachTarget!).toFixed(1)}`).join(" ");
  const area = `${linha} L ${x(pontos.length - 1).toFixed(1)} ${H - PAD} L ${x(0).toFixed(1)} ${H - PAD} Z`;

  const ultimo = pontos[pontos.length - 1];
  const primeiro = pontos[0];
  const delta = ultimo.probReachTarget! - primeiro.probReachTarget!;

  return (
    <div className="px-4 pb-3 pt-1">
      <div className="flex items-baseline justify-between mb-1.5">
        <span className="text-[11px] font-mono text-muted-foreground uppercase tracking-wide">
          Evolução da probabilidade
        </span>
        <span className={`text-[11px] font-mono ${delta >= 0 ? "text-green-400" : "text-red-400"}`}>
          {delta >= 0 ? "+" : ""}{(delta * 100).toFixed(1)}pp desde {fmtDateBR(primeiro.calcDate)}
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        width="100%"
        height={H}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Probabilidade de ${fmtPct(primeiro.probReachTarget)} em ${fmtDateBR(primeiro.calcDate)} para ${fmtPct(ultimo.probReachTarget)} em ${fmtDateBR(ultimo.calcDate)}.`}
      >
        {/* Fronteira dos 50% ("mais provável que não") -- só quando cabe na
            escala; num alvo ambicioso o teto fica bem abaixo disso. */}
        {teto >= 0.5 && (
          <line x1={PAD} y1={y(0.5)} x2={W - PAD} y2={y(0.5)} className="stroke-border" strokeWidth="1" strokeDasharray="3 3" vectorEffect="non-scaling-stroke" />
        )}
        <path d={area} className="fill-primary/10" />
        <path d={linha} className="stroke-primary" strokeWidth="1.5" fill="none" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
        {/* Marcador do valor de hoje como tick vertical, não círculo: o
            viewBox é esticado na horizontal (preserveAspectRatio="none"),
            e um círculo viraria uma elipse achatada. */}
        <line
          x1={x(pontos.length - 1)} y1={y(ultimo.probReachTarget!)}
          x2={x(pontos.length - 1)} y2={H - PAD}
          className="stroke-primary" strokeWidth="1.5" vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="flex justify-between text-[10px] font-mono text-muted-foreground mt-0.5">
        <span>0%</span>
        <span>{pontos.length} dias · topo da escala {fmtPct(teto)}</span>
        <span>{fmtPct(ultimo.probReachTarget)} hoje</span>
      </div>
    </div>
  );
}

function NewsList({ news }: { news: EntryExitStudyNewsItem[] }) {
  if (!news.length) return null;
  return (
    <div className="px-4 pb-4 pt-1">
      <div className="flex items-center gap-1.5 text-[11px] font-mono text-muted-foreground uppercase tracking-wide mb-2">
        <Newspaper className="h-3 w-3" />
        Notícias do último cálculo
      </div>
      <div className="space-y-2">
        {news.map((n, i) => (
          <div key={i} className="border border-border/50 rounded-md px-3 py-2 bg-secondary/10">
            <div className="flex items-start gap-2">
              <div className="flex-1 min-w-0">
                <div className="text-xs font-medium text-foreground leading-snug">{n.title}</div>
                {n.summary && (
                  <div className="text-[11px] text-muted-foreground mt-1 leading-snug">{n.summary}</div>
                )}
                <div className="flex items-center gap-2 mt-1 text-[10px] font-mono text-muted-foreground">
                  {n.source && <span>{n.source}</span>}
                  {n.relatedTickers && n.relatedTickers.length > 0 && (
                    <span className="text-primary">· {n.relatedTickers.join(" ")}</span>
                  )}
                </div>
              </div>
              {n.url && (
                <a
                  href={n.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-muted-foreground hover:text-primary transition-colors flex-shrink-0 p-0.5"
                  aria-label="Abrir notícia"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                </a>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function StudyDetails({ id }: { id: number }) {
  const { data, isLoading } = useGetEntryExitStudy(id, {
    query: { queryKey: getGetEntryExitStudyQueryKey(id), staleTime: 30_000 },
  });

  if (isLoading) {
    return (
      <div className="px-4 pb-3 pt-1">
        <span className="font-mono text-xs text-muted-foreground animate-pulse">Carregando histórico...</span>
      </div>
    );
  }

  const history = data?.history ?? [];
  const resolution = data?.resolution;
  const ultimasNoticias = [...history].reverse().find((h) => h.news && h.news.length > 0)?.news ?? [];

  if (history.length === 0) {
    return (
      <div className="px-4 pb-3 pt-1 flex items-center gap-2 text-xs font-mono text-muted-foreground">
        <History className="h-3 w-3" />
        Ainda sem histórico diário registrado -- o checker roda 1x por dia.
      </div>
    );
  }

  return (
    <div>
      {resolution && (
        <div className="px-4 pt-3 pb-1">
          <div className={`flex items-center gap-2.5 border rounded-md px-3 py-2.5 ${
            resolution.bateu
              ? "border-green-500/30 bg-green-500/5"
              : "border-red-500/30 bg-red-500/5"
          }`}>
            {resolution.bateu
              ? <CheckCircle2 className="h-4 w-4 text-green-400 flex-shrink-0" />
              : <XCircle className="h-4 w-4 text-red-400 flex-shrink-0" />}
            <div className="text-xs font-mono">
              <span className={resolution.bateu ? "text-green-400 font-bold" : "text-red-400 font-bold"}>
                {resolution.bateu ? "Bateu o alvo" : "Não bateu o alvo"}
              </span>
              <span className="text-muted-foreground">
                {" "}· fechou em {fmtUsd(resolution.finalPrice)} contra alvo de {fmtUsd(resolution.targetPrice)}
                {resolution.probFinal != null && ` · o modelo dava ${fmtPct(resolution.probFinal)}`}
              </span>
            </div>
          </div>
        </div>
      )}

      <ProbSparkline history={history} />

      <div className="px-4 pb-4 pt-1">
        <div className="border border-border/50 rounded-md overflow-hidden overflow-x-auto">
          <table className="w-full text-xs font-mono">
            <thead>
              <tr className="border-b border-border/50 bg-secondary/30">
                <th className="text-left px-3 py-1.5 text-muted-foreground font-normal uppercase tracking-wide">Data</th>
                <th className="text-right px-3 py-1.5 text-muted-foreground font-normal uppercase tracking-wide">Preço</th>
                <th className="text-right px-3 py-1.5 text-muted-foreground font-normal uppercase tracking-wide">Prob. alvo</th>
                <th className="text-right px-3 py-1.5 text-muted-foreground font-normal uppercase tracking-wide">Média baixa 6m</th>
                <th className="text-right px-3 py-1.5 text-muted-foreground font-normal uppercase tracking-wide">Mín. 12m</th>
              </tr>
            </thead>
            <tbody>
              {[...history].reverse().map((h, i) => (
                <tr
                  key={h.id}
                  className={`border-b border-border/30 last:border-0 ${i % 2 === 0 ? "" : "bg-secondary/10"}`}
                >
                  <td className="px-3 py-1.5 text-muted-foreground">{fmtDateBR(h.calcDate)}</td>
                  <td className="px-3 py-1.5 text-right text-foreground">{fmtUsd(h.currentPrice)}</td>
                  <td className={`px-3 py-1.5 text-right font-bold ${probColorClass(h.probReachTarget)}`}>
                    {fmtPct(h.probReachTarget)}
                  </td>
                  <td className="px-3 py-1.5 text-right text-muted-foreground">{fmtUsd(h.avgLow6m)}</td>
                  <td className="px-3 py-1.5 text-right text-muted-foreground">{fmtUsd(h.minLow1y)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <NewsList news={ultimasNoticias} />
    </div>
  );
}

export default function EntryExitStudyPage() {
  const qc = useQueryClient();
  const { toast } = useToast();

  const { data, isLoading } = useListEntryExitStudies({
    query: { queryKey: getListEntryExitStudiesQueryKey(), refetchInterval: 60_000 },
  });
  const createStudy = useCreateEntryExitStudy();
  const deleteStudy = useDeleteEntryExitStudy();
  const updateStudy = useUpdateEntryExitStudy();

  const invalidate = () => qc.invalidateQueries({ queryKey: getListEntryExitStudiesQueryKey() });

  const [ticker, setTicker] = useState("");
  const [targetPrice, setTargetPrice] = useState("");
  const [targetDate, setTargetDate] = useState("");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editPrice, setEditPrice] = useState("");
  const [editDate, setEditDate] = useState("");

  function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    const price = parseFloat(targetPrice);
    if (!ticker.trim() || !targetDate || isNaN(price) || price <= 0) return;

    createStudy.mutate(
      { data: { ticker: ticker.trim().toUpperCase(), targetPrice: price, targetDate } },
      {
        onSuccess: (res) => {
          invalidate();
          setTicker("");
          setTargetPrice("");
          setTargetDate("");
          toast({
            title: "Estudo criado",
            description: `${res.target.ticker}: ${fmtPct(res.calc.probReachTarget)} de chance de bater ${fmtUsd(res.target.targetPrice)} até ${fmtDateBR(res.target.targetDate)}`,
          });
        },
        onError: (err) => toast({
          title: "Erro ao criar estudo",
          description: err instanceof Error ? err.message : String(err),
          variant: "destructive",
        }),
      },
    );
  }

  function startEdit(id: number, price: number, date: string) {
    setEditingId(id);
    setEditPrice(String(price));
    setEditDate(date);
  }

  function handleSaveEdit(id: number) {
    const price = parseFloat(editPrice);
    if (isNaN(price) || price <= 0 || !editDate) return;

    updateStudy.mutate(
      { id, data: { targetPrice: price, targetDate: editDate } },
      {
        onSuccess: (res) => {
          invalidate();
          qc.invalidateQueries({ queryKey: getGetEntryExitStudyQueryKey(id) });
          setEditingId(null);
          toast({
            title: "Estudo atualizado",
            description: `${res.target.ticker}: ${fmtPct(res.calc.probReachTarget)} de chance de bater ${fmtUsd(res.target.targetPrice)} até ${fmtDateBR(res.target.targetDate)}`,
          });
        },
        onError: (err) => toast({
          title: "Erro ao atualizar",
          description: err instanceof Error ? err.message : String(err),
          variant: "destructive",
        }),
      },
    );
  }

  function handleDelete(id: number) {
    deleteStudy.mutate(
      { id },
      {
        onSuccess: () => {
          if (expandedId === id) setExpandedId(null);
          invalidate();
          toast({ title: "Estudo removido" });
        },
        onError: () => toast({ title: "Erro ao remover", variant: "destructive" }),
      },
    );
  }

  const studies = data?.studies ?? [];
  const canSubmit = ticker.trim() && targetDate && targetPrice && !isNaN(parseFloat(targetPrice)) && parseFloat(targetPrice) > 0;

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-3xl font-bold font-mono tracking-tight" data-testid="text-entry-exit-study-title">
          ESTUDO DE ENTRADA E SAÍDA
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Probabilidade de um ticker bater um preço-alvo até uma data, com referência de suporte pra entrada
        </p>
      </div>

      {/* Create form */}
      <div className="border border-border rounded-lg p-6 mb-6 bg-card">
        <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-widest mb-5">
          <Plus className="h-3.5 w-3.5" />
          Novo estudo
        </div>

        <form onSubmit={handleCreate} className="space-y-4">
          <div className="flex flex-wrap items-end gap-4">
            <div>
              <label className="font-mono text-xs uppercase text-muted-foreground block mb-2">Ticker</label>
              <Input
                value={ticker}
                onChange={(e) => setTicker(e.target.value.toUpperCase())}
                placeholder="SMCI, NVDA…"
                className="font-mono bg-secondary border-border w-32"
                data-testid="input-study-ticker"
              />
            </div>

            <div>
              <label className="font-mono text-xs uppercase text-muted-foreground block mb-2">Preço-alvo</label>
              <div className="flex items-center gap-2">
                <span className="font-mono text-sm text-muted-foreground">$</span>
                <Input
                  value={targetPrice}
                  onChange={(e) => setTargetPrice(e.target.value)}
                  type="number"
                  step="0.01"
                  placeholder="45.00"
                  className="font-mono bg-secondary border-border w-28"
                  data-testid="input-study-target-price"
                />
              </div>
            </div>

            <div>
              <label className="font-mono text-xs uppercase text-muted-foreground block mb-2">Data-alvo</label>
              <Input
                value={targetDate}
                onChange={(e) => setTargetDate(e.target.value)}
                type="date"
                className="font-mono bg-secondary border-border"
                data-testid="input-study-target-date"
              />
            </div>

            <Button
              type="submit"
              disabled={!canSubmit || createStudy.isPending}
              className="font-mono font-bold"
              data-testid="btn-create-study"
            >
              <Plus className="h-4 w-4 mr-1" />
              {createStudy.isPending ? "Calculando..." : "Criar Estudo"}
            </Button>
          </div>

          <p className="text-xs font-mono text-muted-foreground border border-dashed border-border rounded px-3 py-2 max-w-2xl">
            Cria 3 alertas de preço automaticamente: saída acima do alvo, entrada abaixo da média das mínimas de
            6 meses, e entrada abaixo da menor mínima de 12 meses. Probabilidade calculada com passeio aleatório
            sem viés direcional (drift zero), ajustado pela volatilidade real do papel e pelo salto histórico de
            earnings quando o balanço cai dentro da janela.
          </p>
        </form>
      </div>

      {/* List */}
      {isLoading && (
        <div className="flex items-center justify-center h-32">
          <span className="font-mono text-sm text-muted-foreground animate-pulse">Carregando estudos...</span>
        </div>
      )}

      {!isLoading && studies.length === 0 && (
        <div className="border border-dashed border-border rounded-lg p-12 text-center">
          <Target className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
          <p className="font-mono text-muted-foreground text-sm">Nenhum estudo em acompanhamento.</p>
          <p className="font-mono text-muted-foreground text-xs mt-1">
            Crie um estudo acima pra começar a acompanhar a probabilidade dia a dia.
          </p>
        </div>
      )}

      {studies.length > 0 && (
        <div className="space-y-2">
          {studies.map(({ target, latest }) => {
            const isExpanded = expandedId === target.id;
            const isEditing = editingId === target.id;
            const dias = daysUntil(target.targetDate);

            // Destaque de earnings: dentro da janela do estudo o balanço
            // engorda a volatilidade usada na probabilidade (volComSalto no
            // cálculo Python), então merece cor de atenção; fora da janela
            // mas a <=10 dias ainda vale mostrar, neutro, como contexto.
            const earnDias = latest?.earningsDate ? daysUntil(latest.earningsDate) : null;
            const earnNaJanela = latest?.earningsDate != null && latest.earningsDate <= target.targetDate;
            const mostraEarnings = earnDias != null && earnDias >= 0 && (earnNaJanela || earnDias <= 10);

            return (
              <div
                key={target.id}
                className="border border-border rounded-lg bg-card transition-colors"
                data-testid={`study-row-${target.id}`}
              >
                <div className="flex items-center gap-4 px-4 py-3 flex-wrap">
                  <Target className="h-4 w-4 flex-shrink-0 text-primary" />

                  <div className="flex-1 min-w-0">
                    {isEditing ? (
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-mono font-bold text-primary">{target.ticker}</span>
                        <div className="flex items-center gap-1">
                          <span className="font-mono text-xs text-muted-foreground">$</span>
                          <Input
                            value={editPrice}
                            onChange={(e) => setEditPrice(e.target.value)}
                            type="number"
                            step="0.01"
                            className="font-mono bg-secondary border-border w-24 h-8 text-sm"
                            data-testid={`edit-price-${target.id}`}
                          />
                        </div>
                        <Input
                          value={editDate}
                          onChange={(e) => setEditDate(e.target.value)}
                          type="date"
                          className="font-mono bg-secondary border-border h-8 text-sm w-40"
                          data-testid={`edit-date-${target.id}`}
                        />
                        <button
                          type="button"
                          onClick={() => handleSaveEdit(target.id)}
                          disabled={updateStudy.isPending}
                          className="text-green-400 hover:text-green-300 transition-colors p-1 disabled:opacity-50"
                          data-testid={`save-edit-${target.id}`}
                          aria-label="Salvar alterações"
                        >
                          <Check className="h-4 w-4" />
                        </button>
                        <button
                          type="button"
                          onClick={() => setEditingId(null)}
                          className="text-muted-foreground hover:text-foreground transition-colors p-1"
                          data-testid={`cancel-edit-${target.id}`}
                          aria-label="Cancelar edição"
                        >
                          <X className="h-4 w-4" />
                        </button>
                      </div>
                    ) : (
                      <>
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-mono font-bold text-primary">{target.ticker}</span>
                          <Badge variant="outline" className="font-mono text-xs border-primary/30 text-primary bg-primary/5">
                            alvo {fmtUsd(target.targetPrice)}
                          </Badge>
                          <Badge variant="outline" className="font-mono text-xs text-muted-foreground border-border">
                            <Calendar className="h-3 w-3 mr-1" />
                            {fmtDateBR(target.targetDate)} ({dias}d)
                          </Badge>
                          {latest && (
                            <Badge
                              variant="outline"
                              className={`font-mono text-xs border-current/30 bg-current/5 ${probColorClass(latest.probReachTarget)}`}
                            >
                              {fmtPct(latest.probReachTarget)} de chance
                            </Badge>
                          )}
                          {mostraEarnings && (
                            <Badge
                              variant="outline"
                              className={`font-mono text-xs ${
                                earnNaJanela
                                  ? "border-yellow-500/40 text-yellow-400 bg-yellow-500/5"
                                  : "text-muted-foreground border-border"
                              }`}
                              title={earnNaJanela
                                ? "Balanço dentro da janela do estudo — o salto de volatilidade de earnings já está embutido na probabilidade"
                                : "Balanço próximo, mas depois da data-alvo — não afeta este cálculo"}
                            >
                              <CalendarClock className="h-3 w-3 mr-1" />
                              earnings {earnDias === 0 ? "hoje" : `em ${earnDias}d`}
                            </Badge>
                          )}
                        </div>
                        {latest && (
                          <div className="flex items-center gap-3 mt-1 text-[11px] font-mono text-muted-foreground flex-wrap">
                            <span>Preço atual: <span className="text-foreground">{fmtUsd(latest.currentPrice)}</span></span>
                            <span className="flex items-center gap-1">
                              <TrendingDown className="h-3 w-3" />
                              entrada: média 6m {fmtUsd(latest.avgLow6m)} · mín. 12m {fmtUsd(latest.minLow1y)}
                            </span>
                          </div>
                        )}
                      </>
                    )}
                  </div>

                  {!isEditing && (
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <button
                        type="button"
                        onClick={() => setExpandedId(isExpanded ? null : target.id)}
                        className="flex items-center gap-1 text-[11px] font-mono text-muted-foreground hover:text-foreground transition-colors px-2 py-1 rounded hover:bg-secondary"
                        data-testid={`history-toggle-${target.id}`}
                        title="Ver histórico diário"
                      >
                        <History className="h-3.5 w-3.5" />
                        {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                      </button>
                      <button
                        type="button"
                        onClick={() => startEdit(target.id, target.targetPrice, target.targetDate)}
                        className="text-muted-foreground hover:text-primary transition-colors p-1"
                        data-testid={`edit-study-${target.id}`}
                        aria-label="Editar alvo"
                        title="Editar preço-alvo e data (mantém o histórico)"
                      >
                        <Pencil className="h-4 w-4" />
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(target.id)}
                        className="text-muted-foreground hover:text-red-400 transition-colors p-1"
                        data-testid={`delete-study-${target.id}`}
                        aria-label="Parar de acompanhar"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </div>
                  )}
                </div>

                {isExpanded && (
                  <div className="border-t border-border/50">
                    <div className="px-4 pt-2 pb-1 flex items-center gap-1.5 text-[11px] font-mono text-muted-foreground uppercase tracking-wide">
                      <History className="h-3 w-3" />
                      Histórico diário
                    </div>
                    <StudyDetails id={target.id} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <p className="text-xs font-mono text-muted-foreground mt-6">
        Recalculado 1x por dia automaticamente · 3 alertas por estudo (saída + 2 entradas) via Alertas de Preço ·
        Quando a data-alvo vence, o resultado (bateu ou não) fica registrado no histórico
      </p>
    </div>
  );
}
