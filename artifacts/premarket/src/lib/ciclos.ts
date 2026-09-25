/**
 * Ciclos de uma posição: da primeira compra até a quantidade voltar a zero.
 *
 * ## O problema
 *
 * A tabela "Ações Vendidas" agrupava por POSIÇÃO, não por ciclo. Como a linha
 * de posição sobrevive à venda total (ver `recomputePosition` em
 * routes/portfolio.ts), um ticker comprado, zerado e comprado de novo pode
 * cair numa linha só ou em duas — e o que decide isso é se a linha de posição
 * foi apagada e recriada no meio, não o histórico de operações.
 *
 * Foi o que o usuário viu em 25/09/2026: INTC em duas linhas (junho e
 * setembro, correto) e ARM numa só, misturando a operação de junho com a de
 * julho→setembro. O "Preço compra $328,91" da ARM era a média ponderada de
 * dois ciclos diferentes — um número que não corresponde a nenhuma decisão que
 * a pessoa tomou.
 *
 * ## A regra
 *
 * Um ciclo começa quando a quantidade sai de zero e fecha quando volta a zero.
 * Ciclo fechado é uma linha; ciclo aberto não aparece aqui (está na tabela de
 * posições).
 *
 * Ordem dos eventos: por data e, no mesmo dia, COMPRA ANTES DE VENDA. Sem essa
 * regra, uma compra e uma venda no mesmo dia poderiam fechar o ciclo antes de
 * a compra entrar, partindo em dois o que é um ciclo só.
 *
 * `EPS` existe porque a quantidade é derivada (`amount / purchasePrice`) e
 * quase nunca fecha em zero exato: 350/399.73 vendido inteiro deixa resíduo na
 * 16ª casa. Comparar com zero exato deixaria todo ciclo aberto para sempre.
 */

/** O lote como a tabela de compras o entrega. */
export interface LoteDeCiclo {
  ticker: string;
  purchaseDate: string;
  amount: number;
  purchasePrice?: number | null;
  saleDate?: string | null;
  salePrice?: number | null;
}

export interface Ciclo<L extends LoteDeCiclo = LoteDeCiclo> {
  ticker: string;
  /** 1, 2, 3… na ordem cronológica de ABERTURA do ciclo. */
  seq: number;
  lotes: L[];
  inicio: string;
  fim: string;
  dias: number;
  investido: number;
  receita: number;
  quantidade: number;
  lucro: number;
  lucroPct: number;
  /** Ponderados pela quantidade, nunca média simples dos preços. */
  precoMedioCompra: number;
  precoMedioVenda: number;
}

const EPS = 1e-6;

/** Lote que dá para colocar numa linha do tempo: tem preço de compra. */
function computavel(l: LoteDeCiclo): boolean {
  return l.purchasePrice != null && l.purchasePrice > 0 && l.amount > 0;
}

function vendido(l: LoteDeCiclo): boolean {
  return Boolean(l.saleDate) && l.salePrice != null && l.salePrice > 0;
}

function resumir<L extends LoteDeCiclo>(
  ticker: string, seq: number, lotes: L[], inicio: string, fim: string,
): Ciclo<L> {
  let quantidade = 0;
  let investido = 0;
  let receita = 0;
  for (const l of lotes) {
    const qty = l.amount / (l.purchasePrice as number);
    quantidade += qty;
    investido += l.amount;
    receita += qty * (l.salePrice as number);
  }
  const dias = Math.round((Date.parse(fim) - Date.parse(inicio)) / 86_400_000);
  return {
    ticker, seq, lotes, inicio, fim, dias, investido, receita, quantidade,
    lucro: receita - investido,
    lucroPct: investido > 0 ? (receita / investido - 1) * 100 : 0,
    precoMedioCompra: quantidade > 0 ? investido / quantidade : 0,
    precoMedioVenda: quantidade > 0 ? receita / quantidade : 0,
  };
}

/**
 * Os ciclos FECHADOS dos lotes recebidos, do mais recente para o mais antigo.
 *
 * Lote sem preço de compra fica de fora: sem ele não há quantidade, e sem
 * quantidade não há linha do tempo. Vem em `lotesIgnorados` para a tela poder
 * dizer que ignorou — lote que desaparece calado é lucro que não fecha com o
 * total do card e ninguém sabe por quê.
 */
export function construirCiclos<L extends LoteDeCiclo>(
  lotes: L[],
): { ciclos: Ciclo<L>[]; lotesIgnorados: L[] } {
  const ignorados: L[] = [];
  const porTicker = new Map<string, L[]>();
  for (const l of lotes) {
    if (!computavel(l)) { ignorados.push(l); continue; }
    const lista = porTicker.get(l.ticker);
    if (lista) lista.push(l); else porTicker.set(l.ticker, [l]);
  }

  const saida: Ciclo<L>[] = [];
  for (const [ticker, doTicker] of porTicker) {
    // t=0 compra, t=1 venda: o desempate do mesmo dia mora no sort.
    const eventos = doTicker.flatMap((l) => {
      const qty = l.amount / (l.purchasePrice as number);
      const e = [{ data: l.purchaseDate, delta: qty, t: 0, lote: l }];
      if (vendido(l)) e.push({ data: l.saleDate as string, delta: -qty, t: 1, lote: l });
      return e;
    }).sort((a, b) => a.data.localeCompare(b.data) || a.t - b.t);

    let aberto = 0;
    let seq = 0;
    let atual: L[] = [];
    let inicio = "";
    for (const e of eventos) {
      if (e.delta > 0) {
        if (aberto < EPS) { inicio = e.data; atual = []; }
        atual.push(e.lote);
      }
      aberto += e.delta;
      if (e.delta < 0 && aberto < EPS) {
        saida.push(resumir(ticker, ++seq, atual, inicio, e.data));
        aberto = 0;
        atual = [];
      }
    }
  }
  // Mais recente primeiro, e o desempate por ticker+seq para a ordem ser
  // estável entre renders (dois ciclos podem fechar no mesmo dia).
  saida.sort((a, b) =>
    b.fim.localeCompare(a.fim) || a.ticker.localeCompare(b.ticker) || a.seq - b.seq);
  return { ciclos: saida, lotesIgnorados: ignorados };
}

/**
 * O rótulo da linha: "ARM #1" quando o ticker tem mais de um ciclo, "ARM"
 * quando tem um só. Numerar um ciclo único é ruído.
 */
export function rotuloDoCiclo(c: Ciclo, todos: Ciclo[]): string {
  const quantos = todos.reduce((n, x) => n + (x.ticker === c.ticker ? 1 : 0), 0);
  return quantos > 1 ? `${c.ticker} #${c.seq}` : c.ticker;
}
