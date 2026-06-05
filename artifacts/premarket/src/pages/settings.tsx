import { useGetSettings, useUpdateSettings, getGetSettingsQueryKey } from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useEffect } from "react";
import { Form, FormControl, FormDescription, FormField, FormItem, FormLabel, FormMessage } from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/hooks/use-toast";
import { Save, Mail, Clock, Tag } from "lucide-react";

const schema = z.object({
  notifyEmail: z.string().email("E-mail inválido"),
  scheduleEnabled: z.boolean(),
  scheduleHour: z.coerce.number().int().min(0).max(23),
  tickers: z.string().min(1, "Adicione pelo menos um ticker"),
});

type FormValues = z.infer<typeof schema>;

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
      tickers: "MU, SMCI",
    },
  });

  useEffect(() => {
    if (settings) {
      form.reset({
        notifyEmail: settings.notifyEmail,
        scheduleEnabled: settings.scheduleEnabled,
        scheduleHour: settings.scheduleHour,
        tickers: settings.tickers.join(", "),
      });
    }
  }, [settings, form]);

  const onSubmit = (values: FormValues) => {
    const tickers = values.tickers
      .split(",")
      .map((t) => t.trim().toUpperCase())
      .filter(Boolean);

    updateSettings.mutate(
      { data: { ...values, tickers } },
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
                  <FormControl>
                    <Input
                      {...field}
                      placeholder="seu@email.com"
                      className="font-mono bg-secondary border-border"
                      data-testid="input-notify-email"
                    />
                  </FormControl>
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
                  <FormControl>
                    <Switch
                      checked={field.value}
                      onCheckedChange={field.onChange}
                      data-testid="switch-schedule-enabled"
                    />
                  </FormControl>
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
                  <FormControl>
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
                  </FormControl>
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
            <div className="flex items-center gap-2 text-xs font-mono text-muted-foreground uppercase tracking-widest mb-4">
              <Tag className="h-3.5 w-3.5" />
              Ativos monitorados
            </div>
            <FormField
              control={form.control}
              name="tickers"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="font-mono text-xs uppercase text-muted-foreground">Tickers (separados por vírgula)</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      placeholder="MU, SMCI, NVDA"
                      className="font-mono bg-secondary border-border"
                      data-testid="input-tickers"
                    />
                  </FormControl>
                  <FormDescription className="text-xs text-muted-foreground">
                    Símbolos da NYSE/NASDAQ. Alterações aplicadas na próxima execução.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
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
