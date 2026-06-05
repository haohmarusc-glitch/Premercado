import { useGetSettings, useUpdateSettings, getGetSettingsQueryKey } from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { useForm, Controller } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useEffect, useState, useRef, KeyboardEvent } from "react";
import { Form, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/hooks/use-toast";
import { Save, Mail, Clock, Tag, Plus, X } from "lucide-react";

const schema = z.object({
  notifyEmail: z.string().email("E-mail inválido"),
  scheduleEnabled: z.boolean(),
  scheduleHour: z.coerce.number().int().min(0).max(23),
  tickers: z.array(z.string().min(1)).min(1, "Adicione pelo menos um ticker"),
});

type FormValues = z.infer<typeof schema>;

function TickerEditor({
  value,
  onChange,
}: {
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const [input, setInput] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  function add() {
    const ticker = input.trim().toUpperCase();
    if (!ticker) return;
    if (value.includes(ticker)) {
      setInput("");
      return;
    }
    onChange([...value, ticker]);
    setInput("");
  }

  function remove(t: string) {
    onChange(value.filter((v) => v !== t));
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      add();
    } else if (e.key === "Backspace" && input === "" && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  }

  return (
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
          onChange={(e) => setInput(e.target.value.toUpperCase())}
          onKeyDown={onKey}
          placeholder={value.length === 0 ? "NVDA, AAPL…" : "Adicionar…"}
          className="flex-1 bg-transparent outline-none font-mono text-sm text-foreground placeholder:text-muted-foreground min-w-[80px]"
          data-testid="input-ticker-new"
        />
        {input.trim() && (
          <button
            type="button"
            onClick={add}
            className="text-primary hover:text-primary/80 transition-colors"
            data-testid="btn-add-ticker"
            aria-label="Adicionar ticker"
          >
            <Plus className="h-4 w-4" />
          </button>
        )}
      </div>
    </div>
  );
}

export default function Settings() {
  const queryClient = useQueryClient();
  const { toast } = useToast();
  const { data: settings, isLoading } = useGetSettings({ query: { queryKey: getGetSettingsQueryKey() } });
  const updateSettings = useUpdateSettings();

  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      notifyEmail: "",
      scheduleEnabled: true,
      scheduleHour: 8,
      tickers: ["MU", "SMCI"],
    },
  });

  useEffect(() => {
    if (settings) {
      form.reset({
        notifyEmail: settings.notifyEmail,
        scheduleEnabled: settings.scheduleEnabled,
        scheduleHour: settings.scheduleHour,
        tickers: settings.tickers,
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

            <FormField
              control={form.control}
              name="scheduleHour"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="font-mono text-xs uppercase text-muted-foreground">
                    Horário de disparo (hora local — Brasília)
                  </FormLabel>
                  <div className="flex items-center gap-3">
                    <Input
                      {...field}
                      type="number"
                      min={0}
                      max={23}
                      className="font-mono bg-secondary border-border w-24"
                      data-testid="input-schedule-hour"
                    />
                    <span className="text-sm font-mono text-muted-foreground">:00 BRT</span>
                  </div>
                  <FormDescription className="text-xs text-muted-foreground">
                    Valor entre 0 e 23. Recomendado: 8 (antes do mercado abrir).
                  </FormDescription>
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

          <Button
            type="submit"
            disabled={updateSettings.isPending}
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
