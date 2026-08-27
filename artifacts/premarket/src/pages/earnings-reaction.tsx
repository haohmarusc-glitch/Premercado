import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { MarkdownContent } from "@/components/markdown";
import { Gauge, Sparkles } from "lucide-react";
import { ExportarRelatorio, cabecalho, itens, tabela, pct } from "@/components/exportar-relatorio";
import { benchmarkSugerido } from "@/lib/benchmark-setor";

interface SessionMove {
  date: string;
  gap_pct: number;
  close_pct: number;
  intraday_range_pct: number;
  volume: number;
}

interface PontoTrajetoria {
  dia: number;
  date: string;
  acum_pct: number;
  dia_pct: number;
  /** Ausentes quando o benchmark não veio (rede fora) — o cru ainda serve. */
  bench_pct?: number;
  excesso_pct?: number;
}

interface ReactionEvent {
  earnings_date: string;
  runup_pct?: number | null;
  announcement_day: SessionMove | null;
  next_day: SessionMove | null;
  /** Em qual sessão a notícia é precificada -- decidido pelo HORÁRIO de
   * divulgação (ver `_janela_da_reacao` no backend), nunca pela magnitude
   * do movimento. "anuncio" = BMO (reage no próprio dia); "seguinte" = AMC
   * (reage no pregão seguinte). */
  janela_reacao?: "anuncio" | "seguinte";
  /** true quando o horário de divulgação não veio da fonte e a janela foi
   * assumida pelo padrão dominante (AMC), não confirmada. */
  janela_inferida?: boolean;
  /** Até 10 pregões após o balanço. Vazio nos earnings recentes demais. */
  trajetoria?: PontoTrajetoria[];
}

interface RunupSummary {
  runup_pregoes: number;
  esticado_corte_pct: number;
  n_com_runup: number;
  corr_runup_reacao?: number | null;
  esticado_n?: number;
  esticado_caiu_n?: number;
  esticado_reacao_media?: number | null;
  descontado_n?: number;
  descontado_subiu_n?: number;
  descontado_reacao_media?: number | null;
  runup_atual_pct?: number;
  estado_atual?: "esticado" | "descontado" | "neutro";
  // A janela de RUNUP_PREGOES termina HOJE, então logo após um balanço ela
  // engole o pregão de reação: runup_atual_pct deixa de medir antecipação.
  // Quando isso acontece, estado_atual passa a sair do ex-evento.
  janela_contem_earnings?: boolean;
  pregoes_desde_earnings?: number;
  runup_atual_ex_evento_pct?: number;
}

// `number | null` em TODO campo numérico, e não só nos dois que já eram.
//
// O backend serializa por json_seguro desde 18/08/2026: qualquer valor
// não-finito vira `null` em vez de `NaN`, porque `NaN` não é JSON válido e
// derrubava a resposta inteira no JSON.parse do Node.
//
// A consequência aqui é que o tipo ANTIGO virou mentira -- declarava `number`
// para campos que chegam nulos. E tipo mentiroso não é neutro: ele silencia
// justamente o `valor.toFixed()` que quebra em runtime. Foi o que aconteceu:
// a tela ficou PRETA, sem cabeçalho e sem mensagem, porque um throw no render
// desmonta a árvore React inteira.
//
// Declarar a verdade faz o compilador enumerar cada ponto de uso -- que é
// muito melhor que revisar trinta chamadas a olho.
interface ReactionSummary {
  n_events: number;
  gap_pct_mean: number | null;
  gap_pct_abs_mean: number | null;
  close_pct_mean: number | null;
  close_pct_abs_mean: number | null;
  close_pct_std: number | null;
  intraday_range_pct_mean: number | null;
  volume_ratio_mean: number | null;
  suggested_threshold_pct: number | null;
  trajetoria?: {
    dias: {
      dia: number; n: number; acum_medio_pct: number; positivos: number;
      excesso_medio_pct?: number; bateu_bench?: number;
    }[];
  };
  current_price: number | null;
  r1_price: number | null;
  r2_price: number | null;
  s1_price: number | null;
  s2_price: number | null;
  runup?: RunupSummary;
}

interface ReactionResult {
  ticker: string;
  error?: string;
  summary?: ReactionSummary;
  events?: ReactionEvent[];
}

const DEFAULT_TICKERS = "NVDA,SMCI,AVGO,SKHY,ARM";

// Traço em vez de exceção. "—" é o que a tela já mostra para indicador que
// não pôde ser calculado; um throw aqui apaga a página inteira.
const SEM_DADO = "—";

export function temNumero(v: number | null | undefined): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

