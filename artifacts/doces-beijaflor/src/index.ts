import { createApp, configFromEnv } from "./app.js";

const port = parseInt(process.env.PORT ?? "3300", 10);
const app = createApp(configFromEnv());

app.listen(port, () => {
  console.log(`🍬 Doces Beija-Flor no ar em http://localhost:${port}`);
});
