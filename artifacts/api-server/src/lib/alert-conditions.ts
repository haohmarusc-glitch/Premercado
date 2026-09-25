/**
 * Alerta com MAIS DE UMA condição: dispara só quando todas passam na mesma
 * verificação.
 *
 * O caso que pediu isto: o chat recomendou "Monitorar confirmação acima de
 * $365-370 com volume >1.2x". São duas condições — preço E volume relativo — e
 * o alerta só aceitava uma.
 *
 * ## Por que o RVOL NÃO é recalculado aqui
 *
 * A especificação descreve a fórmula (`volumeAtual / (mediaVol20d × fração)`)
 * para ser implementada junto do avaliador. Implementá-la aqui seria recriar
 * exatamente o defeito que `agent/volume_intradiario.py` existe para fechar: a
 * conta já esteve DUPLICADA em tools.py e get_technicals.py, quebrou nos dois
 * ao mesmo tempo (NVDA 26/08/2026, rvol 8,89 com o real em 0,78) e a
 * duplicação era "documentada como segura" por um teste que nunca existiu.
 *
 * O `rvol` já chega pronto: `get_technicals.py` o calcula com
 * `volume_intradiario.rvol_da_sessao` e o emite no payload que o checker já
 * consome — só não estava declarado no tipo, então o valor vinha e era jogado
 * fora. Uma fonte, uma definição, e os números da tela e do alerta batem por
 * construção, que é o que a especificação pede no item 1.
 *
 * ## A abertura do pregão
 *
 * A especificação pede piso de 0,1 na fração decorrida "para evitar ruído na
 * abertura". Esse piso existiu no repo (`_RVOL_FRACAO_MINIMA`) e foi
 * SUBSTITUÍDO de propósito por algo mais forte, porque ele não resolve: às
 * 9h40, com média de 20M, o piso põe o esperado em 2M — um spike de abertura
 * passa de 1,2x sem dificuldade e o alerta dispara em ruído do mesmo jeito.
 *
 * O que o repo faz hoje é marcar `rvolSignal = "indefinido_abertura"` nos
 * primeiros ~30 minutos. Para um alerta isso é melhor que um piso: a condição
 * de RVOL simplesmente NÃO É SATISFEITA enquanto o número não é conclusivo, em
 * vez de ser satisfeita por um denominador inventado. Ver
 * `RVOL_INDEFINIDO_NAO_SATISFAZ`.
 *
 * ## RVOL de outro pregão
 *
 * O frame `period="1d"` do provedor volta com o ÚLTIMO dia negociado, não com
 * hoje: num feriado, ou numa resposta velha, o rvol chega completo, plausível e
 * referente a ontem. Nada no número denuncia isso, então a data vem junto
 * (`rvolData`) e é comparada com a data em horário de bolsa. Divergência =
 * indefinido. Ver `rvolEhDeHoje`.
 *
 * O que NÃO está aqui de propósito: a duração da sessão. Pregão curto (210
 * minutos em vez de 390) é calendário, e calendário replicado em dois idiomas é
 * a mesma armadilha da conta do rvol. A comparação de DATA basta para o que
 * este lado precisa decidir, e a duração fica só em `volume_intradiario.py`.
 */

export type IndicadorDeCondicao =
  | "price" | "changePct" | "rsi14" | "macd" | "sma20" | "sma50" | "rvol";

export const INDICADORES_DE_CONDICAO: IndicadorDeCondicao[] = [
  "price", "changePct", "rsi14", "macd", "sma20", "sma50", "rvol",
];

/**
 * Indicadores cujo "above"/"below" já descreve a condição inteira.
 *
 * MACD é sinal do histograma; MM20/MM50 são cruzamento do preço com a média.
 * Exigir nível deles rejeitaria alerta válido; aceitar nível dos OUTROS sem
 * valor criaria alerta que nunca dispara e não diz por quê.
 */
export const INDICADORES_SEM_NIVEL: IndicadorDeCondicao[] = ["macd", "sma20", "sma50"];

export type OperadorDeCondicao = "above" | "below";

export interface Condicao {
  indicator: IndicadorDeCondicao;
  op: OperadorDeCondicao;
  /** MACD não usa valor (o sinal é o próprio histograma). Os demais exigem. */
  value?: number | null;
}

