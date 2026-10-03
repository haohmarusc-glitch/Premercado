/**
 * O que é um lote em aberto, e quanto ele vale — uma definição só.
 *
 * ## Por que este pacote existe
 *
 * "O lote ainda está em aberto?" tinha DUAS respostas no repo:
 *
 *   - `recomputePosition` (routes/portfolio.ts):  `p.saleDate == null`
 *   - todo o resto (performance.ts, scenarios.ts, derivePosition na tela,
 *     portfolio-alerts.ts):                      `!(saleDate && salePrice)`
 *
 * Um lote com data de venda e preço nulo é FECHADO para o primeiro e ABERTO
 * para os outros quatro. É o §2b do playbook — duas contas com o mesmo nome —
 * e o `PATCH /portfolio/purchases/:id` permitia criar exatamente esse estado,
 * porque testava os dois campos independentemente (`if ("saleDate" in body)` /
 * `if ("salePrice" in body)`). A tela bloqueava; a API não.
 *
 * Mora num pacote de workspace, e não em `portfolio-math.ts` do api-server,
 * porque um dos consumidores é a TELA (`artifacts/premarket`), que não importa
 * do servidor. Mesmo precedente de `@workspace/alertas`: o avaliador de alerta
 * composto virou pacote no dia em que a tela e o checker precisaram concordar.
 *
 * ## O preço de não ter isto
 *
 * Varredura de 03/10/2026 sobre `portfolio_alert_firings` (204 disparos):
 * 98 eram comprovadamente falsos, e nenhum vinha da lógica de preço — vinham
 * de ler `portfolio_positions` sem olhar os lotes. Ver o cabeçalho de
 * `portfolio-alerts.ts` para o detalhe.
 */

/** O mínimo que define o estado de venda de um lote. */
export interface LoteDeCompra {
  saleDate?: string | null;
  salePrice?: number | string | null;
}

/** Lote com valor investido e preço de compra, para as contas de posição. */
export interface LoteComPreco extends LoteDeCompra {
  amount: number | string;
  purchasePrice?: number | string | null;
}

/**
 * Número a partir do que o Postgres devolve.
 *
 * Coluna `numeric` chega como STRING via Drizzle, mesmo com `.$type<number>()`
 * (playbook §7) — `Number(...)` antes de qualquer comparação não é zelo, é o
 * que impede `"0.0000" > 0` de ser avaliado como string.
 */
