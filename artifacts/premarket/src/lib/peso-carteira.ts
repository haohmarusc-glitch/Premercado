/**
 * O peso de uma posição na carteira — uma definição só, para a coluna e para o
 * gráfico.
 *
 * ## O problema
 *
 * A coluna "Peso" da tabela dividia pelo INVESTIDO; o gráfico "Alocação atual"
 * dividia pelo VALOR ATUAL. Relato de 25/09/2026: o gráfico mostrava NVDA
 * 47,7% / BABA 2,8% / AVGO 3,1% e a coluna, na mesma tela, 46,1% / 3,4% /
 * 3,4%.
 *
 * Nenhum dos dois estava com a conta errada — eles respondiam perguntas
 * diferentes ("quanto do meu custo está aqui" contra "quanto da carteira de
 * hoje está aqui") e nada na tela dizia qual era qual. Dois números com o
 * mesmo nome e valores diferentes, lado a lado, é pior que um número só.
 *
 * ## A definição
 *
 * Peso = valor atual da posição ÷ soma dos valores atuais das posições. É a
 * pergunta que faz sentido ao lado de um gráfico de alocação: o que a carteira
 * É hoje, não o que ela custou.
 *
 * Posição sem cotação vale zero nas DUAS pontas — ela não entra no numerador
 * nem no denominador. Somá-la ao denominador com valor zero e mostrá-la na
 * tabela com peso zero seria consistente; excluí-la de um e não do outro é o
 * que produz o desencontro de 1,6 ponto do relato.
 */

/** Valor atual de cada posição, já em USD. Sem cotação = null, nunca 0. */
export type ValorAtual = number | null;

/**
 * Uma posição candidata a entrar no peso.
 *
 * `vendida` existe porque a unificação de 25/09 acertou a MÉTRICA e deixou
 * passar a POPULAÇÃO. O denominador da coluna era somado sobre TODAS as
 * posições; o gráfico saía da lista já sem as encerradas. Bastava uma posição
 * vendida com `quantity` armazenado diferente de zero (que é o estado em que
 * `PUT /portfolio/:id` deixa a posição) para os dois discordarem de novo.
 *
 * Com os dados de 02/07/2026 -- MU e INTC totalmente vendidas, `quantity`
 * ainda em 0,4609 e 3,3558 -- o denominador da coluna era 4.358,54 contra
 * 3.462,67 do gráfico. NVDA aparecia com 29,3% na coluna e 36,9% no gráfico,
 * e a soma da coluna dava 79,4%.
 */
export interface LinhaCandidata {
  ticker: string;
  valorAtualUsd: ValorAtual;
  vendida: boolean;
}

/**
 * As linhas que contam no peso -- UMA decisão, servindo a coluna e o gráfico.
 *
 * Existe como função, e não como dois `filter` parecidos, pelo mesmo motivo
 * que `somaDosValoresAtuais` existe: o defeito era dois cálculos convivendo.
 * Agora a coluna e o gráfico não podem nem receber conjuntos diferentes.
 */
export function linhasQueContamNoPeso<T extends LinhaCandidata>(linhas: readonly T[]): T[] {
  return linhas.filter(
    (l) => !l.vendida && l.valorAtualUsd != null && Number.isFinite(l.valorAtualUsd) && l.valorAtualUsd > 0,
  );
}

/** O denominador, a partir das linhas candidatas. */
export function totalDoPeso(linhas: readonly LinhaCandidata[]): number {
  return somaDosValoresAtuais(linhasQueContamNoPeso(linhas).map((l) => l.valorAtualUsd));
}

/** As fatias do gráfico de alocação -- mesmas linhas, mesmo denominador. */
export function fatiasDaAlocacao(linhas: readonly LinhaCandidata[]): { name: string; value: number }[] {
  return linhasQueContamNoPeso(linhas).map((l) => ({ name: l.ticker, value: l.valorAtualUsd as number }));
}

/**
 * O denominador: soma dos valores atuais conhecidos.
 *
 * Existe como função, e não como um `reduce` solto em cada lugar, porque o
 * defeito foi exatamente dois `reduce` diferentes convivendo.
 */
export function somaDosValoresAtuais(valores: ValorAtual[]): number {
  let total = 0;
  for (const v of valores) {
    if (v != null && Number.isFinite(v) && v > 0) total += v;
  }
  return total;
}

/**
 * O peso em porcento. Zero quando a posição não tem cotação ou a carteira não
 * tem valor — nunca NaN: `NaN.toFixed(1)` imprime "NaN" na tela.
 */
export function pesoNaCarteira(valorAtual: ValorAtual, totalAtual: number): number {
  if (valorAtual == null || !Number.isFinite(valorAtual) || valorAtual <= 0) return 0;
  if (!Number.isFinite(totalAtual) || totalAtual <= 0) return 0;
  return (valorAtual / totalAtual) * 100;
}

/**
 * Os pesos de todas as posições de uma vez — o que um gráfico de fatias
 * precisa. Mesma conta da coluna, por construção: as duas passam por
 * `pesoNaCarteira` sobre o mesmo denominador.
 */
export function pesosDaCarteira(valores: ValorAtual[]): number[] {
  const total = somaDosValoresAtuais(valores);
  return valores.map((v) => pesoNaCarteira(v, total));
}
