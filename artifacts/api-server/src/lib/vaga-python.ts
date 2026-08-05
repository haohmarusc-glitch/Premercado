import { logger } from "./logger";

/**
 * Teto de subprocessos Python simultâneos vindos de ROTA HTTP.
 *
 * Por que existe (medido em 05/08, já na Reserved VM, com CPU dedicada e sem
 * throttling -- ou seja, isto NÃO é problema de plataforma):
 *
 *   ciclo sozinho          imports 1,68s – 1,89s   (seis medições seguidas)
 *   com outro Python junto imports 4,6s / 8,0s / 20,0s / 26,0s
 *
 * Quinze vezes mais lento no pior caso. A causa é que um carregamento de
 * dashboard dispara as rotas todas de uma vez -- em 04/08 foram SETE
 * interpretadores em 148ms (alt-data, trend, technicals, news, chart,
 * market_alerts ×2), cada um carregando pandas + numpy + yfinance do zero. O
 * import é CPU puro; sete deles competindo transformam 1,7s em dezenas de
 * segundos, e todo mundo espera mais do que esperaria em fila.
 *
 * Por que não a fila serial de python-queue.ts: lá tem um checker de fundo
 * atrás, e ninguém esperando. Aqui tem um cliente na frente da tela. Serializar
 * sete chamadas somaria os tempos; o objetivo é limitar a disputa, não
 * eliminar o paralelismo.
 *
 * O agente diário e o chat NÃO passam por aqui, mesma razão pela qual não
 * passam pela fila: rodam por minutos, e uma vaga presa por tanto tempo
 * estrangularia o dashboard inteiro.
 */

// 3, e o valor é julgamento sobre dado ruidoso -- vale dizer em vez de fingir
// precisão. O que a medição mostra com clareza é o extremo: 1 processo custa
// 1,7s, e "vários" custa de 4,6s a 26s. Entre 2 e 5 os números não são
// monotônicos (vivos:3 deu 8,0s e vivos:5 deu 4,6s), porque o que pesa não é
// só a contagem -- é o que mais estava rodando junto.
//
// 3 mantém o dashboard em ~3 ondas em vez de uma rajada de 7, sem somar os
// tempos como faria um teto de 1. Env pra ajustar sem deploy se a máquina
// mudar (uma VPS de 2 vCPU pode preferir 2).
const LIMITE = Math.max(1, Number(process.env["PYTHON_HTTP_CONCORRENCIA"] ?? 3) || 3);

// Espera máxima por uma vaga. Existe porque há um cliente do outro lado: sob
// rajada anormal é melhor devolver erro do que pendurar a request para sempre.
// O timeout de cada rota só começa a contar DEPOIS do spawn, então ele não
// cobriria esta espera.
const ESPERA_MAX_MS = 30_000;

// Prazo de posse. A vaga é devolvida à força depois disto, mesmo que a tarefa
// siga rodando.
//
// É a mesma invariante que python-queue.ts documenta -- "toda tarefa precisa se
// resolver sozinha em tempo limitado" -- só que aqui ela é GARANTIDA em vez de
// pedida, porque nem toda rota cumpre: routes/chart.ts spawna sem nenhum
// setTimeout, e um get_chart pendurado prenderia uma vaga para sempre.
// Devolver a vaga com o processo ainda vivo torna o teto elástico por um
// instante, o que é muito melhor que travar o pool.
const POSSE_MAX_MS = 150_000;

interface Espera {
  resolve: () => void;
  reject: (err: Error) => void;
  rotulo: string;
  desdeMs: number;
  expirador: ReturnType<typeof setTimeout>;
}

let emUso = 0;
const fila: Espera[] = [];

function proxima(): void {
  const e = fila.shift();
  if (!e) return;
  clearTimeout(e.expirador);
  emUso += 1;
  const esperou = Date.now() - e.desdeMs;
  if (esperou >= 1_000) {
    logger.info(
      { rotulo: e.rotulo, esperouMs: esperou, emUso, naFila: fila.length, limite: LIMITE },
      "Vaga Python: rota esperou por uma vaga",
    );
  }
  e.resolve();
}

function liberar(): void {
  emUso = Math.max(0, emUso - 1);
  proxima();
}

function pegarVaga(rotulo: string): Promise<void> {
  if (emUso < LIMITE) {
    emUso += 1;
    return Promise.resolve();
  }
  return new Promise<void>((resolve, reject) => {
    const espera: Espera = {
      resolve,
      reject,
      rotulo,
      desdeMs: Date.now(),
      expirador: setTimeout(() => {
        const i = fila.indexOf(espera);
        if (i >= 0) fila.splice(i, 1);
        logger.warn(
          { rotulo, esperaMaxMs: ESPERA_MAX_MS, emUso, naFila: fila.length },
          "Vaga Python: desistiu de esperar por uma vaga",
        );
        reject(new Error(`Sem vaga para rodar Python em ${ESPERA_MAX_MS}ms (${rotulo})`));
      }, ESPERA_MAX_MS),
    };
    fila.push(espera);
  });
}

/**
 * Roda `tarefa` ocupando uma das vagas. Espera se todas estiverem tomadas.
 *
 * Usar POR DENTRO do coalescer, nunca por fora: duas requests idênticas devem
 * consumir UMA vaga, e é o coalescer que as junta. Invertido, a segunda
 * ocuparia uma vaga só para esperar a primeira.
 */
export async function comVagaPython<T>(rotulo: string, tarefa: () => Promise<T>): Promise<T> {
  await pegarVaga(rotulo);

  let liberada = false;
  const soltarUmaVez = (): void => {
    if (liberada) return;
    liberada = true;
    liberar();
  };

  const prazo = setTimeout(() => {
    logger.warn(
      { rotulo, posseMaxMs: POSSE_MAX_MS, emUso, naFila: fila.length },
      "Vaga Python: prazo de posse estourado, devolvendo a vaga com a tarefa ainda em andamento",
    );
    soltarUmaVez();
  }, POSSE_MAX_MS);

  try {
    return await tarefa();
  } finally {
    clearTimeout(prazo);
    soltarUmaVez();
  }
}

/** Estado atual, para log e teste. */
export function estadoDasVagas(): { emUso: number; naFila: number; limite: number } {
  return { emUso, naFila: fila.length, limite: LIMITE };
}

/** Só para teste: devolve tudo ao estado inicial entre casos. */
export function _resetVagas(): void {
  for (const e of fila) {
    clearTimeout(e.expirador);
    e.reject(new Error("reset"));
  }
  fila.length = 0;
  emUso = 0;
}