function num(v: number | string | null | undefined): number | null {
  if (v == null) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/**
 * A venda do lote está REGISTRADA E COMPLETA: tem data e tem preço utilizável.
 *
 * Preço zero ou negativo não conta como venda registrada. Não é zelo teórico:
 * `!(saleDate && salePrice)` — a forma antiga — já tratava `salePrice: 0` como
 * lote aberto por acidente de falsy, e a conta de receita (`q * salePrice`)
 * devolveria zero sem reclamar.
 */
export function vendaRegistrada(l: LoteDeCompra): boolean {
  const preco = num(l.salePrice);
  return !!(l.saleDate && String(l.saleDate).trim() && preco != null && preco > 0);
}

/**
 * O lote ainda é possuído. A ÚNICA definição — complemento exato de
 * `vendaRegistrada`, para as duas não poderem divergir.
 */
export function loteEmAberto(l: LoteDeCompra): boolean {
  return !vendaRegistrada(l);
}

export interface TotaisDaPosicao {
  quantity: number;
  avgCost: number;
  investedAmount: number;
}

/**
 * Quantidade, custo médio e investido de uma lista de lotes ABERTOS.
 *
 * `investedAmount` soma TODO o dinheiro em lote aberto (inclusive lote sem
 * preço ainda) — é o valor que o usuário realmente colocou. `quantity` e
 * `avgCost` só contam lote com preço conhecido: usar o investido total no
 * custo médio infla o custo das shares conhecidas sempre que sobra um lote sem
 * preço (aguardando backfill, ou data sem pregão no yfinance).
 *
 * Consequência que os chamadores precisam saber: com um lote sem preço,
 * `quantity * avgCost != investedAmount`, de propósito.
 */
export function totaisDosLotesAbertos(lotes: readonly LoteComPreco[]): TotaisDaPosicao {
  let investido = 0;
  let investidoComPreco = 0;
  let shares = 0;
  for (const l of lotes) {
    const amount = num(l.amount);
    if (amount == null) continue;
    investido += amount;
    const preco = num(l.purchasePrice);
    if (preco != null && preco > 0) {
      investidoComPreco += amount;
      shares += amount / preco;
    }
  }
  const avgCost = shares > 0 ? investidoComPreco / shares : 0;
  return { quantity: shares, avgCost, investedAmount: investido };
}

/** Os totais já filtrando os lotes vendidos — o atalho que quase todo chamador quer. */
export function totaisDaPosicao(lotes: readonly LoteComPreco[]): TotaisDaPosicao {
  return totaisDosLotesAbertos(lotes.filter(loteEmAberto));
}

/**
 * Piso para considerar uma posição "ativa".
 *
 * Abaixo disso é resíduo de ponto flutuante de uma posição totalmente vendida
 * (`recomputePosition` zera os três campos nesse caso).
 */
export const QUANTIDADE_MINIMA = 0.00001;

export function quantidadeAtiva(quantity: number | string | null | undefined): boolean {
  const q = num(quantity);
  return q != null && q > QUANTIDADE_MINIMA;
}

/**
 * A posição ainda é possuída, decidido pelos LOTES e não pelo campo
 * `quantity` armazenado.
 *
 * `PUT /portfolio/:id` edita `quantity`/`avgCost`/`investedAmount` direto, sem
 * recalcular dos lotes (os três campos existem para correção manual de posição
 * antiga sem lote registrado). Uma posição com todos os lotes vendidos e esse
 * campo editado depois da última venda fica travada num valor desatualizado
 * para sempre — não sobra mutação de lote que dispare `recomputePosition` e
 * corrija. Visto em produção com MU, aparecendo no Painel de Cenários e na
 * tela de Performance com os 2 lotes já vendidos.
 *
 * Sem NENHUM lote registrado, cai de volta para o `quantity` armazenado: é a
 * única fonte que existe nesse caso (posição importada por script, ou falha ao
 * criar o primeiro lote junto com a posição). Em produção, 03/10/2026, era o
 * caso do SGOV.
 */
export function posicaoAtivaPelosLotes(
  quantityArmazenado: number | string | null | undefined,
  lotes: readonly LoteDeCompra[],
): boolean {
  if (lotes.length === 0) return quantidadeAtiva(quantityArmazenado);
  return lotes.some(loteEmAberto);
}

/**
 * Um custo médio em que dá para dividir.
 *
 * Existe como função nomeada porque a ausência dela custou 69 e-mails falsos:
 * `recomputePosition` zera `avg_cost` ao vender tudo, e
 * `((price - 0) / 0) * 100` é `Infinity`, que passa em TODOS os limiares de
 * ganho de uma vez. Confirmado seis vezes em produção, sempre no ciclo de 15
 * min seguinte ao zeramento da posição — inclusive `gain:BABA:50` em
 * 02/10/2026, numa BABA comprada a 125,97 e vendida a 105,88, prejuízo de
 * 15,95%. O e-mail dizia "Infinity%".
 *
 * A guarda não depende de o filtro de posição ativa estar certo, de propósito:
 * divisão por zero não pode ser responsabilidade de um `filter` dois passos
 * acima.
 */
export function custoMedioUtilizavel(avgCost: number | string | null | undefined): number | null {
  const c = num(avgCost);
  return c != null && c > 0 ? c : null;
}

/**
 * Variação percentual contra o custo médio, ou `null` quando a conta não é
 * possível. Nunca `Infinity`, nunca `NaN`.
 */
export function variacaoContraCusto(
  price: number | string | null | undefined,
  avgCost: number | string | null | undefined,
): number | null {
  const p = num(price);
  const c = custoMedioUtilizavel(avgCost);
  if (p == null || c == null) return null;
  const pct = ((p - c) / c) * 100;
  return Number.isFinite(pct) ? pct : null;
}

/**
 * O estado de venda que o `PATCH` vai gravar é coerente?
 *
 * Devolve a mensagem do 400, ou `null` se estiver tudo bem. Valida o estado
 * RESULTANTE (corpo mesclado sobre a linha existente), não o corpo isolado:
 * editar só a data de um lote que já tem preço de venda é legítimo, e um
 * validador que olhasse apenas o corpo recusaria isso.
 *
 * Os dois nulos juntos são válidos — é o "desfazer venda" da tela.
 */
export function erroNoEstadoDeVenda(
  saleDate: string | null | undefined,
  salePrice: number | string | null | undefined,
): string | null {
  const temData = !!(saleDate && String(saleDate).trim());
  const precoBruto = salePrice == null || salePrice === "" ? null : Number(salePrice);

  if (precoBruto != null && !Number.isFinite(precoBruto)) {
    return "salePrice inválido: não é um número finito";
  }
  if (precoBruto != null && precoBruto <= 0) {
    return "salePrice deve ser maior que zero";
  }
  const temPreco = precoBruto != null;

  if (temData && !temPreco) {
    return "venda incompleta: saleDate sem salePrice. A posição sairia do cálculo de quantidade "
      + "para recomputePosition e continuaria contando como aberta para os outros leitores.";
  }
  if (temPreco && !temData) {
    return "venda incompleta: salePrice sem saleDate. Sem a data a venda não entra em nenhum "
      + "ciclo fechado nem no lucro realizado.";
  }
  return null;
}
