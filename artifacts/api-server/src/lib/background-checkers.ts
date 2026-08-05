/**
 * Decide se ESTE processo deve rodar os checkers de fundo por TIMER (preço,
 * carteira, cenário, params).
 *
 * Padrão: LIGADO fora de development.
 *
 * Esse padrão já foi `false` em todo ambiente, por causa do Autoscale: lá o
 * deployment mantinha instâncias antigas vivas nas trocas de versão, cada uma
 * com seu próprio timer e sua própria fila Python, e a que não recebia tráfego
 * falhava o conjunto inteiro em todo ciclo (04/08: dois pids logando "Ciclo de
 * checkers pulado" com filas independentes, 3s de diferença). A saída foi tirar
 * o timer e disparar os ciclos por request (routes/checkers.ts).
 *
 * Numa Reserved VM esse problema não existe: um processo só, sempre de pé, com
 * CPU dedicada. Timer volta a ser o modelo certo -- e manter o padrão em
 * `false` aqui seria pior que inútil, porque sem um gatilho externo NENHUM
 * checker rodaria e o log só diria "Timers de checkers desligados", como se
 * fosse intencional.
 *
 * O endpoint `POST /api/checkers/run` continua existindo e válido: serve como
 * disparo manual e para quem rodar em plataforma que estrangule processo
 * ocioso. Com os timers ligados ele simplesmente não é chamado.
 *
 * RUN_BACKGROUND_CHECKERS força explicitamente nos dois sentidos: `1` para
 * testar um checker localmente, `0` para rodar uma instância que só serve HTTP
 * enquanto outra cuida do trabalho de fundo.
 */
export function shouldRunBackgroundCheckers(
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  const flag = env["RUN_BACKGROUND_CHECKERS"];
  if (flag !== undefined && flag !== "") {
    return flag !== "0" && flag.toLowerCase() !== "false";
  }
  return env["NODE_ENV"] !== "development";
}
