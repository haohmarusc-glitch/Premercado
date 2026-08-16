import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileDown, Mail, Check } from "lucide-react";

// Modos aceitos por POST /reports/export. Espelha MODOS_EXPORTAVEIS em
// routes/reports.ts — os dois têm que andar juntos, senão a tela manda um modo
// que o servidor rejeita com 400.
export const ROTULO_POR_MODO_EXPORTADO: Record<string, string> = {
  tela_backtest: "backtest",
  tela_radar: "radar",
  tela_cenarios: "cenários",
  tela_veredito: "veredito",
  tela_earnings_reaction: "earnings",
  tela_entry_exit_study: "estudo",
  tela_sector_ai: "setor ia",
  tela_sector_coal: "setor carvão",
  tela_analise_rapida: "análise rápida",
};

interface RespostaExport {
  id: number;
  date: string;
  enviado: boolean;
  email?: string;
  erroEnvio?: string;
}

interface Props {
  /** Título do relatório — vira o assunto do e-mail. */
  titulo: string;
  /** Modo persistido em reports.mode. Precisa estar em ROTULO_POR_MODO_EXPORTADO. */
  mode: keyof typeof ROTULO_POR_MODO_EXPORTADO | string;
  /** Tickers do relatório, se a tela tiver esse conceito. */
  tickers?: string[];
  /**
   * Monta o markdown. Só é chamada no clique — é função, não string, porque
   * montar o relatório do Radar a cada render da tela custa caro à toa.
   * Devolver null aborta a exportação com aviso na tela.
   */
  construir: () => string | null;
  /**
   * A tela já tem dados? Controla só o estado dos botões. Separado de
   * `construir` de propósito: se o "tem dados?" também chamasse `construir`,
   * o relatório seria montado a cada render — exatamente o custo que a
   * assinatura em função existe pra evitar.
   */
  pronto?: boolean;
}

