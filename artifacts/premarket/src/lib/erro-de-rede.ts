/**
 * Traduz a falha que chega numa mutation/query para uma frase que diz o que
 * aconteceu e o que fazer.
 *
 * O que motivou (21/09/2026): a tela Análise Rápida mostrou, em vermelho e
 * sem mais nada, `TypeError: Failed to fetch`. É a frase crua do navegador —
 * em inglês, com o nome da classe de erro junto — e ela não diz a única coisa
 * que importa: o pedido SAIU e nada voltou, então não houve resposta de
 * aplicação nenhuma para ler. As causas prováveis (app reiniciando num deploy,
 * conexão caindo numa espera longa) não aparecem em lugar nenhum, e a rota da
 * Análise com IA espera até 245s — tempo de sobra para a conexão morrer no
 * meio.
 *
 * Pior: o servidor tinha erros que se PARECIAM com esse ("Failed to fetch
 * /trend", ver routes/analysis.ts), então nem dava para saber, lendo a tela,
 * se a falha era de rede ou do cálculo. Os dois lados foram consertados no
 * mesmo commit — aqui a tradução, lá as mensagens em português.
 *
 * O que NÃO é traduzido volta como veio: mensagem que o nosso backend
 * escreveu já está em português e já explica o caso.
 */

/** Falha de rede: o pedido saiu do navegador e nada voltou. */
export function ehFalhaDeRede(erro: unknown): boolean {
  if (!(erro instanceof Error)) return false;
  // Cada navegador tem a sua frase para a MESMA coisa: Chrome diz "Failed to
  // fetch", Firefox "NetworkError when attempting to fetch resource", Safari
  // "Load failed". Testar só a do Chrome deixaria o iPhone de fora.
  const m = erro.message.toLowerCase();
  return erro.name === "TypeError"
    && /failed to fetch|networkerror|load failed|network request failed/.test(m);
}

/**
 * Resposta que não era JSON. Acontece quando quem responde não é o app: o
 * proxy devolvendo a própria página de 502 enquanto o container sobe. O
 * `r.json()` estoura antes do `r.ok` ser olhado, e o usuário recebia
 * "SyntaxError: Unexpected token '<'".
 */
export function ehRespostaNaoJson(erro: unknown): boolean {
  if (!(erro instanceof Error)) return false;
  if (erro.name !== "SyntaxError") return false;
  const m = erro.message.toLowerCase();
  return /json|unexpected token|unexpected end of/.test(m);
}

export function mensagemDeFalha(erro: unknown): string {
  if (ehFalhaDeRede(erro)) {
    return "A conexão caiu antes da resposta chegar — o pedido saiu, nada voltou. "
      + "Normalmente é o app reiniciando (deploy) ou a rede caindo numa espera longa. "
      + "Tente de novo em alguns segundos.";
  }
  if (ehRespostaNaoJson(erro)) {
    return "O servidor respondeu algo que não é do app — em geral é a página de erro "
      + "do proxy enquanto o container sobe. Tente de novo em alguns segundos.";
  }
  if (erro instanceof Error) return erro.message;
  if (typeof erro === "string") return erro;
  return "Falha desconhecida";
}
