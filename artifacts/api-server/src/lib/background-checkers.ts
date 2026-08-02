/**
 * Decide se ESTE processo deve rodar os checkers de fundo (preço, carteira,
 * cenário, params).
 *
 * Eles rodam num timer próprio e spawnam subprocessos Python que batem no
 * yfinance, e não têm coordenação entre processos: o guard `agentState.running`
 * do alert-checker é in-process, então duas instâncias do api-server não se
 * enxergam e cada uma roda o conjunto completo.
 *
 * Visto em produção (02/08): com um `pnpm run dev` no mesmo container do app
 * deployado, os dois conjuntos competiam por CPU e rede, e todo subprocesso
 * Python do lado deployado estourava seu timeout -- inclusive os de janela
 * generosa. A prova de que não era a rede: `get_scenario_params.py` estourou
 * 60s no processo deployado às 19:34:52 e completou os 8 tickers no processo
 * de dev às 19:37:31, na mesma máquina.
 *
 * Daí o padrão: LIGADO em produção, DESLIGADO em development. A env força
 * explicitamente, para testar um checker localmente ou rodar um worker
 * dedicado que não serve HTTP.
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
