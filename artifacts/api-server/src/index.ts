import app from "./app";
import { logger } from "./lib/logger";
import { startScheduler } from "./lib/scheduler";
import { startAlertChecker } from "./lib/alert-checker";
import { startPortfolioAlertChecker } from "./lib/portfolio-alerts";
import { ensureSchema } from "./lib/ensure-schema";
import { claimSeedAccountBootstrap } from "./lib/claim-seed-account";

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
  startAlertChecker();
  startPortfolioAlertChecker();
});
