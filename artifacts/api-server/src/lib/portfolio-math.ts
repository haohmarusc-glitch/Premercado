export interface OpenLot {
  amount: number;
  purchasePrice: number | null;
}

export interface PositionTotals {
  quantity: number;
  avgCost: number;
  investedAmount: number;
}

// Pure math, extraida pra ser testada sem banco. totalInvested soma TODO o
// dinheiro em lotes abertos (inclusive sem preco ainda) -- e o valor real que
// o usuario colocou. pricedInvested/quantity só contam lotes com preco
// conhecido: usar totalInvested no avgCost infla o custo medio das shares
// conhecidas sempre que sobrar um lote sem preco (ex: aguardando backfill,
// ou data sem pregao no yfinance).
export function computeOpenLotTotals(open: OpenLot[]): PositionTotals {
  if (open.length === 0) return { quantity: 0, avgCost: 0, investedAmount: 0 };

  let totalInvested = 0;
  let pricedInvested = 0;
  let totalShares = 0;
  for (const p of open) {
    totalInvested += p.amount;
    if (p.purchasePrice != null && p.purchasePrice > 0) {
      pricedInvested += p.amount;
      totalShares += p.amount / p.purchasePrice;
    }
  }
  const avgCost = totalShares > 0 ? pricedInvested / totalShares : 0;
  return { quantity: totalShares, avgCost, investedAmount: totalInvested };
}

// Piso pra considerar uma posição "ativa" (ainda possuída de fato) --
// abaixo disso é resíduo de ponto flutuante de uma posição totalmente
// vendida (todos os lotes com saleDate, ver recomputePosition em
// routes/portfolio.ts, que zera quantity/avgCost/investedAmount nesse
// caso). Usado só por quem precisa saber "o usuário ainda possui isso pra
// valer" (ex.: getPortfolioTickers() em runner.ts, pra não incluir um
// ticker já vendido na análise de carteira do agente) -- GET /portfolio
// (routes/portfolio.ts) NÃO filtra por isso: a Carteira do app precisa da
// posição zerada de volta pra montar a seção "Ações Vendidas".
export function isActivePosition(quantity: number | string): boolean {
  return Number(quantity) > 0.00001;
}

export interface LotSaleInfo {
  saleDate: string | null;
  salePrice: number | string | null;
}

// isActivePosition(quantity) sozinho não é confiável pra decidir se uma
// posição ainda está de fato ativa: PUT /portfolio/:id permite editar
// quantity/avgCost/investedAmount DIRETO, sem recalcular a partir dos lotes
// reais (esses três campos existem pra correção manual de posições antigas,
// ver PositionDialog no frontend). Se uma posição com todos os lotes já
// vendidos tiver esse campo editado por qualquer motivo depois da última
// venda, `quantity` fica travado num valor desatualizado pra sempre -- não
// existe mais nenhuma mutação de lote que dispare recomputePosition() e
// corrija (visto em produção: MU aparecendo no Painel de Cenários e podendo
// entrar na análise de carteira do agente com os 2 lotes já vendidos).
//
// Checa os lotes (portfolio_purchases) direto, mesma fonte de verdade já
// usada pela seção "Ações Vendidas" da Carteira (baseada em
// saleDate/salePrice de cada lote, não em quantity). Se a posição não tiver
// NENHUM lote registrado ainda -- caso raro: falha ao criar o primeiro lote
// junto com a posição no formulário "Nova posição" -- cai de volta pro
// `quantity` armazenado, única fonte disponível nesse caso.
export function isPositionActiveFromLots<T extends LotSaleInfo>(
  storedQuantity: number | string,
  lots: T[],
): boolean {
  if (lots.length === 0) return isActivePosition(storedQuantity);
  return lots.some((l) => !(l.saleDate && l.salePrice));
}

/**
 * Qual lista de "carteira" o subprocesso do agente recebe.
 *
 * Precedência: banco > env var > default do config.py (string vazia = deixa o
 * Python cair no default dele).
 *
 * Existia UMA fonte para isto e era a errada: `AGENT_PORTFOLIO_TICKERS`. Sem a
 * env var setada, o Python caía numa lista fixa no código -- que continuava
 * exigindo observação de ativos já vendidos e nunca exigia de uma posição nova.
 * Duas listas respondendo a mesma pergunta, e a que mandava não era a que o
 * usuário edita.
 *
 * A env var fica como escape hatch (rodar contra uma carteira hipotética sem
 * mexer no banco), nunca mais como fonte principal: ela não sabe quando você
 * compra ou vende.
 *
 * `escopadaAUmUsuario`: quando a lista do banco foi buscada PARA UM USUÁRIO
 * específico, vazio é RESPOSTA, não lacuna a preencher.
 *
 * Vazamento real (26/08/2026): uma conta sem posições abriu o Veredito do Dia
 * e recebeu um veredito sobre NVDA, SMCI, GOOGL, ARM, AVGO, MRVL, SKHY e TSLA
 * -- a carteira do operador, que mora em `AGENT_PORTFOLIO_TICKERS`. Os painéis
 * estruturados da mesma tela diziam, corretamente, "Sem posições na carteira".
 *
 * `getPortfolioTickers` já sabia disso e devolve `[]` de propósito, com um
 * comentário dizendo por quê: "Vazio, NUNCA um fallback fixo -- um fallback
 * compartilhado aqui devolveria a carteira de outra pessoa pra quem não tem
 * posições". Esta função desfazia isso uma camada acima.
 */
export function carteiraParaOAgente(
  doBanco: readonly string[],
  doEnv: string | undefined,
  escopadaAUmUsuario = false,
): string {
  if (doBanco.length) return doBanco.join(",");
  if (escopadaAUmUsuario) return "";
  return doEnv ?? "";
}
