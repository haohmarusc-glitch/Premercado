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
