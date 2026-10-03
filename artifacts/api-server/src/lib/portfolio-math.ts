/**
 * Contas de carteira do lado do servidor.
 *
 * O que decide "lote em aberto", "posição ativa" e os totais de uma posição
 * mora em `@workspace/carteira`, porque a TELA precisa das mesmas respostas e
 * não importa deste pacote. Aqui só fica o que é exclusivo do servidor
 * (`carteiraParaOAgente`) e o reexport, para os chamadores existentes não
 * precisarem saber de onde vem.
 *
 * Os nomes antigos (`computeOpenLotTotals`, `isActivePosition`,
 * `isPositionActiveFromLots`) foram renomeados em vez de mantidos como alias:
 * um alias teria deixado dois nomes para a mesma conta, que é a origem de
 * metade dos defeitos listados no playbook.
 */
export {
  loteEmAberto,
  vendaRegistrada,
  totaisDosLotesAbertos,
  totaisDaPosicao,
  quantidadeAtiva,
  posicaoAtivaPelosLotes,
  custoMedioUtilizavel,
  variacaoContraCusto,
  erroNoEstadoDeVenda,
  QUANTIDADE_MINIMA,
} from "@workspace/carteira";
export type {
  LoteDeCompra,
  LoteComPreco,
  TotaisDaPosicao,
} from "@workspace/carteira";

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
