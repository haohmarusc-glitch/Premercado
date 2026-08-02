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
 */
import { logger } from "./logger";

// Acima disso, a espera na fila vira sinal de que a serialização está apertada
// demais (tarefas se acumulando mais rápido do que drenam) e merece log.
const ESPERA_NOTAVEL_MS = 10_000;

let ultima: Promise<unknown> = Promise.resolve();

export function runExclusive<T>(label: string, tarefa: () => Promise<T>): Promise<T> {
  const enfileiradoEm = Date.now();

  const executar = async (): Promise<T> => {
    const esperou = Date.now() - enfileiradoEm;
    if (esperou >= ESPERA_NOTAVEL_MS) {
      logger.info({ label, esperouMs: esperou }, "Fila Python: tarefa esperou antes de rodar");
    }
    return tarefa();
  };

  // `then(executar, executar)` roda a próxima tarefa mesmo quando a anterior
  // rejeitou -- um timeout de um checker não pode parar a fila.
  const resultado = ultima.then(executar, executar);
  // A corrente guarda a versão silenciada: sem o catch, a rejeição desta tarefa
  // viraria unhandled rejection assim que alguém encadeasse na frente dela.
  ultima = resultado.catch(() => undefined);
  return resultado;
}

/** Só para teste: zera a corrente entre casos. */
export function _resetQueue(): void {
  ultima = Promise.resolve();
}
