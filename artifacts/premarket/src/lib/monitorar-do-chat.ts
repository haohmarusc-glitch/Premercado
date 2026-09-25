/**
 * Transformar "Ação: Monitorar ..." do chat num alerta pré-preenchido.
 *
 * O agente escreve recomendações assim:
 *
 *     **AVGO** — Ação: Monitorar confirmação acima de **$365-370** com volume
 *     **>1.2x**. Por enquanto, segure a posição mas sem adicionar.
 *
 * São duas condições — preço E volume relativo — e até aqui elas morriam no
 * texto: quem quisesse o alerta tinha de reler a frase, traduzir "confirmação
 * acima de $365-370" para um número e digitar tudo de novo.
 *
 * ## Duas fontes, nesta ordem
 *
 * 1. **Bloco JSON do próprio agente** (`{"monitor": {...}}`), que é o caminho
 *    confiável: o agente já sabe qual ticker e quais níveis ele quis dizer.
 * 2. **Texto**, por padrão de escrita, como rede de segurança para as respostas
 *    que não trazem o bloco — inclusive as que já estão no histórico.
 *
 * ## O que este módulo NÃO faz
 *
 * Não cria alerta. Ele monta um pré-preenchimento para a tela `/alerts`, que o
 * usuário revisa e confirma. Essa separação não é cerimônia: a extração por
 * texto é palpite sobre linguagem natural, e um palpite que virasse alerta
 * automático mandaria e-mail sobre um nível que o agente nunca recomendou.
 *
 * Por isso também não inventa o que não encontrou. Faixa sem número, "monitorar"
 * sem nível, ticker ausente — devolve o que deu e deixa o campo vazio no
 * formulário. Preencher com um default seria pior que não preencher: o usuário
 * confirma o que a tela mostra, e a tela estaria afirmando algo que ninguém
 * disse.
 */

export type OperadorDeCondicao = "above" | "below";

export interface CondicaoExtraida {
  indicator: "price" | "rvol" | "changePct" | "rsi14";
  op: OperadorDeCondicao;
  value: number;
}

export interface MonitorExtraido {
  ticker: string;
  conditions: CondicaoExtraida[];
  note: string;
  /** "json" = veio do bloco do agente; "texto" = deduzido da frase. */
  origem: "json" | "texto";
}

/**
 * Bloco que o agente pode emitir junto da resposta. Aceito em bloco cercado
 * (```json) ou solto no texto -- o modelo escolhe uma das duas formas com
 * frequência parecida, e exigir só uma faria o caminho confiável falhar por
 * formatação.
 */
const BLOCO_JSON = /\{\s*"monitor"\s*:\s*\{[\s\S]*?\}\s*\}/g;

/** A linha de ação. O agente escreve "Ação:" com e sem cedilha/acento. */
const LINHA_MONITORAR = /A[çc][ãa]o\s*:\s*Monitorar\b[^\n]*/gi;

/** `**AVGO**`, `AVGO —`, `## AVGO`. Dois a cinco maiúsculas. */
const TICKER = /\b([A-Z]{2,5})\b/g;

/**
 * Palavras que parecem ticker e não são. Sem isto, "Ação: Monitorar SE o preço"
 * viraria um alerta para o ticker "SE".
 */
const NAO_E_TICKER = new Set([
  "SE", "NO", "NA", "DE", "DO", "DA", "EM", "AO", "OU", "AS", "OS", "UM",
  "USD", "BRL", "RSI", "MACD", "MM", "VWAP", "RVOL", "ATR", "IV", "ETF",
  "ET", "BRT", "IA", "AI", "PDF", "MD", "OK", "PS", "OBS", "EUA", "FED",
  "CPI", "PIB", "IPO", "MM20", "MM50", "SMA", "EMA", "ADR", "PPI",
]);

/**
 * Tira a ênfase markdown antes de ler os números.
 *
 * Isto não é cosmético. O agente escreve o nível em negrito -- `acima de
 * **$365-370**` -- e um regex ancorado em "acima de" seguido de `$` ou dígito
 * não encontra nada, porque o que vem depois de "de " é `**`. Foi o caso REAL
 * da AVGO em 25/09 que mostrou isso: casos escritos à mão sem asterisco
 * passavam, e o texto de produção não.
 *
 * Só ênfase e crase. O `$` fica, porque distingue preço de qualquer outro
 * número na frase.
 */