/** O retrato do ticker no instante da verificação. */
export interface RetratoDoTicker {
  ticker: string;
  price?: number | null;
  changePct?: number | null;
  rsi?: number | null;
  macdHistogram?: number | null;
  sma20?: number | null;
  sma50?: number | null;
  rvol?: number | null;
  /**
   * "indefinido_abertura" | "indisponivel" | "alto" | "normal" | "baixo"
   * — ver volume_intradiario.situacao_do_rvol, que é a única fonte disto.
   */
  rvolSignal?: string | null;
  /** Data (YYYY-MM-DD) do pregão a que as barras do rvol pertencem. */
  rvolData?: string | null;
  /** Hora de bolsa (HH:MM) da última barra fechada que entrou no rvol. */
  rvolAte?: string | null;
}

export interface CondicaoAvaliada {
  condicao: Condicao;
  /** O valor lido agora. null = não veio dado. */
  atual: number | null;
  satisfeita: boolean;
  /** Por que não deu para avaliar, quando for o caso. */
  motivo?: string;
}

export interface ResultadoDaAvaliacao {
  disparou: boolean;
  condicoes: CondicaoAvaliada[];
  /** O valor da PRIMEIRA condição, que é o que `valueAtFiring` registra. */
  valorPrincipal: number | null;
}

/**
 * RVOL em pregão recém-aberto não satisfaz condição nenhuma.
 *
 * Não é "vale zero" nem "vale o que deu": é indeterminado, e condição
 * indeterminada não dispara alerta. O alerta volta a ser avaliável no ciclo
 * seguinte — perder 30 minutos de janela é mais barato que um e-mail de
 * rompimento falso, que é o que o alerta existe para evitar.
 */
export const RVOL_INDEFINIDO_NAO_SATISFAZ = "indefinido_abertura";

/** Sem barra de pregão utilizável — pré-mercado, feriado, sem base de volume. */
export const RVOL_INDISPONIVEL = "indisponivel";

/**
 * O rvol é do pregão de hoje?
 *
 * Sem `rvolData` a resposta é NÃO. Payload de antes deste campo existir não
 * pode ser tratado como "provavelmente é de hoje": o alerta composto passaria a
 * disparar com rvol de data desconhecida exatamente nos deploys em que o Python
 * e o Node estão fora de passo, que é quando o risco é maior.
 */
export function rvolEhDeHoje(
  retrato: RetratoDoTicker, dataDeHojeNaBolsa: string,
): boolean {
  return retrato.rvolData != null && retrato.rvolData === dataDeHojeNaBolsa;
}

/**
 * Por que o rvol deste retrato não serve, ou null se ele serve.
 *
 * Uma função só, com a ordem dos motivos fixada, para a tela, o e-mail e o
 * checker darem a MESMA explicação — "RVOL indisponível (abertura)" na tela e
 * "não disparou porque..." no log têm de sair da mesma decisão.
 */
export function motivoParaIgnorarRvol(
  retrato: RetratoDoTicker, dataDeHojeNaBolsa?: string,
): string | null {
  if (retrato.rvolSignal === RVOL_INDEFINIDO_NAO_SATISFAZ) {
    return "pregão com menos de 30 minutos — RVOL ainda não é conclusivo";
  }
  if (retrato.rvolSignal === RVOL_INDISPONIVEL || retrato.rvol == null) {
    return "sem RVOL do pregão de hoje";
  }
  if (dataDeHojeNaBolsa != null && !rvolEhDeHoje(retrato, dataDeHojeNaBolsa)) {
    return `RVOL é do pregão de ${retrato.rvolData ?? "data desconhecida"}, não de hoje`;
  }
  return null;
}

/** "RVOL indisponível (abertura)" — o rótulo curto para a tela. */
export function rotuloDeRvolIndisponivel(retrato: RetratoDoTicker): string {
  if (retrato.rvolSignal === RVOL_INDEFINIDO_NAO_SATISFAZ) {
    return "RVOL indisponível (abertura)";
  }
  return "RVOL indisponível";
}

function valorDoIndicador(ind: IndicadorDeCondicao, t: RetratoDoTicker): number | null {
  switch (ind) {
    case "price": return t.price ?? null;
    case "changePct": return t.changePct ?? null;
    case "rsi14": return t.rsi ?? null;
    case "macd": return t.macdHistogram ?? null;
    case "sma20": return t.sma20 ?? null;
    case "sma50": return t.sma50 ?? null;
    case "rvol": return t.rvol ?? null;
    default: return null;
  }
}

