/**
 * Conta quantos subprocessos Python este processo tem VIVOS ao mesmo tempo.
 *
 * Por que existe: os checkers de fundo passam por uma fila serializada
 * (python-queue.ts), mas as rotas HTTP spawnam Python direto, uma por request,
 * sem limite e sem a fila saber que elas existem -- 13 dos 20 pontos de spawn
 * do servidor estão fora da fila. Com o frontend fazendo polling (cotações 60s,
 * alertas 60s, gráfico 15s, status do agente 5s), um miss de cache em vários ao
 * mesmo tempo sobe vários interpretadores carregando pandas+numpy+yfinance
 * juntos.
 *
 * A suspeita é que seja essa a origem da contenção medida em produção: boot do
 * interpretador em 7-12s e imports em 68-84s, contra 0,111s e 5,679s medidos no
 * mesmo container ocioso. É uma diferença de 70x e 13x que a fila não explica,
 * porque ela nunca deixa mais de um checker rodar por vez.
 *
 * Mas suspeita não é medida -- e medir por fora não serve: `ps aux` no Shell do
 * Replit enxerga o container de desenvolvimento, não o do deployment, e uma
 * janela de amostragem de um minuto tem que dar sorte de cair em cima de um
 * ciclo de 5 minutos. Quem sabe a resposta é o próprio processo que spawna.
 *
 * Não impõe teto nenhum de propósito: primeiro medir, depois decidir o número.
 * Um teto chutado agora só trocaria uma incógnita por outra.
 */
import {
  spawn,
  type ChildProcessWithoutNullStreams,
  type SpawnOptionsWithoutStdio,
} from "child_process";
import { logger } from "./logger";

/**
 * A partir daqui a concorrência deixa de ser normal e vira o que a gente está
 * caçando. 2 acontece à toa (um checker + uma rota); 3+ já é disputa.
 */
const CONCORRENCIA_NOTAVEL = 3;

let vivos = 0;
let picoDesdeUltimoRelato = 0;
const vivosPorRotulo = new Map<string, number>();

/**
 * Rótulo derivado dos próprios argumentos, nunca passado à mão: um rótulo
 * escrito no call site vira mentira assim que alguém copia o bloco pra outro
 * script e esquece de trocar. `-m agent.get_quotes` vira "agent.get_quotes";
 * um caminho de script vira o nome do arquivo.
 */
export function rotuloDoSpawn(args: readonly string[]): string {
  const i = args.indexOf("-m");
  if (i >= 0 && args[i + 1]) return args[i + 1];
  const primeiro = args.find((a) => !a.startsWith("-"));
  if (!primeiro) return "desconhecido";
  return primeiro.split("/").pop() || primeiro;
}

/**
 * Drop-in do `spawn` do child_process para subprocessos Python: mesma
 * assinatura, mesmo processo de volta. A única diferença é a contabilidade.
 *
 * `SpawnOptionsWithoutStdio` (e não `SpawnOptions`) de propósito: é o overload
 * que garante stdin/stdout/stderr NÃO nulos, que é o que todos os call sites
 * daqui usam. Com o tipo largo, cada `py.stdout.on(...)` do repo passaria a
 * exigir um `!` ou um guard -- ruído em 20 lugares para descrever uma
 * possibilidade que nenhum deles tem.
 */
