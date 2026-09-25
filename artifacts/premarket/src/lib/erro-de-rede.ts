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

/**
 * A mesma tradução, agora sabendo ler o STATUS da resposta.
 *
 * O que motivou (25/09/2026): em Configurações, falha ao salvar virava sempre
 * "Erro ao salvar — Verifique os campos e tente novamente". Para um 403 essa
 * frase é ativamente errada: `PATCH /settings` exige admin (a linha de
 * settings é global -- tickers, orçamento de IA e e-mail de notificação valem
 * para o deployment inteiro), então não há campo para verificar e tentar de
 * novo nunca vai funcionar. O usuário fica convencido de que não consegue
 * adicionar o ticker.
 *
 * Tipagem por PATO, não `instanceof ApiError`: este módulo é o tradutor de
 * falhas da interface e não deve depender do pacote gerado do cliente para
 * ser testável. Quem tem `status` numérico é resposta HTTP.
 */
export function mensagemDeFalhaHttp(erro: unknown): string {
  const status = (erro as { status?: unknown } | null)?.status;
  if (typeof status !== "number") return mensagemDeFalha(erro);

  const doServidor = (erro as { data?: { error?: unknown } | null } | null)?.data?.error;
  const detalhe = typeof doServidor === "string" && doServidor.trim() ? doServidor.trim() : null;

  if (status === 403) {
    return "Sua conta não tem permissão para salvar esta configuração — ela é global do "
      + "sistema (vale para todos) e só o administrador pode alterá-la. Os campos estão "
      + "certos; tentar de novo não resolve.";
  }
  if (status === 401) {
    return "A sessão expirou. Entre de novo e repita a alteração — nada do que você "
      + "digitou estava errado.";
  }
  if (status === 400 || status === 422) {
    return detalhe
      ? `O servidor recusou os dados: ${detalhe}`
      : "O servidor recusou os dados enviados. Confira os campos.";
  }
  if (status >= 500) {
    return `O servidor falhou ao salvar (${status}). Não é problema nos campos — a causa `
      + "está no log do app.";
  }
  return detalhe ? `${detalhe} (${status})` : `A requisição falhou (${status}).`;
}

/**
 * Uma segunda tentativa, SÓ quando a primeira morreu na rede.
 *
 * O que motivou (21/09/2026, do log do Caddy): a Análise com IA da ARM saiu
 * às 14:31:01 e o Caddy registrou `status: 0, size: 0` aos 11,8s -- nenhuma
 * resposta escrita, o cliente sumiu. Dezenove minutos antes, a MESMA análise
 * do MESMO celular levou 69,9s e voltou 200. A diferença estava no protocolo:
 * a que funcionou era HTTP/3, que sobrevive a troca de IP porque a conexão é
 * identificada por um connection ID; a que morreu era HTTP/2 sobre TCP, onde
 * mudar de rede mata a conexão pendente junto. Quem escolhe o protocolo é o
 * navegador, então não há o que configurar aqui -- a retentativa é a defesa.
 *
 * O que torna isto barato, e não um "tenta de novo e paga de novo": o
 * servidor NÃO soube que o cliente sumiu. Ele terminou a análise, pagou os
 * tokens e guardou (`cacheIA`, TTL de 10 min, chave ticker+benchmark). Então
 * a segunda tentativa cai num de dois casos, e nenhum gasta de novo:
 *
 *   - análise já terminou  -> cache por TTL, resposta imediata;
 *   - análise ainda roda   -> `coalescer` (lib/em-voo.ts) a faz pegar carona
 *                             na execução em curso.
 *
 * Só sobra custo se a primeira requisição nunca tiver CHEGADO ao servidor --
 * e aí é exatamente o custo do usuário clicando outra vez.
 *
 * UMA tentativa a mais, nunca um laço: rede fora de verdade não melhora na
 * terceira, e cada rodada é uma espera longa na cara de quem está olhando.
 * Erro do app (ticker inválido, 500 do Python) não é repetido -- repetir o
 * que o servidor já respondeu só gasta tempo e dinheiro.
 */
export async function comRetentativaDeRede<T>(
  fn: () => Promise<T>,
  esperaMs = 2000,
): Promise<T> {
  try {
    return await fn();
  } catch (erro) {
    if (!ehFalhaDeRede(erro)) throw erro;
    await new Promise((r) => setTimeout(r, esperaMs));
    return fn();
  }
}
