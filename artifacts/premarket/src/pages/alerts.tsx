import { useState } from "react";
import {
  useListAlerts,
  getListAlertsQueryKey,
  useCreateAlert,
  useDeleteAlert,
  useToggleAlert,
  useListAlertFirings,
  getListAlertFiringsQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/hooks/use-toast";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Badge } from "@/components/ui/badge";
import {
  Bell, BellOff, Plus, Trash2, TrendingUp, TrendingDown,
  Clock, ChevronDown, ChevronUp, History,
} from "lucide-react";
import { useGetTickerQuotes, getGetTickerQuotesQueryKey } from "@workspace/api-client-react";
import { useAuth } from "@/lib/auth";

const CONDITIONS = [
  { key: "above", label: "Sobe acima de", icon: TrendingUp, color: "text-green-400" },
  { key: "below", label: "Cai abaixo de", icon: TrendingDown, color: "text-red-400" },
];

const INDICATORS = [
  { key: "price", label: "Preço / Variação" },
  { key: "rsi", label: "RSI(14)" },
  { key: "macd", label: "MACD" },
  { key: "sma20", label: "SMA20" },
  { key: "sma50", label: "SMA50" },
] as const;
type IndicatorKey = (typeof INDICATORS)[number]["key"];

export function indicatorBadgeLabel(alert: {
  indicator?: string | null;
  condition: string;
  thresholdPrice?: number | null;
  thresholdPct?: number | null;
  thresholdValue?: number | null;
}) {
  const dir = alert.condition === "above" ? "↑ acima de" : "↓ abaixo de";
  switch (alert.indicator) {
    case "rsi":
      return `RSI ${dir.slice(2)} ${alert.thresholdValue ?? "—"}`;
    case "macd":
      return alert.condition === "above" ? "MACD bullish" : "MACD bearish";
    case "sma20":
      return `preço ${alert.condition === "above" ? "cruzou acima" : "cruzou abaixo"} da SMA20`;
    case "sma50":
      return `preço ${alert.condition === "above" ? "cruzou acima" : "cruzou abaixo"} da SMA50`;
    default:
      return `${dir} ${alert.thresholdPrice != null ? `$${alert.thresholdPrice.toFixed(2)}` : `${(alert.thresholdPct ?? 0) > 0 ? "+" : ""}${alert.thresholdPct ?? 0}%`}`;
  }
}

function fmtDateTime(iso: string | null | undefined) {
  if (!iso) return null;
  return new Date(iso).toLocaleString("pt-BR", {
    day: "2-digit", month: "2-digit", year: "2-digit",
    hour: "2-digit", minute: "2-digit",
    timeZone: "America/Sao_Paulo",
  });
}

function fmtPct(n: number) {
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function FiringHistory({ alertId }: { alertId: number }) {
  const { data: firings, isLoading } = useListAlertFirings(alertId, {
    query: {
      queryKey: getListAlertFiringsQueryKey(alertId),
      staleTime: 30_000,
    },
  });

  if (isLoading) {
    return (
      <div className="px-4 pb-3 pt-1">
        <span className="font-mono text-xs text-muted-foreground animate-pulse">Carregando histórico...</span>
      </div>
    );
  }

  if (!firings || firings.length === 0) {
    return (
      <div className="px-4 pb-3 pt-1 flex items-center gap-2 text-xs font-mono text-muted-foreground">
        <History className="h-3 w-3" />
        Nenhum disparo registrado ainda.
      </div>
    );
  }

  return (
    <div className="px-4 pb-4 pt-1">
      <div className="border border-border/50 rounded-md overflow-hidden">
        <table className="w-full text-xs font-mono">
          <thead>
            <tr className="border-b border-border/50 bg-secondary/30">
              <th className="text-left px-3 py-1.5 text-muted-foreground font-normal uppercase tracking-wide">Data/Hora</th>
              <th className="text-right px-3 py-1.5 text-muted-foreground font-normal uppercase tracking-wide">Variação</th>
              <th className="text-right px-3 py-1.5 text-muted-foreground font-normal uppercase tracking-wide">Preço</th>
            </tr>
          </thead>
          <tbody>
            {firings.map((f, i) => (
              <tr
                key={f.id}
                className={`border-b border-border/30 last:border-0 ${i % 2 === 0 ? "" : "bg-secondary/10"}`}
              >
                <td className="px-3 py-1.5 text-muted-foreground">{fmtDateTime(f.firedAt)}</td>
                <td className={`px-3 py-1.5 text-right font-bold ${(f.changePctAtFiring ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                  {f.changePctAtFiring != null ? fmtPct(f.changePctAtFiring) : "—"}
                </td>
                <td className="px-3 py-1.5 text-right text-foreground">
                  {f.priceAtFiring != null ? `$${f.priceAtFiring.toFixed(2)}` : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function Alerts() {
  const qc = useQueryClient();
  const { toast } = useToast();
  const { user } = useAuth();

  const { data: alerts, isLoading } = useListAlerts({
    query: { queryKey: getListAlertsQueryKey(), refetchInterval: 30_000 },
  });
  const { data: quotes } = useGetTickerQuotes({
    query: { queryKey: getGetTickerQuotesQueryKey(), staleTime: 55_000 },
  });

  const createAlert = useCreateAlert();
  const deleteAlert = useDeleteAlert();
  const toggleAlert = useToggleAlert();

  const invalidate = () => qc.invalidateQueries({ queryKey: getListAlertsQueryKey() });

  const [symbol, setSymbol] = useState("");
  const [condition, setCondition] = useState<"above" | "below">("below");
  const [thresholdPct, setThresholdPct] = useState("");
  const [thresholdPrice, setThresholdPrice] = useState("");
  const [thresholdValue, setThresholdValue] = useState("");
  const [alertMode, setAlertMode] = useState<"pct" | "price">("price");
  const [indicator, setIndicator] = useState<IndicatorKey>("price");
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [notifyEmail, setNotifyEmail] = useState("");
  const effectiveNotifyEmail = notifyEmail || user?.email || "";

  const availableSymbols = quotes?.map((q) => q.symbol) ?? [];

  function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    if (!symbol) return;

    let desc: string;
    let body: { symbol: string; indicator: IndicatorKey; condition: "above" | "below"; thresholdPct: number | null; thresholdPrice: number | null; thresholdValue: number | null; notifyEmail?: string };
    const notifyEmailField = effectiveNotifyEmail.trim() ? { notifyEmail: effectiveNotifyEmail.trim() } : {};

    if (indicator === "price") {
      const pct = alertMode === "pct" ? parseFloat(thresholdPct) : undefined;
      const price = alertMode === "price" ? parseFloat(thresholdPrice) : undefined;
      if (alertMode === "pct" && (pct == null || isNaN(pct))) return;
      if (alertMode === "price" && (price == null || isNaN(price))) return;
      desc = alertMode === "price"
        ? `${symbol} ${condition === "above" ? "acima de" : "abaixo de"} $${price}`
        : `${symbol} ${condition} ${pct! > 0 ? "+" : ""}${pct}%`;
      body = { symbol, indicator, condition, thresholdPct: pct ?? null, thresholdPrice: price ?? null, thresholdValue: null, ...notifyEmailField };
    } else if (indicator === "rsi") {
      const val = parseFloat(thresholdValue);
      if (isNaN(val)) return;
      desc = `${symbol} RSI(14) ${condition === "above" ? "acima de" : "abaixo de"} ${val}`;
      body = { symbol, indicator, condition, thresholdPct: null, thresholdPrice: null, thresholdValue: val, ...notifyEmailField };
    } else if (indicator === "macd") {
      desc = `${symbol} MACD virar ${condition === "above" ? "bullish" : "bearish"}`;
      body = { symbol, indicator, condition, thresholdPct: null, thresholdPrice: null, thresholdValue: null, ...notifyEmailField };
    } else {
      const period = indicator === "sma20" ? "SMA20" : "SMA50";
      desc = `${symbol} preço cruzar ${condition === "above" ? "acima" : "abaixo"} da ${period}`;
      body = { symbol, indicator, condition, thresholdPct: null, thresholdPrice: null, thresholdValue: null, ...notifyEmailField };
    }

    createAlert.mutate(
      { data: body },
      {
        onSuccess: () => {
          invalidate();
          setSymbol("");
          setThresholdPct("");
          setThresholdPrice("");
          setThresholdValue("");
          toast({ title: "Alerta criado", description: desc });
        },
        onError: () => toast({ title: "Erro ao criar alerta", variant: "destructive" }),
      },
    );
  }

  const canSubmit = symbol && (
    indicator === "price" ? (alertMode === "pct" ? !!thresholdPct : !!thresholdPrice)
    : indicator === "rsi" ? !!thresholdValue
    : true // macd/sma20/sma50 só precisam da condição
  );

  function handleDelete(id: number) {
    deleteAlert.mutate(
      { id },
      {
        onSuccess: () => {
          if (expandedId === id) setExpandedId(null);
          invalidate();
          toast({ title: "Alerta removido" });
        },
        onError: () => toast({ title: "Erro ao remover", variant: "destructive" }),
      },
    );
  }

  function handleToggle(id: number, enabled: boolean) {
    toggleAlert.mutate({ id, data: { enabled } }, { onSuccess: () => invalidate() });
  }

  const activeCount = alerts?.filter((a) => a.enabled).length ?? 0;

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-3xl font-bold font-mono tracking-tight" data-testid="text-alerts-title">
          ALERTAS DE PREÇO
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Receba um e-mail quando o preço cruzar o threshold configurado
        </p>
      </div>

      {alerts && alerts.length > 0 && (
        <div className="grid grid-cols-2 gap-4 mb-8">
          <div className="border border-border rounded-lg p-4 bg-card">
            <div className="text-xs font-mono text-muted-foreground uppercase mb-1">Total</div>
            <div className="text-2xl font-bold font-mono">{alerts.length}</div>
          </div>
          <div className="border border-primary/30 rounded-lg p-4 bg-primary/5">
            <div className="text-xs font-mono text-muted-foreground uppercase mb-1">Ativos</div>
            <div className="text-2xl font-bold font-mono text-primary">{activeCount}</div>
          </div>
        </div>
      )}

      {/* Create form */}
      <div className="border border-border rounded-lg p-6 mb-6 bg-card">
        <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-widest mb-5">
          <Plus className="h-3.5 w-3.5" />
          Novo alerta
        </div>

        <form onSubmit={handleCreate} className="space-y-4">
          <div>
            <label className="font-mono text-xs uppercase text-muted-foreground block mb-2">Ticker</label>
            {availableSymbols.length > 0 ? (
              <div className="flex flex-wrap gap-2">
                {availableSymbols.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => setSymbol(s)}
                    className={`px-3 py-1.5 rounded font-mono text-sm font-bold border transition-colors ${
                      symbol === s
                        ? "bg-primary text-primary-foreground border-primary"
                        : "border-border text-muted-foreground hover:border-primary/50 hover:text-foreground"
                    }`}
                    data-testid={`select-symbol-${s}`}
                  >
                    {s}
                  </button>
                ))}
                <Input
                  value={availableSymbols.includes(symbol) ? "" : symbol}
                  onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                  placeholder="Outro…"
                  className="font-mono bg-secondary border-border w-28 h-9 text-sm"
                  data-testid="input-symbol-custom"
                />
              </div>
            ) : (
              <Input
                value={symbol}
                onChange={(e) => setSymbol(e.target.value.toUpperCase())}
                placeholder="MU, NVDA…"
                className="font-mono bg-secondary border-border w-40"
                data-testid="input-symbol"
              />
            )}
          </div>

          <div>
            <label className="font-mono text-xs uppercase text-muted-foreground block mb-2">Indicador</label>
            <div className="flex flex-wrap gap-2">
              {INDICATORS.map((ind) => (
                <button
                  key={ind.key}
                  type="button"
                  onClick={() => setIndicator(ind.key)}
                  className={`px-3 py-1.5 rounded border font-mono text-sm transition-colors ${
                    indicator === ind.key
                      ? "bg-primary text-primary-foreground border-primary"
                      : "border-border text-muted-foreground hover:border-primary/50"
                  }`}
                  data-testid={`indicator-${ind.key}`}
                >
                  {ind.label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <label className="font-mono text-xs uppercase text-muted-foreground block mb-2">E-mail de notificação</label>
            <Input
              value={effectiveNotifyEmail}
              onChange={(e) => setNotifyEmail(e.target.value)}
              type="email"
              placeholder={user?.email ?? "seu@email.com"}
              className="font-mono bg-secondary border-border w-64"
              data-testid="input-notify-email"
            />
          </div>

          <div className="flex flex-wrap items-end gap-4">
            <div>
              <label className="font-mono text-xs uppercase text-muted-foreground block mb-2">
                {indicator === "macd" ? "Condição (cruzamento)" : indicator === "sma20" || indicator === "sma50" ? "Condição (preço vs média)" : "Condição"}
              </label>
              <div className="flex gap-2">
                {CONDITIONS.map((c) => (
                  <button
                    key={c.key}
                    type="button"
                    onClick={() => setCondition(c.key as "above" | "below")}
                    className={`flex items-center gap-1.5 px-3 py-2 rounded border font-mono text-sm transition-colors ${
                      condition === c.key
                        ? "bg-primary text-primary-foreground border-primary"
                        : "border-border text-muted-foreground hover:border-primary/50"
                    }`}
                    data-testid={`condition-${c.key}`}
                  >
                    <c.icon className="h-3.5 w-3.5" />
                    {c.label}
                  </button>
                ))}
              </div>
            </div>

            {indicator === "price" && (
              <div>
                <label className="font-mono text-xs uppercase text-muted-foreground block mb-2">Tipo de alerta</label>
                <div className="flex gap-2 mb-3">
                  <button
                    type="button"
                    onClick={() => setAlertMode("price")}
                    className={`px-3 py-1.5 rounded border font-mono text-sm transition-colors ${
                      alertMode === "price"
                        ? "bg-primary text-primary-foreground border-primary"
                        : "border-border text-muted-foreground hover:border-primary/50"
                    }`}
                  >
                    $ Preço absoluto
                  </button>
                  <button
                    type="button"
                    onClick={() => setAlertMode("pct")}
                    className={`px-3 py-1.5 rounded border font-mono text-sm transition-colors ${
                      alertMode === "pct"
                        ? "bg-primary text-primary-foreground border-primary"
                        : "border-border text-muted-foreground hover:border-primary/50"
                    }`}
                  >
                    % Variação diária
                  </button>
                </div>

                {alertMode === "price" ? (
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-sm text-muted-foreground">$</span>
                    <Input
                      value={thresholdPrice}
                      onChange={(e) => setThresholdPrice(e.target.value)}
                      type="number"
                      step="0.01"
                      placeholder={condition === "below" ? "865.00" : "1000.00"}
                      className="font-mono bg-secondary border-border w-32"
                      data-testid="input-threshold-price"
                    />
                    <span className="font-mono text-sm text-muted-foreground">USD</span>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    <Input
                      value={thresholdPct}
                      onChange={(e) => setThresholdPct(e.target.value)}
                      type="number"
                      step="0.5"
                      placeholder={condition === "below" ? "-5.0" : "3.0"}
                      className="font-mono bg-secondary border-border w-28"
                      data-testid="input-threshold-pct"
                    />
                    <span className="font-mono text-sm text-muted-foreground">% no dia</span>
                  </div>
                )}
              </div>
            )}

            {indicator === "rsi" && (
              <div>
                <label className="font-mono text-xs uppercase text-muted-foreground block mb-2">Nível de RSI</label>
                <div className="flex items-center gap-2">
                  <Input
                    value={thresholdValue}
                    onChange={(e) => setThresholdValue(e.target.value)}
                    type="number"
                    step="1"
                    min="0"
                    max="100"
                    placeholder={condition === "below" ? "30" : "70"}
                    className="font-mono bg-secondary border-border w-24"
                    data-testid="input-threshold-value"
                  />
                </div>
              </div>
            )}

            {(indicator === "macd" || indicator === "sma20" || indicator === "sma50") && (
              <div className="text-xs font-mono text-muted-foreground border border-dashed border-border rounded px-3 py-2 max-w-xs">
                {indicator === "macd"
                  ? "Sem valor extra: dispara quando o histograma do MACD virar bullish (acima) ou bearish (abaixo)."
                  : `Sem valor extra: dispara quando o preço cruzar ${condition === "above" ? "acima" : "abaixo"} da média móvel de ${indicator === "sma20" ? "20" : "50"} dias.`}
              </div>
            )}

            <Button
              type="submit"
              disabled={!canSubmit || createAlert.isPending}
              className="font-mono font-bold"
              data-testid="btn-create-alert"
            >
              <Plus className="h-4 w-4 mr-1" />
              Criar Alerta
            </Button>
          </div>

          {symbol && indicator === "price" && alertMode === "price" && thresholdPrice && !isNaN(parseFloat(thresholdPrice)) && (
            <p className="text-xs font-mono text-muted-foreground border border-dashed border-border rounded px-3 py-2">
              Enviar e-mail quando <span className="text-primary font-bold">{symbol}</span>{" "}
              {condition === "above" ? "subir acima de" : "cair abaixo de"}{" "}
              <span className="text-primary font-bold">${parseFloat(thresholdPrice).toFixed(2)}</span>.{" "}
              Cooldown: 4h.
            </p>
          )}
          {symbol && indicator === "price" && alertMode === "pct" && thresholdPct && !isNaN(parseFloat(thresholdPct)) && (
            <p className="text-xs font-mono text-muted-foreground border border-dashed border-border rounded px-3 py-2">
              Enviar e-mail quando <span className="text-primary font-bold">{symbol}</span>{" "}
              {condition === "above" ? "subir acima de" : "cair abaixo de"}{" "}
              <span className="text-primary font-bold">
                {parseFloat(thresholdPct) > 0 ? "+" : ""}{thresholdPct}%
              </span>{" "}
              em relação ao fechamento anterior. Cooldown: 4h.
            </p>
          )}
          {symbol && indicator === "rsi" && thresholdValue && !isNaN(parseFloat(thresholdValue)) && (
            <p className="text-xs font-mono text-muted-foreground border border-dashed border-border rounded px-3 py-2">
              Enviar e-mail quando o <span className="text-primary font-bold">RSI(14)</span> de{" "}
              <span className="text-primary font-bold">{symbol}</span> ficar{" "}
              {condition === "above" ? "acima de" : "abaixo de"}{" "}
              <span className="text-primary font-bold">{thresholdValue}</span>. Cooldown: 4h.
            </p>
          )}
        </form>
      </div>

      {/* Alerts list */}
      {isLoading && (
        <div className="flex items-center justify-center h-32">
          <span className="font-mono text-sm text-muted-foreground animate-pulse">Carregando alertas...</span>
        </div>
      )}

      {!isLoading && (!alerts || alerts.length === 0) && (
        <div className="border border-dashed border-border rounded-lg p-12 text-center">
          <Bell className="h-8 w-8 text-muted-foreground mx-auto mb-3" />
          <p className="font-mono text-muted-foreground text-sm">Nenhum alerta configurado.</p>
          <p className="font-mono text-muted-foreground text-xs mt-1">
            Crie um alerta acima para receber notificações.
          </p>
        </div>
      )}

      {alerts && alerts.length > 0 && (
        <div className="space-y-2">
          {alerts.map((alert) => {
            const cond = CONDITIONS.find((c) => c.key === alert.condition);
            const Icon = cond?.icon ?? Bell;
            const lastFired = fmtDateTime(alert.lastTriggeredAt);
            const isExpanded = expandedId === alert.id;

            return (
              <div
                key={alert.id}
                className={`border rounded-lg transition-colors ${
                  alert.enabled ? "border-border bg-card" : "border-border/40 bg-secondary/10 opacity-60"
                }`}
                data-testid={`alert-row-${alert.id}`}
              >
                {/* Alert header row */}
                <div className="flex items-center gap-4 px-4 py-3">
                  <Icon className={`h-4 w-4 flex-shrink-0 ${cond?.color ?? "text-muted-foreground"}`} />

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono font-bold text-primary">{alert.symbol}</span>
                      <Badge
                        variant="outline"
                        className={`font-mono text-xs ${
                          alert.condition === "above"
                            ? "border-green-500/30 text-green-400 bg-green-500/5"
                            : "border-red-500/30 text-red-400 bg-red-500/5"
                        }`}
                      >
                        {indicatorBadgeLabel(alert)}
                      </Badge>
                      {alert.indicator && alert.indicator !== "price" && (
                        <Badge variant="outline" className="font-mono text-xs text-muted-foreground border-border uppercase">
                          {alert.indicator}
                        </Badge>
                      )}
                      {!alert.enabled && (
                        <Badge variant="outline" className="font-mono text-xs text-muted-foreground border-border">
                          pausado
                        </Badge>
                      )}
                    </div>
                    {lastFired && (
                      <div className="flex items-center gap-1 mt-0.5 text-[11px] font-mono text-muted-foreground">
                        <Clock className="h-3 w-3" />
                        Último disparo: {lastFired}
                      </div>
                    )}
                  </div>

                  <div className="flex items-center gap-2 flex-shrink-0">
                    {/* History toggle */}
                    <button
                      type="button"
                      onClick={() => setExpandedId(isExpanded ? null : alert.id)}
                      className="flex items-center gap-1 text-[11px] font-mono text-muted-foreground hover:text-foreground transition-colors px-2 py-1 rounded hover:bg-secondary"
                      data-testid={`history-toggle-${alert.id}`}
                      title="Ver histórico de disparos"
                    >
                      <History className="h-3.5 w-3.5" />
                      {isExpanded ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}
                    </button>

                    <Switch
                      checked={alert.enabled}
                      onCheckedChange={(v) => handleToggle(alert.id, v)}
                      data-testid={`toggle-alert-${alert.id}`}
                      aria-label="Ativar/desativar alerta"
                    />
                    <button
                      type="button"
                      onClick={() => handleDelete(alert.id)}
                      className="text-muted-foreground hover:text-red-400 transition-colors p-1"
                      data-testid={`delete-alert-${alert.id}`}
                      aria-label="Remover alerta"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>

                {/* Expandable firing history */}
                {isExpanded && (
                  <div className="border-t border-border/50">
                    <div className="px-4 pt-2 pb-1 flex items-center gap-1.5 text-[11px] font-mono text-muted-foreground uppercase tracking-wide">
                      <History className="h-3 w-3" />
                      Histórico de disparos (últimos 20)
                    </div>
                    <FiringHistory alertId={alert.id} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <p className="text-xs font-mono text-muted-foreground mt-6">
        Verificação a cada 5 minutos · Cooldown de 4h por alerta · Destinatário: e-mail configurado em Settings
      </p>
    </div>
  );
}