export function fmtPct(v: number | null | undefined): string {
  if (!temNumero(v)) return SEM_DADO;
  return `${v >= 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function fmtUsd(v: number | null | undefined): string {
  if (!temNumero(v)) return SEM_DADO;
  return `$${v.toFixed(2)}`;
}

/** Número solto, com casas configuráveis e sufixo opcional ("%", "x"). */
export function fmtNum(v: number | null | undefined, casas = 2, sufixo = ""): string {
  if (!temNumero(v)) return SEM_DADO;
  return `${v.toFixed(casas)}${sufixo}`;
}

/** Acumulado no N-ésimo pregão após o balanço, se o evento chegou lá. */
function acumNoDia(e: ReactionEvent, dia: number): PontoTrajetoria | undefined {
  return (e.trajetoria ?? []).find((p) => p.dia === dia);
}

function AcumCell({ ponto }: { ponto?: PontoTrajetoria }) {
  // Earnings recente ainda não tem D+10 — SEM_DADO é a resposta honesta, e
  // não pode virar 0% (que leria como "não andou").
  if (!ponto) return <span className="text-muted-foreground">{SEM_DADO}</span>;
  return (
    <span className={ponto.acum_pct >= 0 ? "text-green-400" : "text-red-400"}>
      {fmtPct(ponto.acum_pct)}
    </span>
  );
}

function SessionCell({ move }: { move: SessionMove | null }) {
  if (!move) return <span className="text-muted-foreground">sem pregão</span>;
  return (
    <span>
      gap <span className={move.gap_pct >= 0 ? "text-green-400" : "text-red-400"}>{fmtPct(move.gap_pct)}</span>
      {" / "}
      fech <span className={move.close_pct >= 0 ? "text-green-400" : "text-red-400"}>{fmtPct(move.close_pct)}</span>
    </span>
  );
}

// Interpretação em texto puro, calculada em cima dos mesmos campos de summary/events
// já retornados pelo backend -- nenhuma chamada de LLM, só regras diretas (mesmo
// princípio "não usa LLM" da análise em si).
/**
 * Quantos earnings a cesta REALMENTE tem, não quantos foram pedidos.
 *
 * A legenda usava o `lookback` do formulário e mentia: em 19/08/2026 o WOLF
 * saiu com 4 eventos e a tela dizia "~8 earnings por ticker". Papel novo, ou
 * com histórico curto, entrega menos do que se pede -- e a força da leitura
 * depende justamente desse número.
 */
export function amostraDaCesta(results: ReactionResult[] | null): string {
  const ns = (results ?? [])
    .map((r) => r.summary?.n_events)
    .filter((n): n is number => typeof n === "number" && n > 0);
  if (!ns.length) return "Amostra por ticker indisponível";
  const min = Math.min(...ns);
  const max = Math.max(...ns);
  return min === max
    ? `Amostra de ${min} earnings por ticker`
    : `Amostra de ${min} a ${max} earnings por ticker`;
}

/**
 * O corpo que vai para /earnings-reaction/ia — SEM os `events`.
 *
 * O servidor descarta os eventos de qualquer jeito (o prompt usa só ticker,
 * summary, error e stale), mas a tela mandava os results inteiros: 8 eventos
 * x 5 tickers x trajetórias diárias ≈ 107KB, contra o limite de 100KB do
 * body-parser global. Em 20/08/2026 isso virou 413 -> 500 "Internal server
 * error" na tela — e só quando o dado do dia cruzava o limite: a MESMA cesta
 * tinha rodado duas vezes mais cedo no mesmo dia. Enviar o formato enxuto
 * (~10KB) elimina a classe do problema em vez de negociar com o limite.
 */
export function payloadDaInterpretacao(
  results: ReactionResult[], lookback: number, benchmark: string,
): object {
  return {
    results: results.map((r) => ({
      ticker: r.ticker,
      ...(r.error != null ? { error: r.error } : {}),
      ...(r.summary != null ? { summary: r.summary } : {}),
      ...((r as { stale?: boolean }).stale ? { stale: true } : {}),
    })),
    lookback,
    benchmark,
  };
}

/**
 * Frase do estado atual (esticado/descontado) -- antes era um rótulo FIXO
 * ("historicamente é o estado que precede reações negativas") que ignorava
 * o histórico de reação daquele MESMO papel exibido duas frases acima.
 *
 * Auditoria de 27/08/2026: NVDA mostrava "em 1 de 1 balanços esticados a
 * reação foi de ALTA (média +3,25%)" e, logo depois, "ESTICADO —
 * historicamente é o estado que precede reações NEGATIVAS" -- contradição
 * direta dentro do mesmo card. Mesmo problema em SMCI (3 de 4 esticados
 * subiram, média +9,38%, rótulo continuava dizendo "precede negativas").
 *
 * Agora o rótulo só afirma a hipótese de reversão quando a MAIORIA dos
 * eventos daquele bucket, NESTE papel, confirma; senão diz explicitamente
 * que o padrão geral não se confirmou na amostra dele. Sem histórico do
 * bucket (n=0, caso do SKHY com 1 evento e run-up prévio "n/d"), não afirma
 * nada que não dá pra sustentar.
 */
export function rotuloEstadoAtual(ru: RunupSummary): string {
  const estado = ru.estado_atual;
  if (estado !== "esticado" && estado !== "descontado") return "neutro";
  const esticado = estado === "esticado";
  const NOME = esticado ? "ESTICADO" : "DESCONTADO";
  const n = (esticado ? ru.esticado_n : ru.descontado_n) ?? 0;
  const media = esticado ? ru.esticado_reacao_media : ru.descontado_reacao_media;
  const trecho = esticado ? "esticados" : "descontados";
  if (n <= 0) {
    return `${NOME} — sem eventos ${trecho} no histórico deste papel para confirmar ou contradizer o padrão geral`;
  }
  // "a favor" = confirma a hipótese de reversão: caiu (esticado) ou subiu (descontado).
  const aFavor = (esticado ? ru.esticado_caiu_n : ru.descontado_subiu_n) ?? 0;
  const contra = n - aFavor;
  const maioriaAFavor = aFavor >= contra;
  const mediaTxt = media != null ? `, média ${fmtPct(media)}` : "";
  if (maioriaAFavor) {
    const hipotese = esticado
      ? "historicamente é o estado que precede reações negativas mesmo com resultado bom"
      : "historicamente o estado com mais espaço pra surpresa positiva";
    return `${NOME} — ${hipotese} (neste papel: ${aFavor} de ${n}${mediaTxt})`;
  }
  return `${NOME} — mas NESTE papel a maioria dos eventos ${trecho} foi na direção oposta ` +
    `(${contra} de ${n}${mediaTxt}); o padrão geral não se confirma nesta amostra`;
}

export function interpretResult(r: ReactionResult): string[] {
  if (!r.summary) return [];
  const s = r.summary;
  const notes: string[] = [];

  // Cada bloco abaixo só fala quando TEM o número. Sem o guarda, um campo
  // nulo cairia no `else` e a tela afirmaria "volatilidade histórica baixa"
  // a partir de dado ausente -- pior que não dizer nada, porque a frase tem
  // a mesma cara de uma conclusão medida.
  if (!temNumero(s.suggested_threshold_pct)) {
    notes.push("Volatilidade histórica: sem dado suficiente para classificar.");
  } else if (s.suggested_threshold_pct >= 8) {
    notes.push(
      `Volatilidade histórica alta: as reações passadas chegam a mover o preço ±${fmtNum(s.suggested_threshold_pct, 1)}% no extremo — vale reduzir o tamanho da posição e usar stops mais largos.`,
    );
  } else if (s.suggested_threshold_pct >= 4) {
    notes.push(
      `Volatilidade histórica moderada: espere oscilações de até ±${fmtNum(s.suggested_threshold_pct, 1)}% em torno do resultado.`,
    );
  } else {
    notes.push(
      `Volatilidade histórica baixa: as reações passadas ficaram dentro de ±${fmtNum(s.suggested_threshold_pct, 1)}%, sinal de que o mercado já precifica bem os resultados desse papel.`,
    );
  }

  if (!temNumero(s.close_pct_mean)) {
    notes.push("Viés direcional: sem dado suficiente para avaliar.");
  } else if (Math.abs(s.close_pct_mean) >= 1) {
    const dir = s.close_pct_mean > 0 ? "positivo (alta)" : "negativo (queda)";
    notes.push(
      `Viés histórico ${dir}: em média o papel fechou ${fmtPct(s.close_pct_mean)} na janela de reação, com desvio de ${fmtNum(s.close_pct_std)}pp — não é garantia de repetição.`,
    );
  } else {
    notes.push(
      `Sem viés direcional claro: a média de fechamento (${fmtPct(s.close_pct_mean)}) é próxima de zero, sugerindo reações historicamente equilibradas entre alta e baixa.`,
    );
  }

  // Este par não ganha frase de "sem dado": ele é uma observação OPCIONAL
  // sobre a forma do movimento. Faltando um dos dois, o silêncio é a leitura
  // correta -- não há nada a dizer sobre a relação entre eles.
  if (temNumero(s.gap_pct_abs_mean) && temNumero(s.close_pct_abs_mean)
      && s.gap_pct_abs_mean > 0 && s.close_pct_abs_mean > s.gap_pct_abs_mean * 1.3) {
    notes.push(
      `O movimento tende a se ampliar ao longo do pregão: o gap médio de abertura (${fmtNum(s.gap_pct_abs_mean)}%) é bem menor que a variação até o fechamento (${fmtNum(s.close_pct_abs_mean)}%).`,
    );
  } else if (temNumero(s.gap_pct_abs_mean) && temNumero(s.close_pct_abs_mean)
             && s.gap_pct_abs_mean > 0 && s.gap_pct_abs_mean > s.close_pct_abs_mean * 1.3) {
    notes.push(
      `A maior parte do movimento historicamente acontece já na abertura: o gap médio (${fmtNum(s.gap_pct_abs_mean)}%) é próximo ou maior que a variação até o fechamento (${fmtNum(s.close_pct_abs_mean)}%).`,
    );
  }

  if (s.volume_ratio_mean != null && s.volume_ratio_mean >= 1.5) {
    notes.push(
      `O volume nos dias de reação costuma ser ${fmtNum(s.volume_ratio_mean, 1)}x a média do período — confirma que o mercado reage com convicção a esses resultados.`,
    );
  }

  // AMC/BMO vem do HORÁRIO real de divulgação (`janela_reacao`, calculado por
  // `_janela_da_reacao` no backend a partir do timestamp da fonte), não de
  // qual sessão se moveu mais -- essa era a heurística antiga, e o próprio
  // backend já abandonou a versão por magnitude por seleção-pelo-resultado
  // (ver o comentário em `_janela_da_reacao`): no momento em que o número
  // seria útil, ninguém sabe qual sessão vai andar mais. O evento já chega
  // com a resposta; usar magnitude aqui era ignorar um dado mais forte que
  // já estava disponível.
  const events = r.events ?? [];
  const comJanela = events.filter((e) => e.janela_reacao != null);
  if (comJanela.length >= 2) {
    const seguinte = comJanela.filter((e) => e.janela_reacao === "seguinte").length;
    const anuncio = comJanela.length - seguinte;
    const inferidos = comJanela.filter((e) => e.janela_inferida).length;
    const ressalva = inferidos > 0
      ? ` (${inferidos} de ${comJanela.length} sem horário confirmado pela fonte, assumido pelo padrão dominante)`
      : "";
    if (seguinte > anuncio) {
      notes.push(
        `Em ${seguinte} de ${comJanela.length} eventos, o resultado saiu depois do fechamento (AMC) — a reação é precificada no pregão SEGUINTE ao anúncio${ressalva}.`,
      );
    } else if (anuncio > seguinte) {
      notes.push(
        `Em ${anuncio} de ${comJanela.length} eventos, o resultado saiu antes da abertura (BMO) — a reação é precificada no PRÓPRIO dia do anúncio${ressalva}.`,
      );
    }
  }

  const ru = s.runup;
  if (ru && ru.esticado_n != null && ru.esticado_n > 0) {
    // A palavra QUEDA/ALTA e a contagem que a acompanha precisam descrever o
    // MESMO subconjunto -- antes a palavra era fixa em "QUEDA" e a contagem
    // sempre mostrava `esticado_caiu_n`, então um bucket onde a maioria SUBIU
    // (ex.: NVDA, 0 de 1 caiu) saía como "em 0 de 1 balanços ... a reação foi
    // de QUEDA (média +3.25%)" -- a própria frase se contradizia: 0 caíram e
    // a média era positiva, mas o rótulo dizia queda. Deriva o rótulo da
    // MAIORIA (caiu vs subiu) e troca a contagem exibida junto.
    const n = ru.esticado_n;
    const caiu = ru.esticado_caiu_n ?? 0;
    const subiu = n - caiu;
    const foiQueda = caiu >= subiu; // empate mantém a leitura conservadora original
    const contagem = foiQueda ? caiu : subiu;
    const direcao = foiQueda ? "QUEDA" : "ALTA";
    const frase = `Padrão "chegou esticado": em ${contagem} de ${n} balanços em que o papel subiu ≥${fmtNum(ru.esticado_corte_pct, 0)}% no mês anterior, a reação foi de ${direcao}` +
      (ru.esticado_reacao_media != null ? ` (média ${fmtPct(ru.esticado_reacao_media)})` : "") + `.`;
    notes.push(frase);
  }
  if (ru && ru.descontado_n != null && ru.descontado_n > 0) {
    // Mesma correção do bucket "esticado" acima, espelhada: o rótulo segue a
    // maioria (subiu vs caiu), e a contagem exibida é a do lado que o rótulo
    // descreve.
    const n = ru.descontado_n;
    const subiu = ru.descontado_subiu_n ?? 0;
    const caiu = n - subiu;
    const foiAlta = subiu >= caiu; // empate mantém a leitura conservadora original
    const contagem = foiAlta ? subiu : caiu;
    const direcao = foiAlta ? "ALTA" : "QUEDA";
    notes.push(
      `Chegando descontado (mês anterior ≤ 0%): ${contagem} de ${n} reações foram de ${direcao}` +
      (ru.descontado_reacao_media != null ? ` (média ${fmtPct(ru.descontado_reacao_media)})` : "") + `.`,
    );
  }
  if (ru && ru.corr_runup_reacao != null) {
    notes.push(
      `Correlação run-up × reação: ${ru.corr_runup_reacao >= 0 ? "+" : ""}${fmtNum(ru.corr_runup_reacao)} — amostra pequena, trate como indício, não prova.`,
    );
  }

  // A reação inicial grudou ou foi devolvida? Compara o primeiro pregão com
  // o último horizonte disponível — é a pergunta que a trajetória existe
  // para responder, e ninguém deveria precisar ler a tabela pra chegar lá.
  const traj = s.trajetoria?.dias ?? [];
  if (traj.length >= 2) {
    const d1 = traj[0];
    const ultimo = traj[traj.length - 1];
    // Prefere o EXCESSO quando existe: um papel que subiu 10% num setor que
    // subiu 10% não reagiu ao balanço, e a frase não pode dizer que reagiu.
    const usaExcesso = d1.excesso_medio_pct != null && ultimo.excesso_medio_pct != null;
    const v1 = usaExcesso ? d1.excesso_medio_pct! : d1.acum_medio_pct;
    const vN = usaExcesso ? ultimo.excesso_medio_pct! : ultimo.acum_medio_pct;
    const virou = Math.sign(v1) !== Math.sign(vN);
    const encolheu = Math.abs(vN) < Math.abs(v1) * 0.5;
    const base = `Trajetória${usaExcesso ? " (excesso sobre o benchmark)" : ""}: em média o papel estava ${fmtPct(v1)} no D+${d1.dia} e ${fmtPct(vN)} no D+${ultimo.dia} (${ultimo.n} evento${ultimo.n === 1 ? "" : "s"} com esse horizonte)`;
    if (virou) {
      notes.push(`${base} — a reação inicial se INVERTEU ao longo das semanas seguintes; o movimento do dia foi mau guia da direção.`);
    } else if (encolheu) {
      notes.push(`${base} — o mercado DEVOLVEU boa parte da reação inicial, sinal de que o movimento do dia exagera.`);
    } else if (Math.abs(vN) > Math.abs(v1)) {
      notes.push(`${base} — a reação inicial CONTINUOU na mesma direção; o movimento do dia tende a ser começo, não fim.`);
    } else {
      notes.push(`${base} — a reação inicial se manteve, sem continuação nem devolução relevante.`);
    }
  }
  if (ru && ru.runup_atual_pct != null && ru.estado_atual) {
    const rotulo = rotuloEstadoAtual(ru);
    if (ru.janela_contem_earnings) {
      // Sem esta ressalva a frase lê "o papel chega esticado ao balanço" logo
      // DEPOIS de um balanço — o run-up bruto aqui é a reação já ocorrida.
      notes.push(
        `O último balanço foi há ${ru.pregoes_desde_earnings} pregão(ões), DENTRO da janela de run-up: ` +
        `os ${fmtPct(ru.runup_atual_pct)} do mês incluem o próprio salto da reação. ` +
        (ru.runup_atual_ex_evento_pct != null
          ? `Descontando o pregão do evento, o run-up é ${fmtPct(ru.runup_atual_ex_evento_pct)} → ${rotulo}.`
          : `O estado abaixo desconsidera o pregão do evento.`),
      );
    } else {
      notes.push(
        `Estado atual do papel: run-up de ${fmtPct(ru.runup_atual_pct)} no último mês → ${rotulo}.`,
      );
    }
  }

  if (s.n_events < 4) {
    notes.push(
      `Amostra pequena (${s.n_events} evento${s.n_events === 1 ? "" : "s"}) — trate os pontos acima como indicativos, não estatisticamente robustos.`,
    );
  }

  return notes;
}

// Um bloco por ticker, com os níveis técnicos e a tabela evento a evento —
// que é o dado que sustenta a média. O run-up entra como seção própria só
// quando existe: nem todo ticker tem histórico suficiente pro cálculo.
function montarRelatorioReacao(results: ReactionResult[], lookback: string, leituraIA?: string | null): string {
  const blocos: string[] = [];
  const ok = results.filter((r) => r.summary);
  blocos.push(cabecalho(
    `Reação a earnings — ${ok.map((r) => r.ticker).join(", ") || "sem resultado"}`,
    `Lookback de ${lookback} earnings passados`,
  ));

  // A leitura da IA vai no TOPO, antes das tabelas: ela é comparação entre os
  // papéis, e quem exporta o relatório quer a conclusão antes do dado bruto.
  // Fora quando não foi pedida -- seção vazia num relatório parece dado que
  // faltou, não recurso que não se usou.
  if (leituraIA) blocos.push(`## Leitura da cesta com IA\n\n${leituraIA}`);

  for (const r of results) {
    if (r.error || !r.summary) {
      blocos.push(`## ${r.ticker}\n\nSem resultado: ${r.error ?? "dados insuficientes"}`);
      continue;
    }
    const s = r.summary;
    blocos.push(`## ${r.ticker}\n\n` + itens([
      ["Eventos analisados", s.n_events],
      ["Threshold sugerido", `±${fmtNum(s.suggested_threshold_pct)}%`],
      ["Preço atual", fmtUsd(s.current_price)],
      ["Gap médio", pct(s.gap_pct_mean)],
      ["Gap médio absoluto", `${fmtNum(s.gap_pct_abs_mean)}%`],
      ["Fechamento médio", pct(s.close_pct_mean)],
      ["Fechamento médio absoluto", `${fmtNum(s.close_pct_abs_mean)}%`],
      ["Desvio-padrão do fechamento", fmtNum(s.close_pct_std, 2, "%")],
      ["Amplitude intradiária média", fmtNum(s.intraday_range_pct_mean, 2, "%")],
      ["Razão de volume", fmtNum(s.volume_ratio_mean, 2, "x")],
      // Bandas estatísticas (preço ± reação histórica), NÃO suporte/resistência
      // de gráfico — o rótulo antigo ("Resistências"/"Suportes") fazia o texto
      // exportado ser lido como estrutura de preço, inclusive pela análise com IA.
      ["Banda de reação · alta", `R1 ${fmtUsd(s.r1_price)} · R2 ${fmtUsd(s.r2_price)}`],
      ["Banda de reação · baixa", `S1 ${fmtUsd(s.s1_price)} · S2 ${fmtUsd(s.s2_price)}`],
    ]) + "\n\n_R1/R2/S1/S2 projetam a volatilidade histórica de earnings sobre o preço atual — não são suporte/resistência técnico._");

    const ru = s.runup;
    if (ru) {
      const linhasRunup: [string, string | number | null | undefined][] = [
        ["Janela", `${ru.runup_pregoes} pregões · corte de esticado em ${ru.esticado_corte_pct}%`],
        ["Eventos com run-up medido", ru.n_com_runup],
        ["Correlação run-up × reação", fmtNum(ru.corr_runup_reacao)],
        ["Esticado", ru.esticado_n != null ? `${ru.esticado_caiu_n ?? 0} de ${ru.esticado_n} caíram · reação média ${pct(ru.esticado_reacao_media)}` : "—"],
        ["Descontado", ru.descontado_n != null ? `${ru.descontado_subiu_n ?? 0} de ${ru.descontado_n} subiram · reação média ${pct(ru.descontado_reacao_media)}` : "—"],
        ["Run-up atual", ru.runup_atual_pct != null ? `${pct(ru.runup_atual_pct)} (${ru.estado_atual ?? "—"})` : "—"],
      ];
      // Janela contaminada: o run-up bruto engloba o próprio salto do balanço,
      // então o número sozinho induz a leitura errada ("chegou esticado" para
      // um evento que já passou). O ex-evento vem junto, sempre.
      if (ru.janela_contem_earnings) {
        linhasRunup.push(
          ["Balanço dentro da janela", `sim — há ${ru.pregoes_desde_earnings} pregão(ões), o run-up bruto inclui a reação`],
          ["Run-up ex-evento", ru.runup_atual_ex_evento_pct != null ? `${pct(ru.runup_atual_ex_evento_pct)} (é este que define o estado)` : "—"],
        );
      }
      blocos.push(`### Run-up prévio (${r.ticker})\n\n` + itens(linhasRunup));
    }

    if (r.events?.length) {
      blocos.push(`### Eventos (${r.ticker})\n\n` + tabela(
        ["Data", "Run-up", "Gap dia", "Fech. dia", "Amplitude", "Fech. D+1", "D+5", "D+10"],
        r.events.map((e) => [
          e.earnings_date,
          e.runup_pct != null ? pct(e.runup_pct) : "—",
          e.announcement_day ? pct(e.announcement_day.gap_pct) : "—",
          e.announcement_day ? pct(e.announcement_day.close_pct) : "—",
          e.announcement_day ? fmtNum(e.announcement_day.intraday_range_pct, 2, "%") : "—",
          e.next_day ? pct(e.next_day.close_pct) : "—",
          acumNoDia(e, 5) ? pct(acumNoDia(e, 5)!.acum_pct) : "—",
          acumNoDia(e, 10) ? pct(acumNoDia(e, 10)!.acum_pct) : "—",
        ]),
      ));
    }

    if (s.trajetoria?.dias.length) {
      blocos.push(`### Trajetória média pós-earnings (${r.ticker})\n\n` + tabela(
        ["Pregão", "Acumulado médio", "Excesso vs benchmark", "Positivos", "Bateu setor", "Eventos com dado"],
        s.trajetoria.dias.map((d) => [
          `D+${d.dia}`,
          pct(d.acum_medio_pct),
          d.excesso_medio_pct != null ? pct(d.excesso_medio_pct) : "—",
          String(d.positivos),
          d.bateu_bench != null ? String(d.bateu_bench) : "—",
          String(d.n),
        ]),
      ));
    }

    // Detalhe dia a dia por evento — é o dado cru que sustenta as médias
    // acima. Fica só no relatório: na tela, dez colunas por evento seriam
    // ilegíveis no celular.
    for (const e of r.events ?? []) {
      if (!e.trajetoria?.length) continue;
      blocos.push(`#### ${r.ticker} · ${e.earnings_date} — dia a dia\n\n` + tabela(
        ["Pregão", "Data", "Acumulado", "Variação do dia", "Benchmark", "Excesso"],
        e.trajetoria.map((p) => [
          `D+${p.dia}`, p.date, pct(p.acum_pct), pct(p.dia_pct),
          p.bench_pct != null ? pct(p.bench_pct) : "—",
          p.excesso_pct != null ? pct(p.excesso_pct) : "—",
        ]),
      ));
    }
  }

  return blocos.join("\n\n");
}

