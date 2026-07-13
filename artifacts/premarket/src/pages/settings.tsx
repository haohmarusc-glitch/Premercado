import { useGetSettings, useUpdateSettings, getGetSettingsQueryKey, useGetAgentSpend, getGetAgentSpendQueryKey } from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useEffect, useState, useRef, KeyboardEvent } from "react";
import { Form, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useToast } from "@/hooks/use-toast";
import { Save, Mail, Clock, Tag, Plus, X, Zap, DollarSign } from "lucide-react";

const PROVIDER_OPTIONS: { value: string; label: string }[] = [
  { value: "anthropic", label: "Anthropic (Claude)" },
  { value: "gemini", label: "Gemini" },
  { value: "openrouter", label: "OpenRouter" },
  { value: "openai", label: "OpenAI" },
  { value: "kimi", label: "Kimi" },
];

function providerLabel(key: string): string {
  return PROVIDER_OPTIONS.find((p) => p.value === key)?.label ?? key;
}

function formatProviderGroup(raw: string): string {
  return raw.split(",").filter(Boolean).map(providerLabel).join(" → ") || raw;
}

function formatUsd(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `US$ ${v.toFixed(v < 1 ? 4 : 2)}`;
}

const POPULAR_TICKERS: { symbol: string; name: string }[] = [
  { symbol: "AAPL", name: "Apple" },
  { symbol: "MSFT", name: "Microsoft" },
  { symbol: "NVDA", name: "Nvidia" },
  { symbol: "GOOGL", name: "Alphabet" },
  { symbol: "AMZN", name: "Amazon" },
  { symbol: "META", name: "Meta Platforms" },
  { symbol: "TSLA", name: "Tesla" },
  { symbol: "AMD", name: "AMD" },
  { symbol: "MU", name: "Micron Technology" },
  { symbol: "SMCI", name: "Super Micro Computer" },
  { symbol: "INTC", name: "Intel" },
  { symbol: "QCOM", name: "Qualcomm" },
  { symbol: "AVGO", name: "Broadcom" },
  { symbol: "TSM", name: "TSMC" },
  { symbol: "ASML", name: "ASML Holding" },
  { symbol: "ARM", name: "Arm Holdings" },
  { symbol: "AMAT", name: "Applied Materials" },
  { symbol: "KLAC", name: "KLA Corporation" },
  { symbol: "LRCX", name: "Lam Research" },
  { symbol: "MRVL", name: "Marvell Technology" },
  { symbol: "ORCL", name: "Oracle" },
  { symbol: "CRM", name: "Salesforce" },
  { symbol: "NOW", name: "ServiceNow" },
  { symbol: "ADBE", name: "Adobe" },
  { symbol: "SNOW", name: "Snowflake" },
  { symbol: "PLTR", name: "Palantir" },
  { symbol: "UBER", name: "Uber" },
  { symbol: "NET", name: "Cloudflare" },
  { symbol: "SHOP", name: "Shopify" },
  { symbol: "PYPL", name: "PayPal" },
  { symbol: "SQ", name: "Block" },
  { symbol: "COIN", name: "Coinbase" },
  { symbol: "MSTR", name: "MicroStrategy" },
  { symbol: "JPM", name: "JPMorgan Chase" },
  { symbol: "GS", name: "Goldman Sachs" },
  { symbol: "BAC", name: "Bank of America" },
  { symbol: "V", name: "Visa" },
  { symbol: "MA", name: "Mastercard" },
  { symbol: "BRK.B", name: "Berkshire Hathaway" },
  { symbol: "SPY", name: "S&P 500 ETF" },
  { symbol: "QQQ", name: "Nasdaq ETF" },
];

const schema = z.object({
  notifyEmail: z.string().email("E-mail inválido"),
  scheduleEnabled: z.boolean(),
  scheduleHour: z.coerce.number().int().min(0).max(23),
  scheduleMinute: z.coerce.number().int().min(0).max(59),
  tickers: z.array(z.string().min(1)).min(1, "Adicione pelo menos um ticker"),
  premarketEnabled: z.boolean(),
  premarketIntervalMin: z.coerce.number().int().min(5).max(60),
  premarketWindowStartHour: z.coerce.number().int().min(0).max(23),
  premarketWindowEndHour: z.coerce.number().int().min(0).max(23),
  agentProvider: z.string().nullable(),
  dailyBudgetUsd: z.coerce.number().min(0).nullable(),
  cheapProvider: z.string().min(1),
}).refine(
  (d) => !d.premarketEnabled || d.premarketWindowStartHour < d.premarketWindowEndHour,
  { message: "Horário de início deve ser antes do horário de fim", path: ["premarketWindowEndHour"] },
);