function semEnfase(texto: string): string {
  return texto.replace(/[*_`]+/g, "");
}

function numero(bruto: string): number | null {
  // "1,2" e "1.2" são o mesmo número aqui: o agente escreve em português e
  // mistura as duas formas na mesma resposta.
  const n = Number(bruto.replace(/\./g, ".").replace(",", "."));
  return Number.isFinite(n) ? n : null;
}

/**
 * O limite INFERIOR de uma faixa.
 *
 * "$365-370" vira 365 porque a condição é "acima de": o alerta tem de disparar
 * na entrada da faixa, não no fim dela. Usar 370 faria o alerta perder
 * exatamente o rompimento que ele foi criado para pegar.
 *
 * Para "abaixo de", o relevante é o limite SUPERIOR, pelo mesmo raciocínio
 * invertido -- "abaixo de $340-350" dispara ao entrar em 350.
 */
function nivelDaFaixa(texto: string, op: OperadorDeCondicao): number | null {
  const nums = [...texto.matchAll(/(\d+(?:[.,]\d+)?)/g)]
    .map((m) => numero(m[1]))
    .filter((n): n is number => n != null);
  if (!nums.length) return null;
  return op === "above" ? Math.min(...nums) : Math.max(...nums);
}

function tickerDoTrecho(trecho: string): string | null {
  for (const m of trecho.matchAll(TICKER)) {
    if (!NAO_E_TICKER.has(m[1])) return m[1];
  }
  return null;
}

function condicoesDaFrase(frase: string): CondicaoExtraida[] {
  const condicoes: CondicaoExtraida[] = [];

  // ── Preço ────────────────────────────────────────────────────────────────
  // "acima de $365-370", "abaixo de US$ 340", "acima de 365".
  //
  // O `$` é opcional porque o agente o omite metade das vezes, mas então é
  // preciso distinguir o preço do volume: "acima de 365 com volume >1.2x" tem
  // dois números e só um é preço. A ancoragem em "acima/abaixo de" resolve --
  // "com volume" nunca vem precedido de "de".
  const precoRe = /(acima|abaixo)\s+d[eo]\s+(?:US\$|R\$|\$)?\s*(\d+(?:[.,]\d+)?(?:\s*[-–a]\s*\$?\s*\d+(?:[.,]\d+)?)?)/i;
  const mPreco = precoRe.exec(frase);
  if (mPreco) {
    const op: OperadorDeCondicao = /acima/i.test(mPreco[1]) ? "above" : "below";
    const valor = nivelDaFaixa(mPreco[2], op);
    if (valor != null) condicoes.push({ indicator: "price", op, value: valor });
  }

  // ── Volume relativo ──────────────────────────────────────────────────────
  // "com volume >1.2x", "volume acima de 1,2x", "RVOL > 1.2".
  //
  // Exige a palavra volume/RVOL perto: um "1.2x" solto pode ser qualquer
  // múltiplo, e inventar condição de volume a partir dele daria um alerta mais
  // exigente do que o agente recomendou -- que é pior que um a menos, porque
  // silencia sem avisar.
  const volRe = /(?:volume|rvol)[^.\n]{0,24}?(>|acima\s+de|<|abaixo\s+de)?\s*(\d+(?:[.,]\d+)?)\s*x?/i;
  const mVol = volRe.exec(frase);
  if (mVol) {
    const marca = (mVol[1] ?? "").toLowerCase();
    const op: OperadorDeCondicao = marca.includes("<") || marca.includes("abaixo")
      ? "below" : "above";
    const valor = numero(mVol[2]);
    if (valor != null) condicoes.push({ indicator: "rvol", op, value: valor });
  }

  return condicoes;
}

function doBlocoJson(texto: string): MonitorExtraido[] {
  const achados: MonitorExtraido[] = [];
  for (const m of texto.matchAll(BLOCO_JSON)) {
    let dados: unknown;
    try {
      dados = JSON.parse(m[0]);
    } catch {
      // JSON truncado pelo streaming, ou com vírgula sobrando. Cai no texto.
      continue;
    }
    const mon = (dados as { monitor?: Record<string, unknown> }).monitor;
    if (!mon || typeof mon.ticker !== "string") continue;
    const brutas = Array.isArray(mon.conditions) ? mon.conditions : [];
    const conditions = brutas.flatMap((c): CondicaoExtraida[] => {
      const o = c as Record<string, unknown>;
      const ind = o.indicator;
      const op = o.op;
      const value = typeof o.value === "number" ? o.value : numero(String(o.value ?? ""));
      const indOk = ind === "price" || ind === "rvol" || ind === "changePct" || ind === "rsi14";
      if (!indOk || (op !== "above" && op !== "below") || value == null) return [];
      return [{ indicator: ind, op, value }];
    });
    if (!conditions.length) continue;
    achados.push({
      ticker: mon.ticker.toUpperCase(),
      conditions,
      note: typeof mon.note === "string" ? mon.note : "",
      origem: "json",
    });
  }
  return achados;
}

/**
 * Todos os "Monitorar" desta resposta, o do bloco JSON tendo precedência.
 *
 * Plural porque um relatório recomenda monitorar vários tickers de uma vez -- o
 * de sete tickers de 25/09 é o caso -- e devolver só o primeiro esconderia os
 * outros sem dizer nada.
 */
export function extrairMonitoresDoChat(texto: string): MonitorExtraido[] {
  const doJson = doBlocoJson(texto);
  const tickersJaVistos = new Set(doJson.map((m) => m.ticker));
  const achados = [...doJson];

  // O caminho por texto trabalha sobre a versão sem ênfase; o bloco JSON acima
  // leu o texto original, onde a cerca de código ainda existe.
  const plano = semEnfase(texto);

  for (const m of plano.matchAll(LINHA_MONITORAR)) {
    const frase = m[0];
    // O ticker costuma vir ANTES da linha de ação ("**AVGO** — Ação:
    // Monitorar"), então a busca é no trecho que antecede o match, de trás para
    // frente, e só depois na própria frase.
    const antes = plano.slice(0, m.index ?? 0);
    const ticker = tickerDoTrecho(antes.split("\n").reverse().join("\n"))
      ?? tickerDoTrecho(frase);
    if (!ticker || tickersJaVistos.has(ticker)) continue;

    const conditions = condicoesDaFrase(frase);
    if (!conditions.length) continue;

    tickersJaVistos.add(ticker);
    achados.push({ ticker, conditions, note: frase.trim(), origem: "texto" });
  }

  return achados;
}

/** O bloco JSON não deve aparecer na bolha da conversa. */
export function semBlocoDeMonitor(texto: string): string {
  return texto
    .replace(/```json\s*\{\s*"monitor"[\s\S]*?```/g, "")
    .replace(BLOCO_JSON, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

/**
 * O pré-preenchimento vira querystring de `/alerts`.
 *
 * As condições vão em JSON codificado num parâmetro só, em vez de um par de
 * campos por condição: o número de condições é variável, e um esquema
 * `cond1_ind=price&cond1_op=above&...` precisaria ser parseado nos dois lados
 * com a mesma convenção -- duas cópias de uma regra, que é a armadilha que este
 * repo já pagou duas vezes.
 */
export function urlDeAlertaPreenchido(m: MonitorExtraido): string {
  const p = new URLSearchParams();
  p.set("symbol", m.ticker);
  p.set("conditions", JSON.stringify(m.conditions));
  if (m.note) p.set("note", m.note.slice(0, 240));
  return `/alerts?${p.toString()}`;
}

/** Lê de volta o que `urlDeAlertaPreenchido` escreveu. */
export function lerCondicoesDaUrl(search: string): {
  conditions: CondicaoExtraida[];
  note: string;
} {
  const p = new URLSearchParams(search);
  const bruto = p.get("conditions");
  if (!bruto) return { conditions: [], note: p.get("note") ?? "" };
  try {
    const arr = JSON.parse(bruto);
    if (!Array.isArray(arr)) return { conditions: [], note: p.get("note") ?? "" };
    const conditions = arr.flatMap((c): CondicaoExtraida[] => {
      const o = c as Record<string, unknown>;
      const ok = (o.indicator === "price" || o.indicator === "rvol"
        || o.indicator === "changePct" || o.indicator === "rsi14")
        && (o.op === "above" || o.op === "below")
        && typeof o.value === "number" && Number.isFinite(o.value);
      return ok ? [o as unknown as CondicaoExtraida] : [];
    });
    return { conditions, note: p.get("note") ?? "" };
  } catch {
    // Querystring editada à mão, ou truncada por um cliente de e-mail. Cair para
    // formulário vazio é o certo: metade de um alerta pré-preenchido é pior que
    // nenhum, porque o usuário confirma o que a tela mostra.
    return { conditions: [], note: p.get("note") ?? "" };
  }
}
