import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileDown, Mail, Check, Download } from "lucide-react";

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
  tela_previsao_vol: "previsão de vol",
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

/**
 * Nome de arquivo seguro a partir do título: minúsculas, sem acento, hífens.
 * Exportado separado do componente porque é regra pura — testável sem DOM.
 */
export function nomeArquivoMarkdown(titulo: string, data: Date = new Date()): string {
  const slug = titulo
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-+|-+$)/g, "")
    .slice(0, 60)
    .replace(/-+$/, "");
  const dia = data.toISOString().slice(0, 10);
  return `${slug || "relatorio"}-${dia}.md`;
}

export function ExportarRelatorio({ titulo, mode, tickers, construir, pronto = true }: Props) {
  const queryClient = useQueryClient();
  const [aviso, setAviso] = useState<{ texto: string; tom: "ok" | "erro" } | null>(null);

  // Download local: gera o .md no navegador, sem tocar no servidor — o
  // arquivo cai na pasta de downloads do dispositivo (celular incluso).
  function baixarMarkdown() {
    setAviso(null);
    const markdown = construir();
    if (!markdown) {
      setAviso({ texto: "Nada para exportar — rode a análise primeiro.", tom: "erro" });
      return;
    }
    const blob = new Blob([markdown], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = nomeArquivoMarkdown(titulo);
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    setAviso({ texto: "Arquivo .md baixado no dispositivo.", tom: "ok" });
  }

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
      <button
        type="button"
        onClick={baixarMarkdown}
        disabled={ocupado || !temDados}
        title={temDados ? "Baixa o relatório como arquivo .md no dispositivo" : "Rode a análise antes de exportar"}
        className="px-4 py-2 border border-border rounded font-mono text-xs font-bold text-foreground disabled:opacity-50 flex items-center gap-2 hover:border-primary/50"
      >
        <Download className="h-3.5 w-3.5" /> Baixar .md
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

/**
 * O bloco "Análise com IA" do relatório, com o veredito do validador JUNTO.
 *
 * O .md levava só o texto do modelo. A tela mostra o bloco amarelo ("⚠ O
 * validador apontou N problema(s)"); o arquivo exportado não mostrava nada —
 * e é o arquivo que sobrevive à sessão, que é relido depois e que circula
 * por fora do app.
 *
 * Incidente que motivou (21/09/2026): os relatórios de ADI e MRVL saíram com
 * "17,29% abaixo da máxima" e "29,93% abaixo da máxima". Os dois números são
 * a SUBIDA até a máxima, não a distância abaixo dela (o certo é −14,74% e
 * −23,04%), e a checagem 9b do validador (ANALISE_DISTANCIA_DA_FAIXA) pegou
 * os dois na hora. O .md não levou uma linha disso, e as análises foram
 * lidas como se estivessem limpas.
 *
 * Um validador cujo veredito não acompanha o documento validado não protege
 * quem lê o documento.
 *
 * Citação em bloco (`>`), e no TOPO: o aviso tem que ser visto por quem abre
 * o arquivo e lê a primeira tela, não por quem chega ao fim.
 */
export function blocoAnaliseIA(
  markdown: string,
  avisos?: string[] | null,
  truncado?: boolean,
): string {
  const partes = ["## Análise com IA"];
  if (avisos?.length) {
    partes.push(
      `> ⚠ **O validador apontou ${avisos.length} problema(s) nesta análise:**`,
      ...avisos.map((a) => `> - ${a}`),
    );
  }
  if (truncado) {
    partes.push("> ⚠ **O texto foi cortado por tamanho — a Síntese pode estar incompleta.**");
  }
  partes.push(markdown);
  return partes.join("\n\n");
}

/**
 * De qual sessão saem as médias da reação a earnings.
 *
 * Sem esta linha o .md exibe a coluna "Gap dia" (D0) logo abaixo de médias
 * que, num emissor que divulga APÓS o fechamento, vêm da sessão SEGUINTE — e
 * as duas coisas se leem como contradição. Aconteceu ao conferir o MRVL em
 * 21/09/2026: os oito gaps da tabela eram positivos (média +2,26%), o "gap
 * médio" do resumo era −1,1%, e os dois estavam certos, em colunas
 * diferentes. A tela já marca a coluna que reage com "◂"; o arquivo não
 * marcava nada.
 */
export function notaDaJanelaDeReacao(
  janela: "anuncio" | "seguinte" | undefined | null,
): string | null {
  if (janela === "seguinte") {
    return 'sessão SEGUINTE ao anúncio (divulga após o fechamento) — é a coluna "Fech. D+1"';
  }
  if (janela === "anuncio") {
    return 'sessão do anúncio (divulga antes da abertura) — é a coluna "Fech. dia"';
  }
  return null;
}