export function spawnPython(
  bin: string,
  args: readonly string[],
  opts: SpawnOptionsWithoutStdio = {},
): ChildProcessWithoutNullStreams {
  const rotulo = rotuloDoSpawn(args);
  const py = spawn(bin, args as string[], opts);

  vivos += 1;
  vivosPorRotulo.set(rotulo, (vivosPorRotulo.get(rotulo) ?? 0) + 1);
  if (vivos > picoDesdeUltimoRelato) picoDesdeUltimoRelato = vivos;

  if (vivos >= CONCORRENCIA_NOTAVEL) {
    logger.warn(
      { rotulo, vivos, porRotulo: Object.fromEntries(vivosPorRotulo) },
      "Python: vários subprocessos concorrendo",
    );
  }

  // 'close' cobre saída normal e morte por sinal; 'error' cobre a falha de
  // spawnar (nesse caso 'close' pode nem vir). `once` nos dois com uma guarda
  // para o contador nunca ser decrementado duas vezes pelo mesmo processo.
  let jaContabilizado = false;
  const encerrar = (): void => {
    if (jaContabilizado) return;
    jaContabilizado = true;
    vivos -= 1;
    const n = (vivosPorRotulo.get(rotulo) ?? 1) - 1;
    if (n <= 0) vivosPorRotulo.delete(rotulo);
    else vivosPorRotulo.set(rotulo, n);
  };
  py.once("close", encerrar);
  py.once("error", encerrar);

  return py;
}

/** Quantos subprocessos Python estão vivos agora. */
export function pythonVivos(): number {
  return vivos;
}

/**
 * Pico desde a última chamada, e ZERA o pico.
 *
 * O instantâneo sozinho quase sempre pega zero -- o que interessa é o máximo
 * dentro da janela. Ler-e-zerar deixa cada relato periódico responder "qual foi
 * a pior concorrência nos últimos N minutos", que é a pergunta de verdade.
 */
export function consumirPicoPython(): number {
  const pico = picoDesdeUltimoRelato;
  picoDesdeUltimoRelato = vivos;
  return pico;
}

/** Intervalo do relato periódico: o mesmo período do ciclo dos checkers. */
const RELATO_INTERVALO_MS = 5 * 60_000;

let relatoHandle: ReturnType<typeof setInterval> | null = null;

/**
 * Liga o relato periódico do pico de concorrência.
 *
 * Independente do ciclo dos checkers de propósito: a suspeita é justamente que
 * a concorrência venha das ROTAS HTTP, e amarrar a medição ao relógio de quem
 * talvez não seja o culpado é como procurar a chave debaixo do poste.
 *
 * Silencioso quando o pico é 0 ou 1 -- log de rotina que não mudou não é
 * informação, e uma linha a cada 5 min por semanas transforma o log em ruído
 * exatamente onde a gente vai querer procurar.
 */
export function iniciarRelatoPython(): void {
  if (relatoHandle) return;
  relatoHandle = setInterval(() => {
    const pico = consumirPicoPython();
    if (pico <= 1) return;
    logger.info(
      { picoNaJanela: pico, vivosAgora: vivos, janelaMs: RELATO_INTERVALO_MS },
      // "neste processo" não é detalhe: o contador é uma variável de módulo, e
      // no Autoscale mais de uma instância roda o conjunto completo de
      // checkers ao mesmo tempo (04/08: dois pids logando "Ciclo de checkers
      // pulado" com filas independentes, com 3s de diferença). O total real da
      // máquina é a SOMA dos pids, e nenhum processo consegue medi-lo. A
      // mensagem antiga lia-se como número global e levou a concluir que não
      // havia contenção quando havia.
      "Python: pico de subprocessos simultâneos neste processo (por pid, não da máquina)",
    );
  }, RELATO_INTERVALO_MS);
  // Não segura o event loop aberto no shutdown.
  relatoHandle.unref?.();
}

/** Só para teste. */
export function _resetPythonSpawn(): void {
  vivos = 0;
  picoDesdeUltimoRelato = 0;
  vivosPorRotulo.clear();
}

/**
 * Nome de módulo do pacote `agent` a partir de como o script é chamado.
 *
 * Aceita as duas formas que existiam no repo -- `"get_trend.py"` e
 * `"/app/src/agent/get_trend.py"` -- e devolve sempre `"agent.get_trend"`.
 * Existe para que a conversão aconteça num lugar só: um `.replace` repetido
 * em vinte call sites diverge no primeiro que alguém esquecer.
 */
export function moduloDoAgente(script: string): string {
  const base = (script.split("/").pop() ?? script).replace(/\.py$/, "");
  return base.startsWith("agent.") ? base : `agent.${base}`;
}