export function ExportarRelatorio({ titulo, mode, tickers, construir, pronto = true }: Props) {
  const queryClient = useQueryClient();
  const [aviso, setAviso] = useState<{ texto: string; tom: "ok" | "erro" } | null>(null);

  const exportar = useMutation({
    mutationFn: async (enviar: boolean) => {
      const markdown = construir();
      if (!markdown) throw new Error("Nada para exportar — rode a análise primeiro.");
      const r = await fetch("/api/reports/export", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ titulo, markdown, mode, tickers: tickers ?? [], enviar }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Falha ao exportar");
      return data as RespostaExport;
    },
    onSuccess: (data) => {
      // O relatório entra na tabela que a tela Histórico lê — sem invalidar,
      // ele só aparece lá no próximo refetch.
      queryClient.invalidateQueries({ queryKey: ["/api/reports"] });
      if (data.erroEnvio) {
        setAviso({ texto: data.erroEnvio, tom: "erro" });
      } else if (data.enviado) {
        setAviso({ texto: `Enviado para ${data.email} e salvo no Histórico.`, tom: "ok" });
      } else {
        setAviso({ texto: "Salvo no Histórico.", tom: "ok" });
      }
    },
    onError: (err) => setAviso({ texto: String(err instanceof Error ? err.message : err), tom: "erro" }),
  });

  const temDados = pronto;
  const ocupado = exportar.isPending;

  return (
    <div className="flex flex-wrap items-center gap-3">
      <button
        type="button"
        onClick={() => { setAviso(null); exportar.mutate(false); }}
        disabled={ocupado || !temDados}
        title={temDados ? "Salva o retrato atual da tela no Histórico" : "Rode a análise antes de exportar"}
        className="px-4 py-2 border border-border rounded font-mono text-xs font-bold text-foreground disabled:opacity-50 flex items-center gap-2 hover:border-primary/50"
      >
        <FileDown className="h-3.5 w-3.5" /> Salvar relatório
      </button>
      <button
        type="button"
        onClick={() => { setAviso(null); exportar.mutate(true); }}
        disabled={ocupado || !temDados}
        title={temDados ? "Salva no Histórico e envia para o e-mail da sua conta" : "Rode a análise antes de exportar"}
        className="px-4 py-2 border border-border rounded font-mono text-xs font-bold text-foreground disabled:opacity-50 flex items-center gap-2 hover:border-primary/50"
      >
        <Mail className="h-3.5 w-3.5" /> Enviar por e-mail
      </button>
      {ocupado && (
        <span className="font-mono text-xs text-muted-foreground flex items-center gap-2">
          <span className="animate-spin inline-block w-3 h-3 border-2 border-border border-t-foreground rounded-full" />
          Exportando...
        </span>
      )}
      {!ocupado && aviso && (
        <span className={`font-mono text-xs flex items-center gap-1.5 ${aviso.tom === "ok" ? "text-green-400" : "text-yellow-400"}`}>
          {aviso.tom === "ok" && <Check className="h-3.5 w-3.5" />}
          {aviso.texto}
        </span>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers de markdown — usados pelos adaptadores de cada tela.
// ---------------------------------------------------------------------------

/** Cabeçalho padrão: título, data/hora do retrato e uma linha de contexto. */
export function cabecalho(titulo: string, contexto?: string): string {
  const agora = new Date().toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo" });
  return `# ${titulo}\n\nRetrato de ${agora} (BRT)${contexto ? `\n${contexto}` : ""}\n`;
}

/**
 * Tabela markdown. Células com `|` são escapadas — nome de empresa e texto de
 * sinal chegam aqui sem passar por sanitização, e um `|` solto quebra o
 * alinhamento de toda a tabela dali pra baixo.
 */
export function tabela(colunas: string[], linhas: (string | number | null | undefined)[][]): string {
  const celula = (v: string | number | null | undefined) =>
    v === null || v === undefined || v === "" ? "—" : String(v).replace(/\|/g, "\\|");
  const cab = `| ${colunas.join(" | ")} |`;
  const sep = `| ${colunas.map(() => "---").join(" | ")} |`;
  const corpo = linhas.map((l) => `| ${l.map(celula).join(" | ")} |`).join("\n");
  return [cab, sep, corpo].filter(Boolean).join("\n");
}

/** Lista de "rótulo: valor" — para os blocos de números soltos das telas. */
export function itens(pares: [string, string | number | null | undefined][]): string {
  return pares
    .filter(([, v]) => v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `- **${k}:** ${v}`)
    .join("\n");
}

/**
 * Relatório das telas de setor (IA e Carvão), que têm formato idêntico: uma
 * lista de observações do agente agrupadas por dia. Fica aqui, e não duplicado
 * nas duas telas, porque qualquer ajuste de formato precisaria ser feito duas
 * vezes — e as duas telas já divergiram no passado por isso.
 */
export interface ObservacaoExportavel {
  ticker: string;
  date?: string | null;
  createdAt: string;
  sentiment: string;
  summary: string;
  priceAtObservation?: number | null;
}

export function montarRelatorioSetor(
  nomeSetor: string,
  tickers: string[],
  observacoes: ObservacaoExportavel[],
): string | null {
  if (!observacoes.length) return null;

  const conta = (s: string) => observacoes.filter((o) => o.sentiment === s).length;
  const blocos: string[] = [
    cabecalho(`Setor ${nomeSetor}`, `${tickers.join(" · ")}`),
    "## Resumo\n\n" + itens([
      ["Observações", observacoes.length],
      ["Bullish", conta("bullish")],
      ["Bearish", conta("bearish")],
      ["Neutras", conta("neutral")],
    ]),
  ];

  // Agrupa por dia na mesma ordem da tela (mais recente primeiro), pra o
  // relatório ler igual ao que estava na frente de quem clicou.
  const porDia = new Map<string, ObservacaoExportavel[]>();
  for (const o of observacoes) {
    const d = o.date ?? o.createdAt.split("T")[0];
    if (!porDia.has(d)) porDia.set(d, []);
    porDia.get(d)!.push(o);
  }
  const dias = [...porDia.entries()].sort((a, b) => b[0].localeCompare(a[0]));

  for (const [dia, obs] of dias) {
    blocos.push(`## ${dia}\n\n` + obs
      .map((o) => {
        const preco = o.priceAtObservation != null ? ` · $${o.priceAtObservation.toFixed(2)}` : "";
        return `**${o.ticker}** (${o.sentiment}${preco})\n${o.summary}`;
      })
      .join("\n\n"));
  }

  return blocos.join("\n\n");
}

/** Percentual com sinal explícito; null vira travessão. */
export function pct(v: number | null | undefined, casas = 2): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(casas)}%`;
}