type FormValues = z.infer<typeof schema>;

function buildPremarketTimes(start: number, end: number, interval: number): string[] {
  const times: string[] = [];
  for (let h = start; h < end; h++) {
    for (let m = 0; m < 60; m += interval) {
      times.push(`${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`);
    }
  }
  return times;
}

function TickerEditor({
  value,
  onChange,
}: {
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const [input, setInput] = useState("");
  const [activeIdx, setActiveIdx] = useState(-1);
  const [open, setOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const suggestions = input.trim()
    ? POPULAR_TICKERS.filter(
        (t) =>
          !value.includes(t.symbol) &&
          (t.symbol.startsWith(input.trim().toUpperCase()) ||
            t.name.toLowerCase().includes(input.trim().toLowerCase())),
      ).slice(0, 8)
    : [];

  function addTicker(symbol: string) {
    const ticker = symbol.trim().toUpperCase();
    if (!ticker || value.includes(ticker)) return;
    onChange([...value, ticker]);
    setInput("");
    setOpen(false);
    setActiveIdx(-1);
    inputRef.current?.focus();
  }

  function remove(t: string) {
    onChange(value.filter((v) => v !== t));
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIdx((i) => Math.min(i + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIdx((i) => Math.max(i - 1, -1));
    } else if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      if (activeIdx >= 0 && suggestions[activeIdx]) {
        addTicker(suggestions[activeIdx].symbol);
      } else {
        addTicker(input);
      }
    } else if (e.key === "Escape") {
      setOpen(false);
      setActiveIdx(-1);
    } else if (e.key === "Backspace" && input === "" && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  }

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
        setActiveIdx(-1);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <div ref={containerRef} className="relative">
      <div
        className="min-h-[44px] flex flex-wrap gap-2 items-center p-2 rounded-md border border-border bg-secondary cursor-text"
        onClick={() => inputRef.current?.focus()}
      >
        {value.map((t) => (
          <span
            key={t}
            className="flex items-center gap-1 px-2 py-0.5 rounded bg-primary/20 border border-primary/40 text-primary font-mono text-xs font-bold"
            data-testid={`ticker-tag-${t}`}
          >
            {t}
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); remove(t); }}
              className="hover:text-red-400 transition-colors ml-0.5"
              data-testid={`remove-ticker-${t}`}
              aria-label={`Remover ${t}`}
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
        <div className="flex items-center gap-1 flex-1 min-w-[100px]">
          <input
            ref={inputRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value.toUpperCase());
              setOpen(true);
              setActiveIdx(-1);
            }}
            onFocus={() => input.trim() && setOpen(true)}
            onKeyDown={onKey}
            placeholder={value.length === 0 ? "NVDA, AAPL…" : "Adicionar…"}
            className="flex-1 bg-transparent outline-none font-mono text-sm text-foreground placeholder:text-muted-foreground min-w-[80px]"
            data-testid="input-ticker-new"
            autoComplete="off"
          />
          {input.trim() && (
            <button
              type="button"
              onClick={() => addTicker(input)}
              className="text-primary hover:text-primary/80 transition-colors"
              data-testid="btn-add-ticker"
              aria-label="Adicionar ticker"
            >
              <Plus className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      {/* Autocomplete dropdown */}
      {open && suggestions.length > 0 && (
        <div className="absolute z-50 left-0 right-0 mt-1 border border-border rounded-md bg-card shadow-lg overflow-hidden">
          {suggestions.map((s, idx) => (
            <button
              key={s.symbol}
              type="button"
              onMouseDown={(e) => { e.preventDefault(); addTicker(s.symbol); }}
              onMouseEnter={() => setActiveIdx(idx)}
              className={`w-full flex items-center justify-between px-3 py-2 text-left transition-colors font-mono text-sm ${
                idx === activeIdx ? "bg-primary/20 text-primary" : "hover:bg-secondary text-foreground"
              }`}
              data-testid={`suggestion-${s.symbol}`}
            >
              <span className="font-bold text-xs">{s.symbol}</span>
              <span className="text-xs text-muted-foreground truncate ml-3">{s.name}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function Settings() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { data: settings, isLoading } = useGetSettings({ query: { queryKey: getGetSettingsQueryKey() } });
  const updateSettings = useUpdateSettings();
  const { data: spend } = useGetAgentSpend({ query: { queryKey: getGetAgentSpendQueryKey(), refetchInterval: 30_000 } });

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      notifyEmail: "",
      scheduleEnabled: true,
      scheduleHour: 8,
      scheduleMinute: 30,
      tickers: ["NVDA", "SMCI", "MU", "INTC", "GOOGL", "ARM", "TSLA"],
      premarketEnabled: false,
      premarketIntervalMin: 30,
      premarketWindowStartHour: 6,
      premarketWindowEndHour: 9,
      agentProvider: null,
      dailyBudgetUsd: null,
      cheapProvider: "gemini",
    },
  });

  useEffect(() => {
    // Only reset if the user hasn't started editing yet, to avoid wiping unsaved changes
    if (settings && !form.formState.isDirty) {
      form.reset({
        notifyEmail: settings.notifyEmail,
        scheduleEnabled: settings.scheduleEnabled,
        scheduleHour: settings.scheduleHour,
        scheduleMinute: settings.scheduleMinute,
        tickers: settings.tickers,
        premarketEnabled: settings.premarketEnabled,
        premarketIntervalMin: settings.premarketIntervalMin,
        premarketWindowStartHour: settings.premarketWindowStartHour,
        premarketWindowEndHour: settings.premarketWindowEndHour,
        agentProvider: settings.agentProvider ?? null,
        dailyBudgetUsd: settings.dailyBudgetUsd ?? null,
        cheapProvider: settings.cheapProvider ?? "gemini",
      });
    }
  }, [settings, form]);

  const onSubmit = (values: FormValues) => {
    updateSettings.mutate(
      { data: values },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getGetSettingsQueryKey() });
          toast({ title: "Configurações salvas", description: "As alterações entrarão em vigor imediatamente." });
        },
        onError: () => {
          toast({ title: "Erro ao salvar", description: "Verifique os campos e tente novamente.", variant: "destructive" });
        },
      },
    );
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-muted-foreground font-mono text-sm animate-pulse">Carregando configurações...</div>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-3xl font-bold font-mono tracking-tight" data-testid="text-settings-title">CONFIGURAÇÕES</h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">Notificações, agendamento e ativos monitorados</p>
      </div>

      {/* Gasto do agente em tempo real */}
      {spend && (
        <div className="border border-border rounded-lg p-6 space-y-4 max-w-xl mb-6" data-testid="panel-agent-spend">
          <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-widest">
            <DollarSign className="h-3.5 w-3.5" />
            Gasto do agente — hoje ({spend.date} BRT)
          </div>

          <div className="overflow-x-auto">
            <table className="w-full text-sm font-mono">
              <thead>
                <tr className="text-xs text-muted-foreground uppercase border-b border-border">
                  <th className="text-left py-1.5 pr-2">Provedor</th>
                  <th className="text-right py-1.5 px-2">Custo</th>
                  <th className="text-right py-1.5 px-2">Runs</th>
                  <th className="text-right py-1.5 pl-2">Chamadas</th>
                </tr>
              </thead>
              <tbody>
                {spend.byProvider.length === 0 && (
                  <tr>
                    <td colSpan={4} className="py-2 text-muted-foreground text-xs">Nenhuma execução hoje ainda.</td>
                  </tr>
                )}
                {spend.byProvider.map((p) => (
                  <tr key={p.provider} className="border-b border-border/40 last:border-0">
                    <td className="py-1.5 pr-2">{formatProviderGroup(p.provider)}</td>
                    <td className="py-1.5 px-2 text-right">{formatUsd(p.costUsd)}</td>
                    <td className="py-1.5 px-2 text-right">{p.runs}</td>
                    <td className="py-1.5 pl-2 text-right">{p.calls}</td>
                  </tr>
                ))}
              </tbody>
              <tfoot>
                <tr className="border-t border-border font-bold">
                  <td className="py-1.5 pr-2">Total</td>
                  <td className="py-1.5 px-2 text-right">{formatUsd(spend.totalCostUsd)}</td>
                  <td colSpan={2}></td>
                </tr>
              </tfoot>
            </table>
          </div>

          {spend.dailyBudgetUsd !== null && (
            <div
              className={`rounded-md border p-3 text-xs font-mono ${
                spend.budgetExceeded
                  ? "bg-destructive/10 border-destructive/40 text-destructive"
                  : "bg-primary/5 border-primary/20 text-muted-foreground"
              }`}
              data-testid="text-budget-status"
            >
              Teto diário de <strong>{providerLabel(spend.primaryProvider)}</strong>: {formatUsd(spend.dailyBudgetUsd)}
              {" · "}
              {spend.budgetExceeded
                ? "teto atingido — agente rodando no provedor barato pelo resto do dia."
                : `restante: ${formatUsd(spend.budgetRemainingUsd)}`}
            </div>
          )}
        </div>
      )}

      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-6 max-w-xl">

          {/* E-mail */}
          <div className="border border-border rounded-lg p-6 space-y-4">
            <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-widest mb-4">
              <Mail className="h-3.5 w-3.5" />
              Notificação por E-mail
            </div>
            <FormField
              control={form.control}
              name="notifyEmail"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="font-mono text-xs uppercase text-muted-foreground">Endereço de destino</FormLabel>
                  <Input
                    {...field}
                    placeholder="seu@email.com"
                    className="font-mono bg-secondary border-border"
                    data-testid="input-notify-email"
                  />
                  <FormDescription className="text-xs text-muted-foreground">
                    O relatório completo será enviado aqui após cada análise.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>

          {/* Agendamento */}
          <div className="border border-border rounded-lg p-6 space-y-4">
            <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-widest mb-4">
              <Clock className="h-3.5 w-3.5" />
              Agendamento automático
            </div>

            <FormField
              control={form.control}
              name="scheduleEnabled"
              render={({ field }) => (
                <FormItem className="flex items-center justify-between">
                  <div>
                    <FormLabel className="font-mono text-xs uppercase text-muted-foreground">Ativar disparo automático</FormLabel>
                    <FormDescription className="text-xs text-muted-foreground mt-0.5">
                      Roda o agente automaticamente toda semana (seg–sex).
                    </FormDescription>
                  </div>
                  <Switch
                    checked={field.value}
                    onCheckedChange={field.onChange}
                    data-testid="switch-schedule-enabled"
                  />
                </FormItem>
              )}
            />

            <div>
              <p className="font-mono text-xs uppercase text-muted-foreground mb-2">
                Horário de disparo (Brasília — BRT)
              </p>
              <div className="flex items-center gap-2">
                <FormField
                  control={form.control}
                  name="scheduleHour"
                  render={({ field }) => (
                    <FormItem className="flex-none">
                      <Input
                        {...field}
                        type="number"
                        min={0}
                        max={23}
                        className="font-mono bg-secondary border-border w-20 text-center"
                        data-testid="input-schedule-hour"
                      />
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <span className="text-lg font-mono text-muted-foreground font-bold">:</span>
                <FormField
                  control={form.control}
                  name="scheduleMinute"
                  render={({ field }) => (
                    <FormItem className="flex-none">
                      <Input
                        {...field}
                        type="number"
                        min={0}
                        max={59}
                        className="font-mono bg-secondary border-border w-20 text-center"
                        data-testid="input-schedule-minute"
                      />
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <span className="text-sm font-mono text-muted-foreground">BRT</span>
                <span className="text-xs font-mono text-primary bg-primary/10 border border-primary/30 rounded px-2 py-1">
                  {String(form.watch("scheduleHour") ?? 8).padStart(2, "0")}:{String(form.watch("scheduleMinute") ?? 30).padStart(2, "0")} BRT
                </span>
              </div>
              <p className="text-xs text-muted-foreground mt-1.5">
                Recomendado: <strong>08:30 BRT</strong> — relatório pronto antes da abertura do mercado americano.
              </p>
            </div>
          </div>

          {/* Pré-mercado intradiário */}
          <div className="border border-border rounded-lg p-6 space-y-4">
            <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-widest mb-4">
              <Zap className="h-3.5 w-3.5" />
              Flash Scan — Pré-mercado Intradiário
            </div>

            <FormField
              control={form.control}
              name="premarketEnabled"
              render={({ field }) => (
                <FormItem className="flex items-center justify-between">
                  <div>
                    <FormLabel className="font-mono text-xs uppercase text-muted-foreground">Ativar varredura intradiária</FormLabel>
                    <FormDescription className="text-xs text-muted-foreground mt-0.5">
                      Dispara scans rápidos durante a janela pré-mercado (seg–sex).
                    </FormDescription>
                  </div>
                  <Switch
                    checked={field.value}
                    onCheckedChange={field.onChange}
                    data-testid="switch-premarket-enabled"
                  />
                </FormItem>
              )}
            />

            {form.watch("premarketEnabled") && (
              <div className="space-y-4 pt-2 border-t border-border/50">
                {/* Interval */}
                <FormField
                  control={form.control}
                  name="premarketIntervalMin"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="font-mono text-xs uppercase text-muted-foreground">Intervalo entre scans</FormLabel>
                      <Select
                        value={String(field.value)}
                        onValueChange={(v) => field.onChange(Number(v))}
                      >
                        <SelectTrigger className="w-40 font-mono bg-secondary border-border" data-testid="select-premarket-interval">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="15" className="font-mono">15 minutos</SelectItem>
                          <SelectItem value="20" className="font-mono">20 minutos</SelectItem>
                          <SelectItem value="30" className="font-mono">30 minutos</SelectItem>
                          <SelectItem value="60" className="font-mono">60 minutos</SelectItem>
                        </SelectContent>
                      </Select>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {/* Window */}
                <div>
                  <p className="font-mono text-xs uppercase text-muted-foreground mb-2">
                    Janela de execução (BRT)
                  </p>
                  <div className="flex items-center gap-3">
                    <FormField
                      control={form.control}
                      name="premarketWindowStartHour"
                      render={({ field }) => (
                        <FormItem className="flex-none">
                          <Input
                            {...field}
                            type="number"
                            min={0}
                            max={23}
                            className="font-mono bg-secondary border-border w-20 text-center"
                            data-testid="input-premarket-start"
                          />
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <span className="text-sm font-mono text-muted-foreground">até</span>
                    <FormField
                      control={form.control}
                      name="premarketWindowEndHour"
                      render={({ field }) => (
                        <FormItem className="flex-none">
                          <Input
                            {...field}
                            type="number"
                            min={0}
                            max={23}
                            className="font-mono bg-secondary border-border w-20 text-center"
                            data-testid="input-premarket-end"
                          />
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <span className="text-sm font-mono text-muted-foreground">BRT</span>
                  </div>
                </div>

                {/* Live schedule preview */}
                {(() => {
                  const start = form.watch("premarketWindowStartHour") ?? 6;
                  const end = form.watch("premarketWindowEndHour") ?? 9;
                  const interval = form.watch("premarketIntervalMin") ?? 30;
                  const times = start < end ? buildPremarketTimes(start, end, interval) : [];
                  if (times.length === 0) return null;
                  return (
                    <div className="rounded-md bg-primary/5 border border-primary/20 p-3">
                      <p className="text-xs font-mono text-muted-foreground uppercase mb-2">
                        Horários de disparo (dias úteis)
                      </p>
                      <div className="flex flex-wrap gap-1.5">
                        {times.map((t) => (
                          <span
                            key={t}
                            className="px-2 py-0.5 rounded bg-primary/15 border border-primary/30 text-primary font-mono text-xs"
                          >
                            {t}
                          </span>
                        ))}
                      </div>
                      <p className="text-xs text-muted-foreground mt-2">
                        {times.length} scan{times.length !== 1 ? "s" : ""} por dia útil
                        {" · "}modo <span className="text-primary font-mono">flash</span>{" "}
                        (intradiário, sem relatório completo)
                      </p>
                    </div>
                  );
                })()}
              </div>
            )}
          </div>

          {/* Provedor de LLM & controle de custo */}
          <div className="border border-border rounded-lg p-6 space-y-4">
            <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-widest mb-4">
              <DollarSign className="h-3.5 w-3.5" />
              Provedor de LLM & controle de custo
            </div>

            <FormField
              control={form.control}
              name="agentProvider"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="font-mono text-xs uppercase text-muted-foreground">Provedor primário</FormLabel>
                  <Select
                    value={field.value ?? "auto"}
                    onValueChange={(v) => field.onChange(v === "auto" ? null : v)}
                  >
                    <SelectTrigger className="font-mono bg-secondary border-border" data-testid="select-agent-provider">
                      <span>{field.value ? providerLabel(field.value) : "Automático (padrão — Anthropic primeiro)"}</span>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="auto" className="font-mono">Automático (padrão — Anthropic primeiro)</SelectItem>
                      {PROVIDER_OPTIONS.map((p) => (
                        <SelectItem key={p.value} value={p.value} className="font-mono">{p.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormDescription className="text-xs text-muted-foreground">
                    Provedor usado normalmente, antes de qualquer fallback por erro ou teto de gasto.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="dailyBudgetUsd"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="font-mono text-xs uppercase text-muted-foreground">Teto diário (USD)</FormLabel>
                  <Input
                    type="number"
                    step="0.01"
                    min={0}
                    value={field.value ?? ""}
                    onChange={(e) => field.onChange(e.target.value === "" ? null : Number(e.target.value))}
                    placeholder="Sem teto"
                    className="font-mono bg-secondary border-border"
                    data-testid="input-daily-budget"
                  />
                  <FormDescription className="text-xs text-muted-foreground">
                    Ao atingir esse valor no provedor primário (horário de Brasília), o agente passa a usar o provedor barato pelo resto do dia. Deixe vazio para não ter teto.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="cheapProvider"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="font-mono text-xs uppercase text-muted-foreground">Provedor barato (fallback por teto)</FormLabel>
                  <Select value={field.value} onValueChange={field.onChange}>
                    <SelectTrigger className="font-mono bg-secondary border-border" data-testid="select-cheap-provider">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {PROVIDER_OPTIONS.map((p) => (
                        <SelectItem key={p.value} value={p.value} className="font-mono">{p.label}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
          </div>

          {/* Tickers */}
          <div className="border border-border rounded-lg p-6 space-y-4">
            <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-widest mb-2">
              <Tag className="h-3.5 w-3.5" />
              Ativos monitorados
            </div>

            <Controller
              control={form.control}
              name="tickers"
              render={({ field, fieldState }) => (
                <div className="space-y-2">
                  <label className="font-mono text-xs uppercase text-muted-foreground">Tickers</label>
                  <TickerEditor value={field.value} onChange={field.onChange} />
                  <p className="text-xs text-muted-foreground">
                    Digite o símbolo e pressione{" "}
                    <kbd className="px-1 py-0.5 bg-secondary border border-border rounded text-[10px] font-mono">Enter</kbd>{" "}
                    ou{" "}
                    <kbd className="px-1 py-0.5 bg-secondary border border-border rounded text-[10px] font-mono">,</kbd>{" "}
                    para adicionar. Clique no × para remover.
                  </p>
                  {fieldState.error && (
                    <p className="text-sm font-medium text-destructive">{fieldState.error.message}</p>
                  )}
                </div>
              )}
            />
          </div>

          {form.formState.isDirty && !updateSettings.isPending && (
            <p className="text-xs font-mono text-amber-500 flex items-center gap-1.5">
              <span className="inline-block h-1.5 w-1.5 rounded-full bg-amber-500" />
              Alterações não salvas
            </p>
          )}
          <Button
            type="submit"
            disabled={updateSettings.isPending || !form.formState.isDirty}
            className="font-mono font-bold w-full"
            data-testid="button-save-settings"
          >
            <Save className="h-4 w-4 mr-2" />
            {updateSettings.isPending ? "SALVANDO..." : "SALVAR CONFIGURAÇÕES"}
          </Button>
        </form>
      </Form>
    </div>
  );
}
