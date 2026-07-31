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
