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

const emVoo = new Map<string, Promise<unknown>>();

/**
 * Roda `tarefa` sob `chave`, ou entra de carona na que já está rodando.
 *
 * A rejeição também é compartilhada, de propósito: quem entrou de carona teria
 * falhado do mesmo jeito rodando sozinho, e propagar o erro é mais honesto que
 * disparar uma segunda tentativa que o chamador não pediu.
 */
export function coalescer<T>(chave: string, tarefa: () => Promise<T>): Promise<T> {
  const existente = emVoo.get(chave) as Promise<T> | undefined;
  if (existente) {
    logger.debug({ chave, emVoo: emVoo.size }, "Coalescido: entrou de carona numa busca em andamento");
    return existente;
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

  emVoo.set(chave, promessa);
  // Anexar o handler AQUI também marca a rejeição como tratada nesta cadeia
  // derivada, então uma falha não vira unhandled rejection quando ninguém mais
  // estiver ouvindo. Quem chamou continua recebendo a rejeição pela promessa
  // original que devolvemos.
  const limpar = (): void => { emVoo.delete(chave); };
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