function avaliarUma(
  c: Condicao, t: RetratoDoTicker, dataDeHojeNaBolsa?: string,
): CondicaoAvaliada {
  // SMA é cruzamento: compara o PREÇO com a média, não a média com um valor.
  // Era assim no avaliador antigo (evalTechnical) e continua sendo — mudar a
  // semântica silenciosamente inverteria o sentido dos alertas que já existem.
  if (c.indicator === "sma20" || c.indicator === "sma50") {
    const sma = c.indicator === "sma20" ? t.sma20 : t.sma50;
    if (sma == null || t.price == null) {
      return { condicao: c, atual: null, satisfeita: false, motivo: "sem preço ou média" };
    }
    const satisfeita = c.op === "above" ? t.price > sma : t.price < sma;
    return { condicao: c, atual: t.price, satisfeita };
  }

  // MACD é sinal, não nível: acima = histograma positivo.
  if (c.indicator === "macd") {
    if (t.macdHistogram == null) {
      return { condicao: c, atual: null, satisfeita: false, motivo: "sem MACD" };
    }
    const satisfeita = c.op === "above" ? t.macdHistogram > 0 : t.macdHistogram < 0;
    return { condicao: c, atual: t.macdHistogram, satisfeita };
  }

  if (c.indicator === "rvol") {
    const motivo = motivoParaIgnorarRvol(t, dataDeHojeNaBolsa);
    // O valor continua visível mesmo quando não vale: a tela mostra o número e
    // diz por que ele não conta, em vez de esconder ou imprimir 0.
    if (motivo) return { condicao: c, atual: t.rvol ?? null, satisfeita: false, motivo };
  }

  const atual = valorDoIndicador(c.indicator, t);
  if (atual == null) {
    return { condicao: c, atual: null, satisfeita: false, motivo: "sem dado" };
  }
  if (c.value == null) {
    return { condicao: c, atual, satisfeita: false, motivo: "condição sem valor de corte" };
  }
  const satisfeita = c.op === "above" ? atual >= c.value : atual <= c.value;
  return { condicao: c, atual, satisfeita };
}

/**
 * Avalia TODAS as condições e devolve o detalhe de cada uma.
 *
 * Avalia todas mesmo quando a primeira já falhou, de propósito: a lista de
 * alertas mostra "preço 351,05 / 365 ❌ · RVOL 0,89 / 1,2 ❌", e para isso
 * precisa do valor atual de cada condição, não só da primeira que barrou.
 *
 * Lista vazia NÃO dispara. Um alerta sem condição dispararia sempre, e é o
 * estado em que uma migração malfeita deixaria as linhas antigas.
 *
 * `dataDeHojeNaBolsa` (de `timezone.dataDaBolsa`) liga a recusa de rvol de outro
 * pregão. Omiti-la avalia sem esse guarda -- é o que os testes de cortes puros
 * querem, e o checker sempre passa.
 */
export function avaliarCondicoes(
  condicoes: Condicao[], t: RetratoDoTicker, dataDeHojeNaBolsa?: string,
): ResultadoDaAvaliacao {
  const avaliadas = condicoes.map((c) => avaliarUma(c, t, dataDeHojeNaBolsa));
  return {
    disparou: avaliadas.length > 0 && avaliadas.every((a) => a.satisfeita),
    condicoes: avaliadas,
    valorPrincipal: avaliadas[0]?.atual ?? null,
  };
}

/** "preço 366,20 / 365 ✅ · RVOL 1,35 / 1,2 ✅" — para a tela e para o e-mail. */
export function descreverCondicoes(avaliadas: CondicaoAvaliada[]): string {
  const NOME: Record<IndicadorDeCondicao, string> = {
    price: "preço", changePct: "variação", rsi14: "RSI(14)",
    macd: "MACD", sma20: "MM20", sma50: "MM50", rvol: "RVOL",
  };
  return avaliadas.map((a) => {
    const nome = NOME[a.condicao.indicator];
    const atual = a.atual == null ? "—" : a.atual.toFixed(2);
    const alvo = a.condicao.indicator === "macd"
      ? (a.condicao.op === "above" ? "bullish" : "bearish")
      : a.condicao.value == null ? "—" : String(a.condicao.value);
    return `${nome} ${atual} / ${alvo} ${a.satisfeita ? "✅" : "❌"}`;
  }).join(" · ");
}

/**
 * Converte um alerta do formato ANTIGO (um indicador em colunas separadas) para
 * a lista de condições. Sem isto, migrar a tabela mudaria o comportamento dos
 * alertas que já existem — o item da especificação que diz "alertas existentes
 * viram conditions com 1 item (sem mudar comportamento)".
 */