export default function EarningsReactionPage() {
  const [tickersInput, setTickersInput] = useState(DEFAULT_TICKERS);
  const [lookback, setLookback] = useState("8");
  // Referência do excesso na trajetória. Sugerido pelo PRIMEIRO ticker da
  // lista (é o mais provável foco da consulta) e editável — uma cesta com
  // papéis de setores diferentes precisa de uma escolha do usuário, e SPY
  // costuma ser a resposta certa nesse caso.
  const [benchmark, setBenchmark] = useState("");
  const [benchmarkManual, setBenchmarkManual] = useState(false);
  const primeiroTicker = tickersInput.split(",")[0]?.trim().toUpperCase() ?? "";
  const benchmarkEfetivo = (benchmarkManual ? benchmark : benchmarkSugerido(primeiroTicker)) || "SPY";
  const [results, setResults] = useState<ReactionResult[] | null>(null);
  const [leituraIA, setLeituraIA] = useState<
    {
      markdown: string;
      usage?: { total_cost_usd?: number };
      truncado?: boolean;
      // Apontamentos do validador da prosa (agent/reacao_earnings_validator.py).
      // Aparecem JUNTO do texto, nunca no lugar dele: esconder a leitura
      // deixaria a tela vazia sem dizer por quê.
      avisos?: string[];
    } | null
  >(null);

  const run = useMutation({
    mutationFn: async () => {
      const tickers = tickersInput.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
      const r = await fetch("/api/earnings-reaction/run", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tickers,
          lookback: parseInt(lookback, 10) || 8,
          benchmark: benchmarkEfetivo,
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.error || "Failed");
      return data as ReactionResult[];
    },
    // Dados novos invalidam a leitura da IA -- texto antigo sobre cesta nova
    // seria interpretação de outro retrato.
    onSuccess: (data) => { setResults(data); setLeituraIA(null); },
  });

  // Uma chamada para a CESTA inteira, não uma por ticker: a tela já interpreta
  // cada papel por regra (interpretResult), e o que a IA acrescenta é a
  // comparação entre eles -- que a regra não faz por construção.
  const runIA = useMutation({
    mutationFn: async () => {
      const r = await fetch("/api/earnings-reaction/ia", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(
          payloadDaInterpretacao(results ?? [], parseInt(lookback, 10) || 8, benchmarkEfetivo),
        ),
      });
      const data = await r.json();
      if (!r.ok || data.error) throw new Error(data.error || "Falha na interpretação com IA");
      return data as {
        markdown: string;
        usage?: { total_cost_usd?: number };
        truncado?: boolean;
        avisos?: string[];
      };
    },
    onSuccess: setLeituraIA,
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      <div className="border-b border-border pb-4">
        <h1 className="text-3xl font-bold font-mono text-foreground tracking-tight flex items-center gap-2">
          <Gauge className="h-7 w-7 text-primary" /> REAÇÃO A EARNINGS
        </h1>
        <p className="text-muted-foreground font-mono text-sm mt-2">
          Parametriza a volatilidade esperada em torno de resultados (gap, fechamento, volume) em vez de
          depender do calor do momento — não usa LLM, é cálculo direto sobre o histórico do yfinance.
        </p>
      </div>

      <div className="border border-border rounded-lg bg-card p-5 space-y-4">
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
          <div className="flex flex-col gap-1 sm:col-span-2">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Tickers (separados por vírgula)</label>
            <input
              type="text"
              value={tickersInput}
              onChange={(e) => setTickersInput(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Earnings passados (lookback)</label>
            <input
              type="number" min="1" max="20"
              value={lookback}
              onChange={(e) => setLookback(e.target.value)}
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
          <div className="flex flex-col gap-1">
            <label className="text-[10px] font-mono text-muted-foreground uppercase">Benchmark (excesso)</label>
            <input
              type="text"
              value={benchmarkEfetivo}
              onChange={(e) => { setBenchmark(e.target.value); setBenchmarkManual(true); }}
              placeholder="SPY, SMH, KWEB..."
              className="bg-background border border-border rounded px-3 py-2 font-mono text-sm text-foreground focus:outline-none focus:ring-1 focus:ring-primary"
            />
            <span className="text-[10px] font-mono text-muted-foreground/70">
              {benchmarkManual
                ? "escolhido por você"
                : `sugerido por ${primeiroTicker || "—"} · cesta mista? use SPY`}
            </span>
          </div>
        </div>
        <button
          onClick={() => run.mutate()}
          disabled={run.isPending || !tickersInput.trim()}
          className="px-6 py-2 bg-primary text-primary-foreground rounded font-mono text-sm font-bold disabled:opacity-50 flex items-center gap-2"
        >
          {run.isPending ? (
            <>
              <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full" />
              Rodando...
            </>
          ) : (
            <><Gauge className="h-4 w-4" /> Rodar análise</>
          )}
        </button>
        {(run.isError || runIA.isError) && (
          <p className="text-sm text-red-400 font-mono">{String(run.error ?? runIA.error)}</p>
        )}
        {results && results.filter((r) => r.summary).length >= 2 && (
          <button
            onClick={() => runIA.mutate()}
            disabled={runIA.isPending}
            className="px-6 py-2 border border-primary/40 text-primary rounded font-mono text-sm font-bold disabled:opacity-50 flex items-center gap-2"
          >
            {runIA.isPending ? (
              <span className="animate-spin inline-block w-3.5 h-3.5 border-2 border-primary/30 border-t-primary rounded-full" />
            ) : (
              <Sparkles className="h-4 w-4" />
            )}
            Interpretar cesta com IA
          </button>
        )}
        {results && results.length > 0 && (
          <div className="border-t border-border/40 pt-4">
            <ExportarRelatorio
              titulo={`Reação a earnings — ${results.map((r) => r.ticker).join(", ")}`}
              mode="tela_earnings_reaction"
              tickers={results.map((r) => r.ticker)}
              construir={() => montarRelatorioReacao(results, lookback, leituraIA?.markdown)}
            />
          </div>
        )}
      </div>

      {leituraIA && (
        <div className="border border-border rounded-lg overflow-hidden">
          <div className="px-4 py-2.5 border-b border-border bg-secondary/30 flex items-center gap-2">
            <Sparkles className="h-4 w-4 text-primary" />
            <span className="font-mono font-bold text-primary">Leitura da cesta com IA</span>
          </div>
          <div className="p-4 space-y-3">
            <p className="font-mono text-[10px] text-muted-foreground/70">
              Comparação entre os papéis, sobre a estatística já calculada — o que cada um mostra
              sozinho está no card dele. {amostraDaCesta(results)}: são observações, não regras.
              Não é recomendação de compra ou venda.
              {leituraIA.usage?.total_cost_usd != null
                && ` · custo desta leitura: ~$${leituraIA.usage.total_cost_usd.toFixed(4)}`}
            </p>
            {leituraIA.truncado && (
              <p className="font-mono text-xs px-3 py-2 rounded border border-yellow-500/40 bg-yellow-500/10 text-yellow-400">
                ⚠ O texto bateu o limite de tamanho e terminou no meio — rode de novo para uma versão completa.
              </p>
            )}
            {leituraIA.avisos && leituraIA.avisos.length > 0 && (
              <div className="font-mono text-xs px-3 py-2 rounded border border-yellow-500/40 bg-yellow-500/10 text-yellow-400 space-y-1">
                <p className="font-semibold">
                  ⚠ O validador apontou {leituraIA.avisos.length} problema(s) neste texto:
                </p>
                {leituraIA.avisos.map((a, i) => (
                  <p key={i}>{a}</p>
                ))}
                <p className="opacity-80">
                  A leitura fica abaixo assim mesmo — leia com estes pontos em mente.
                </p>
              </div>
            )}
            <MarkdownContent content={leituraIA.markdown} />
          </div>
        </div>
      )}

      {results && (
        <div className="space-y-4">
          {results.map((r) => (
            <div key={r.ticker} className="border border-border rounded-lg overflow-hidden">
              <div className="px-4 py-2.5 border-b border-border bg-secondary/30 flex items-center justify-between">
                <span className="font-mono font-bold text-primary">{r.ticker}</span>
                {r.summary && (
                  <span className="font-mono text-xs text-muted-foreground">
                    {r.summary.n_events} evento(s) · threshold sugerido ±{fmtNum(r.summary.suggested_threshold_pct)}%
                  </span>
                )}
              </div>

              {r.error ? (
                <p className="px-4 py-3 font-mono text-sm text-muted-foreground">⚠ {r.error}</p>
              ) : r.summary ? (
                <>
                  <div className="grid grid-cols-2 md:grid-cols-4 gap-4 p-4">
                    {[
                      { label: "Gap médio", value: fmtPct(r.summary.gap_pct_mean), sub: `|média| ${fmtNum(r.summary.gap_pct_abs_mean)}%` },
                      { label: "Fechamento médio", value: fmtPct(r.summary.close_pct_mean), sub: `desvio ${fmtNum(r.summary.close_pct_std)}` },
                      { label: "Range intradiário", value: fmtNum(r.summary.intraday_range_pct_mean, 2, "%"), sub: "" },
                      { label: "Volume vs média", value: fmtNum(r.summary.volume_ratio_mean, 2, "x"), sub: "" },
                    ].map(({ label, value, sub }) => (
                      <div key={label} className="border border-border/60 rounded-lg bg-background p-3">
                        <div className="text-[10px] font-mono text-muted-foreground uppercase mb-1">{label}</div>
                        <div className="text-lg font-bold font-mono text-foreground">{value}</div>
                        {sub && <div className="text-[10px] font-mono text-muted-foreground mt-0.5">{sub}</div>}
                      </div>
                    ))}
                  </div>

                  <div className="px-4 pb-4">
                    <div className="border border-border/60 rounded-lg bg-background p-3">
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] font-mono text-muted-foreground uppercase">
                          Níveis projetados (base: {fmtUsd(r.summary.current_price)})
                        </span>
                        <span className="text-[10px] font-mono text-muted-foreground/70">
                          bandas estatísticas, não suporte/resistência técnico
                        </span>
                      </div>
                      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 font-mono text-sm">
                        <div>
                          <div className="text-[10px] text-muted-foreground uppercase">R2 · alvo extremo</div>
                          <div className="text-green-400 font-bold">{fmtUsd(r.summary.r2_price)}</div>
                        </div>
                        <div>
                          <div className="text-[10px] text-muted-foreground uppercase">R1 · alvo médio</div>
                          <div className="text-green-400/80 font-bold">{fmtUsd(r.summary.r1_price)}</div>
                        </div>
                        <div>
                          <div className="text-[10px] text-muted-foreground uppercase">S1 · queda média</div>
                          <div className="text-red-400/80 font-bold">{fmtUsd(r.summary.s1_price)}</div>
                        </div>
                        <div>
                          <div className="text-[10px] text-muted-foreground uppercase">S2 · risco extremo</div>
                          <div className="text-red-400 font-bold">{fmtUsd(r.summary.s2_price)}</div>
                        </div>
                      </div>
                    </div>
                  </div>

                  {r.summary.trajetoria && r.summary.trajetoria.dias.length > 0 && (
                    <div className="px-4 pb-4">
                      <div className="border border-border/60 rounded-lg bg-background p-3">
                        <div className="flex items-center justify-between mb-2 flex-wrap gap-1">
                          <span className="text-[10px] font-mono text-muted-foreground uppercase">
                            Trajetória média pós-earnings (acumulado vs véspera)
                          </span>
                          <span className="text-[10px] font-mono text-muted-foreground/70">
                            a reação gruda ou é devolvida?
                          </span>
                        </div>
                        <div className="overflow-x-auto">
                          <table className="w-full font-mono text-xs">
                            <thead>
                              <tr>
                                <th className="text-left pr-2 py-1 text-[10px] text-muted-foreground uppercase">Pregão</th>
                                {r.summary.trajetoria.dias.map((d) => (
                                  <th key={d.dia} className="text-right px-1.5 py-1 text-[10px] text-muted-foreground">
                                    D+{d.dia}
                                  </th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              <tr>
                                <td className="pr-2 py-1 text-[10px] text-muted-foreground uppercase">Média</td>
                                {r.summary.trajetoria.dias.map((d) => (
                                  <td key={d.dia} className={`text-right px-1.5 py-1 font-bold ${d.acum_medio_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
                                    {fmtPct(d.acum_medio_pct)}
                                  </td>
                                ))}
                              </tr>
                              {r.summary.trajetoria.dias.some((d) => d.excesso_medio_pct != null) && (
                                <tr>
                                  <td className="pr-2 py-1 text-[10px] text-muted-foreground uppercase" title="Retorno do papel MENOS o do benchmark no mesmo intervalo — separa 'reagiu ao resultado' de 'andou com o setor'">
                                    Excesso
                                  </td>
                                  {r.summary.trajetoria.dias.map((d) => (
                                    <td key={d.dia} className={`text-right px-1.5 py-1 font-bold ${(d.excesso_medio_pct ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                                      {d.excesso_medio_pct != null ? fmtPct(d.excesso_medio_pct) : SEM_DADO}
                                    </td>
                                  ))}
                                </tr>
                              )}
                              <tr>
                                <td className="pr-2 py-1 text-[10px] text-muted-foreground uppercase" title="Quantos dos eventos analisados estavam positivos neste pregão, e sobre quantos com dado disponível">Positivos</td>
                                {r.summary.trajetoria.dias.map((d) => (
                                  <td key={d.dia} className="text-right px-1.5 py-1 text-muted-foreground">
                                    {d.positivos}/{d.n}
                                  </td>
                                ))}
                              </tr>
                              {r.summary.trajetoria.dias.some((d) => d.bateu_bench != null) && (
                                <tr>
                                  <td className="pr-2 py-1 text-[10px] text-muted-foreground uppercase" title="Em quantos eventos o papel superou o benchmark neste pregão">Bateu setor</td>
                                  {r.summary.trajetoria.dias.map((d) => (
                                    <td key={d.dia} className="text-right px-1.5 py-1 text-muted-foreground">
                                      {d.bateu_bench != null ? `${d.bateu_bench}/${d.n}` : SEM_DADO}
                                    </td>
                                  ))}
                                </tr>
                              )}
                            </tbody>
                          </table>
                        </div>
                        <p className="text-[10px] font-mono text-muted-foreground/70 mt-2 leading-relaxed">
                          Os horizontes mais longos têm menos amostra — earnings recentes ainda não completaram os 10 pregões
                          (veja o denominador de "positivos").
                        </p>
                      </div>
                    </div>
                  )}

                  {r.events && r.events.length > 0 && (
                    <div className="overflow-x-auto border-t border-border">
                      <table className="w-full font-mono text-sm">
                        <thead className="bg-secondary/20">
                          <tr>
                            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Earnings</th>
                            <th className="text-right px-4 py-2 text-[10px] text-muted-foreground uppercase" title="Variação do mês (21 pregões) anterior ao balanço">Run-up prévio</th>
                            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Dia do anúncio</th>
                            <th className="text-left px-4 py-2 text-[10px] text-muted-foreground uppercase">Dia seguinte</th>
                            <th className="text-right px-4 py-2 text-[10px] text-muted-foreground uppercase" title="Acumulado do 5º pregão após o balanço, contra o fechamento da véspera">D+5</th>
                            <th className="text-right px-4 py-2 text-[10px] text-muted-foreground uppercase" title="Acumulado do 10º pregão após o balanço, contra o fechamento da véspera">D+10</th>
                          </tr>
                        </thead>
                        <tbody>
                          {r.events.map((e, idx) => (
                            <tr key={e.earnings_date} className={idx % 2 === 0 ? "bg-card" : "bg-secondary/10"}>
                              <td className="px-4 py-2 text-muted-foreground">{e.earnings_date}</td>
                              <td className={`px-4 py-2 text-right ${e.runup_pct == null ? "text-muted-foreground" : e.runup_pct >= 10 ? "text-yellow-400" : e.runup_pct <= 0 ? "text-blue-400" : "text-muted-foreground"}`}>
                                {e.runup_pct != null ? fmtPct(e.runup_pct) : "n/d"}
                              </td>
                              <td className="px-4 py-2"><SessionCell move={e.announcement_day} /></td>
                              <td className="px-4 py-2"><SessionCell move={e.next_day} /></td>
                              <td className="px-4 py-2 text-right"><AcumCell ponto={acumNoDia(e, 5)} /></td>
                              <td className="px-4 py-2 text-right"><AcumCell ponto={acumNoDia(e, 10)} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}

                  {(() => {
                    const notes = interpretResult(r);
                    if (notes.length === 0) return null;
                    return (
                      <div className="px-4 pb-4 pt-3 border-t border-border">
                        <div className="text-[10px] font-mono text-muted-foreground uppercase mb-2">
                          Interpretação
                        </div>
                        <ul className="space-y-1.5">
                          {notes.map((note, idx) => (
                            <li key={idx} className="font-mono text-xs text-muted-foreground leading-relaxed flex gap-2">
                              <span className="text-primary shrink-0">›</span>
                              <span>{note}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    );
                  })()}
                </>
              ) : null}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
