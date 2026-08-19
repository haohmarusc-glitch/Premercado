/**
 * Junta chamadas idênticas que estão acontecendo AO MESMO TEMPO.
 *
 * As rotas já têm cache por TTL, e mesmo assim spawnam Python repetido: cache
 * por TTL responde "já calculei isso?", nunca "alguém já está calculando isso
 * agora?". Entre o começo e o fim de uma busca de 3 segundos, o cache continua
 * vazio, e toda request que chegar nessa janela abre o seu próprio processo.
 *
 * Medido em produção 04/08, no pico de 10 subprocessos simultâneos:
 *
 *     porRotulo: { agent.get_quotes: 2, agent.get_market_alerts_snapshot: 2, ... }
 *
 * e dois `GET /api/market-alerts` idênticos em voo (ids 72 e 78), cada um com o
 * seu interpretador carregando pandas+numpy+yfinance do zero para produzir
 * exatamente a mesma resposta.
 *
 * Aqui a segunda chamada não spawna nada: ela espera a primeira e recebe o
 * mesmo resultado. É diferente de um teto de concorrência -- isto não enfileira
 * nem atrasa ninguém, só para de fazer duas vezes o trabalho que vale uma.
 */
import { logger } from "./logger";

interface EmVoo {
  promessa: Promise<unknown>;
  desdeMs: number;
}

const emVoo = new Map<string, EmVoo>();

/**
 * Roda `tarefa` sob `chave`, ou entra de carona na que já está rodando.
 *
 * A rejeição também é compartilhada, de propósito: quem entrou de carona teria
 * falhado do mesmo jeito rodando sozinho, e propagar o erro é mais honesto que
 * disparar uma segunda tentativa que o chamador não pediu.
 *
 * `idadeMaxMs` é a exceção a essa frase, e ela existe porque a frase só vale
 * para carona em trabalho NOVO. Quem embarca num trabalho que já gastou quase
 * todo o próprio orçamento NÃO teria falhado do mesmo jeito sozinho: herda só
 * o tempo que sobrou.
 *
 * Visto em produção (18/08/2026) na Análise com IA: a conexão do celular caiu
 * aos 56s, o usuário clicou de novo, e o segundo clique foi colado ao trabalho
 * antigo -- que morreu no teto de 150s levando os dois juntos. O log mostra o
 * absurdo: uma requisição de 68s morrendo por um timeout de 150s. Rodando
 * sozinha teria tido os 150s inteiros, e 58s bastavam (havia um 200 no mesmo
 * log, com responseTime 58608).
 *
 * Com `idadeMaxMs`, o retardatário roda por fora, com orçamento cheio. Ele NÃO
 * assume a chave: quem está em voo continua dono dela até se resolver, senão
 * duas execuções disputariam a mesma entrada do Map.
 */
export function coalescer<T>(chave: string, tarefa: () => Promise<T>, idadeMaxMs?: number): Promise<T> {
  const existente = emVoo.get(chave);
  if (existente) {
    const idade = Date.now() - existente.desdeMs;
    if (idadeMaxMs === undefined || idade <= idadeMaxMs) {
      logger.debug({ chave, emVoo: emVoo.size, idade }, "Coalescido: entrou de carona numa busca em andamento");
      return existente.promessa as Promise<T>;
    }
    logger.info(
      { chave, idade, idadeMaxMs },
      "Coalescer: busca em andamento velha demais para carona, rodando por fora",
    );
    return tarefa();
  }

  let promessa: Promise<T>;
  try {
    promessa = tarefa();
  } catch (err) {
    // `tarefa` que lança de forma síncrona nunca chegou a registrar nada --
    // sem este catch, o throw escaparia antes do Map ser limpo em cenários
    // futuros em que houvesse algo a limpar.
    return Promise.reject(err);
  }

  emVoo.set(chave, { promessa, desdeMs: Date.now() });
  // Anexar o handler AQUI também marca a rejeição como tratada nesta cadeia
  // derivada, então uma falha não vira unhandled rejection quando ninguém mais
  // estiver ouvindo. Quem chamou continua recebendo a rejeição pela promessa
  // original que devolvemos.
  // Só apaga se a entrada ainda for a NOSSA: um retardatário que rodou por
  // fora não registra nada, mas se um dia registrar, apagar cegamente aqui
  // removeria a entrada de outra execução.
  const limpar = (): void => {
    if (emVoo.get(chave)?.promessa === promessa) emVoo.delete(chave);
  };
  promessa.then(limpar, limpar);

  return promessa;
}

/** Quantas buscas distintas estão em andamento agora. */
export function emVooAgora(): number {
  return emVoo.size;
}

/** Só para teste. */
export function _resetEmVoo(): void {
  emVoo.clear();
}
