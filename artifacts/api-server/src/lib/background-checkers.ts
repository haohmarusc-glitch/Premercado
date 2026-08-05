/**
 * Decide se ESTE processo deve rodar os checkers de fundo por TIMER (preço,
 * carteira, cenário, params).
 *
 * Padrão atual: DESLIGADO em qualquer ambiente. Os ciclos rodam via
 * `POST /api/checkers/run` (routes/checkers.ts), disparado por um Scheduled
 * Deployment -- porque no Autoscale a CPU só é garantida DURANTE um request.
 * Medido em produção (04-05/08): por timer, boot de Python de 8-11s e imports
 * de até 156s (pandas), estourando timeouts de 120s; dentro de um request, o
 * mesmo boot leva ~0,05s. Além disso, o Autoscale mantém instâncias antigas
 * vivas nas trocas de versão, cada uma com seu próprio timer -- a instância
 * sem tráfego falhava o conjunto inteiro em todo ciclo. Sem timer, a
 * instância fantasma fica inofensiva.
 *
 * A env RUN_BACKGROUND_CHECKERS=1 força os timers ligados -- útil pra testar
 * um checker localmente ou rodar um worker dedicado fora do Autoscale.
 */
export function shouldRunBackgroundCheckers(
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  const flag = env["RUN_BACKGROUND_CHECKERS"];
  if (flag !== undefined && flag !== "") {
    return flag !== "0" && flag.toLowerCase() !== "false";
  }
  return false;
}