export function condicoesDoAlertaAntigo(a: {
  indicator: string;
  condition: string;
  thresholdPct?: number | null;
  thresholdPrice?: number | null;
  thresholdValue?: number | null;
}): Condicao[] {
  const op: OperadorDeCondicao = a.condition === "above" ? "above" : "below";
  if (a.indicator === "price") {
    // A ordem importa e é a do checker antigo: thresholdPrice tem precedência
    // sobre thresholdPct quando os dois existem.
    if (a.thresholdPrice != null) return [{ indicator: "price", op, value: a.thresholdPrice }];
    if (a.thresholdPct != null) return [{ indicator: "changePct", op, value: a.thresholdPct }];
    return [];
  }
  if (a.indicator === "rsi") return [{ indicator: "rsi14", op, value: a.thresholdValue ?? null }];
  if (a.indicator === "macd") return [{ indicator: "macd", op, value: null }];
  if (a.indicator === "sma20") return [{ indicator: "sma20", op, value: null }];
  if (a.indicator === "sma50") return [{ indicator: "sma50", op, value: null }];
  return [];
}

/**
 * As condições deste alerta, seja ele novo ou antigo.
 *
 * **Não há backfill.** A especificação pede "alertas existentes viram
 * `conditions` com 1 item"; a conversão acontece na LEITURA, não numa migração
 * que reescreve as linhas. Duas razões:
 *
 * 1. A precedência de `thresholdPrice` sobre `thresholdPct` não é expressável em
 *    SQL sem reimplementá-la ali -- terceira cópia de uma regra que já quebrou
 *    quando tinha duas (ver `volume_intradiario.py`).
 * 2. Uma migração que escreva a condição errada num alerta que manda e-mail
 *    sobre dinheiro real é difícil de desfazer; uma derivação na leitura é
 *    sempre coerente com as colunas, e as colunas continuam intactas.
 *
 * O efeito no comportamento é o mesmo: `condicoesDoAlertaAntigo` é a única
 * conversão, e ela tem teste desde antes do schema existir.
 */
export function condicoesDoAlerta(a: {
  conditions?: unknown;
  indicator: string;
  condition: string;
  thresholdPct?: number | null;
  thresholdPrice?: number | null;
  thresholdValue?: number | null;
}): Condicao[] {
  if (Array.isArray(a.conditions) && a.conditions.length > 0) {
    return a.conditions as Condicao[];
  }
  return condicoesDoAlertaAntigo(a);
}

/**
 * Por que esta lista de condições é inválida, ou null se serve.
 *
 * Roda na CRIAÇÃO. `avaliarCondicoes` já se recusa a disparar com condição mal
 * formada, então um alerta ruim é inerte e não perigoso -- mas inerte e
 * silencioso é pior de descobrir que rejeitado na hora, e o usuário fica
 * esperando um e-mail que nunca vem.
 */
export function validarCondicoes(condicoes: unknown): string | null {
  if (!Array.isArray(condicoes) || condicoes.length === 0) {
    return "informe ao menos uma condição";
  }
  for (const [i, c] of condicoes.entries()) {
    const onde = `condição ${i + 1}`;
    if (typeof c !== "object" || c === null) return `${onde}: formato inválido`;
    const { indicator, op, value } = c as Record<string, unknown>;
    if (!INDICADORES_DE_CONDICAO.includes(indicator as IndicadorDeCondicao)) {
      return `${onde}: indicator deve ser um de ${INDICADORES_DE_CONDICAO.join(", ")}`;
    }
    if (op !== "above" && op !== "below") {
      return `${onde}: op deve ser 'above' ou 'below'`;
    }
    const semNivel = INDICADORES_SEM_NIVEL.includes(indicator as IndicadorDeCondicao);
    if (!semNivel && (typeof value !== "number" || !Number.isFinite(value))) {
      return `${onde}: ${String(indicator)} exige um valor numérico de corte`;
    }
  }
  const vistos = condicoes.map((c) => `${(c as Condicao).indicator}:${(c as Condicao).op}`);
  const repetido = vistos.find((v, i) => vistos.indexOf(v) !== i);
  if (repetido) {
    // Duas condições do mesmo indicador na mesma direção: a mais frouxa nunca
    // decide nada, e o usuário fica com um alerta que parece mais exigente do
    // que é. A faixa ("entre X e Y") se escreve com above + below.
    return `condição repetida (${repetido}) -- para uma faixa, use above e below`;
  }
  return null;
}
