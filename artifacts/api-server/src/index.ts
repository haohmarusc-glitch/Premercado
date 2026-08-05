import app from "./app";
import { logger } from "./lib/logger";
import { startScheduler } from "./lib/scheduler";
import { startAlertChecker } from "./lib/alert-checker";
import { startPortfolioAlertChecker } from "./lib/portfolio-alerts";
import { startScenarioAlertChecker } from "./lib/scenario-alert-checker";
import { startScenarioParamsChecker } from "./lib/scenario-params-checker";
import { ensureSchema } from "./lib/ensure-schema";
import { claimSeedAccountBootstrap } from "./lib/claim-seed-account";
import { shouldRunBackgroundCheckers } from "./lib/background-checkers";
import { iniciarRelatoPython } from "./lib/python-spawn";
import { iniciarVigiaDosCheckers } from "./lib/checker-watchdog";

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error("PORT environment variable is required but was not provided.");
}

const port = Number(rawPort);
if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

app.listen(port, async (err) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }
  logger.info({ port }, "Server listening");
  await ensureSchema();
  await claimSeedAccountBootstrap();
  await startScheduler();
  // Fora do if: as rotas HTTP spawnam Python mesmo com os checkers de fundo
  // desligados, e são elas a principal suspeita da contenção.
  iniciarRelatoPython();
  if (shouldRunBackgroundCheckers()) {
    startAlertChecker();
    startPortfolioAlertChecker();
    startScenarioAlertChecker();
    startScenarioParamsChecker();
  } else {
    logger.info(
      { nodeEnv: process.env["NODE_ENV"] },
      "Timers de checkers desligados -- ciclos rodam via POST /api/checkers/run (RUN_BACKGROUND_CHECKERS=1 força os timers)",
    );
    // Esta linha sozinha parece intencional -- e pareceria igual se o gatilho
    // externo nunca tivesse sido criado, e nenhum alerta estivesse rodando.
    // O vigia é quem transforma essa ausência em ERROR. Ver checker-watchdog.ts.
    iniciarVigiaDosCheckers();
  }
});
