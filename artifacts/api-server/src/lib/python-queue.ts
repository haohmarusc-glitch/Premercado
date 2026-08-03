/**
 * Fila global de subprocessos Python: no máximo UM de cada vez neste processo.
 *
 * Por que existe (medido em produção 02/08, com o startup_probe da #200):
 *
 *   agente rodando sozinho     boot +0,29s   imports +1,89s   total 2,18s
 *   checkers na rajada         boot +30 a +44s, e os imports NÃO fechavam
 *                              nem em 120s
 *
 * O `boot` é o interpretador subindo, antes de qualquer código nosso -- não há
 * nada que a gente controle nesse intervalo. Ele ir de 0,29s a 43,70s, e os
 * mesmos imports irem de 1,89s a mais de 120s, é assinatura de contenção: na
 * janela de 20:40:30 a 20:41:30 havia SEIS processos Python subindo quase
 * juntos (spike, bounce, squeeze, get_quotes, get_performance, get_earnings),
 * cada um carregando pandas + numpy + yfinance do zero. Eles se matavam.
 *
 * Isso também explica por que os orçamentos internos das #198/#199 não tiveram
 * efeito: eles só são consultados DEPOIS dos imports, e é durante os imports
 * que o processo morre.
 *
 * Serializar resolve pelo custo certo: a 2,2s por processo quando sozinho, os
 * quatro checkers em série custam ~9s + o trabalho real -- folgado dentro dos
 * timeouts atuais, sem precisar mexer em nenhum deles.
 *
 * INVARIANTE que mantém a fila viva: toda tarefa enfileirada precisa se
 * resolver sozinha em tempo limitado (todas têm setTimeout que mata o processo
 * e rejeita). Uma tarefa sem timeout travaria a fila inteira para sempre --
 * foi por isso que fetchQuotes, o único spawn que não tinha timeout, ganhou um
 * nesta mesma mudança.
 *
 * O agente diário NÃO passa por aqui de propósito: ele roda por minutos, e
 * enfileirá-lo bloquearia todos os checkers durante a run inteira. A separação
 * já existe pelo guard `agentState.running`, que faz os checkers pularem o
 * ciclo enquanto o agente trabalha.
 *
 * ── DESCARTE POR OBSOLESCÊNCIA ──────────────────────────────────────────────
 *
 * Serializar sozinho troca um problema por outro, e foi o que aconteceu: a
 * contenção sumiu, mas as esperas passaram a crescer de forma monotônica
 * (60s -> 330s ao longo de uma manhã) até nunca mais voltarem.
 *
 * A causa é estrutural, não de ajuste fino. O `setInterval` dos checkers
 * enfileira um lote novo a cada 5 min INDEPENDENTEMENTE de o lote anterior ter
 * drenado. Enquanto o tempo de drenagem for maior que o intervalo, a fila só
 * cresce -- e como nada aqui olhava o relógio, cada tarefa velha ainda gastava
 * um processo Python inteiro pra produzir um retrato do mercado de vários
 * minutos atrás, empurrando as seguintes mais pra trás. Sem freio, a fila não
 * tem ponto de equilíbrio.
 *
 * O freio é descartar por idade: uma tarefa periódica que esperou mais do que o
 * próprio período dela é obsoleta POR CONSTRUÇÃO -- o ciclo seguinte, com dados
 * mais novos, já está enfileirado atrás. Rodá-la não entrega informação, só
 * atrasa quem vem depois. Descartar é O(1) e devolve o ponto de equilíbrio: no
 * pior caso a fila drena um lote por período e joga fora o que envelheceu.
 *
 * Só quem é periódico usa isso (`runExclusiveFresh`). Rota HTTP continua no
 * `runExclusive` sem prazo: ali existe um cliente esperando resposta, e trocar
 * uma resposta lenta por um erro é decisão de produto, não de fila.
 */
import { logger } from "./logger";

// Acima disso, a espera na fila vira sinal de que a serialização está apertada
// demais (tarefas se acumulando mais rápido do que drenam) e merece log.
const ESPERA_NOTAVEL_MS = 10_000;

/** Resultado interno de uma tarefa descartada antes de rodar. */
const OBSOLETA = Symbol("tarefa-obsoleta");

let ultima: Promise<unknown> = Promise.resolve();
/**
 * Quantas tarefas estão enfileiradas ou rodando. Existe porque o backlog só foi
 * descoberto lendo tempo de espera em log -- profundidade é o número que mostra
 * o problema formando, antes de a espera explodir.
 */
let pendentes = 0;

function enfileirar<T>(
  label: string,
  tarefa: () => Promise<T>,
  ttlMs: number | null,
): Promise<T | typeof OBSOLETA> {
  const enfileiradoEm = Date.now();
  pendentes += 1;

  const executar = async (): Promise<T | typeof OBSOLETA> => {
    const esperou = Date.now() - enfileiradoEm;
    try {
      if (ttlMs !== null && esperou > ttlMs) {
        // Não spawna: o ciclo desta tarefa já passou e o próximo está atrás
        // dela na fila. Warn (não info) porque descarte recorrente significa
        // que a drenagem não acompanha o intervalo do checker.
        logger.warn(
          { label, esperouMs: esperou, ttlMs, pendentes },
          "Fila Python: tarefa descartada por obsolescência (esperou mais que o próprio período)",
        );
        return OBSOLETA;
      }
      if (esperou >= ESPERA_NOTAVEL_MS) {
        logger.info(
          { label, esperouMs: esperou, pendentes },
          "Fila Python: tarefa esperou antes de rodar",
        );
      }
      return await tarefa();
    } finally {
      pendentes -= 1;
    }
  };

  // `then(executar, executar)` roda a próxima tarefa mesmo quando a anterior
  // rejeitou -- um timeout de um checker não pode parar a fila.
  const resultado = ultima.then(executar, executar);
  // A corrente guarda a versão silenciada: sem o catch, a rejeição desta tarefa
  // viraria unhandled rejection assim que alguém encadeasse na frente dela.
  ultima = resultado.catch(() => undefined);
  return resultado;
}

/** Enfileira sem prazo de validade: a tarefa roda por mais que espere. */
export function runExclusive<T>(label: string, tarefa: () => Promise<T>): Promise<T> {
  return enfileirar(label, tarefa, null) as Promise<T>;
}

/**
 * Enfileira com prazo: se a vez chegar depois de `ttlMs` de espera, a tarefa é
 * descartada e a promise resolve `null` -- sem spawnar processo e sem lançar
 * erro, porque descarte é o funcionamento esperado sob carga, não falha.
 *
 * Passe como `ttlMs` o PERÍODO do checker (não o timeout dele): o que torna a
 * tarefa inútil é existir um ciclo mais novo atrás dela na fila.
 */
export function runExclusiveFresh<T>(
  label: string,
  tarefa: () => Promise<T>,
  ttlMs: number,
): Promise<T | null> {
  return enfileirar(label, tarefa, ttlMs).then((r) => (r === OBSOLETA ? null : (r as T)));
}

/** Profundidade atual da fila (enfileiradas + a que está rodando). */
export function filaPendentes(): number {
  return pendentes;
}

/** Só para teste: zera a corrente entre casos. */
export function _resetQueue(): void {
  ultima = Promise.resolve();
  pendentes = 0;
}
