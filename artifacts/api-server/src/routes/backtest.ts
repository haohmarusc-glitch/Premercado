import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import path from "path";
import { getPythonBin, agentDir } from "../lib/runner";

const router: IRouter = Router();

router.post("/backtest", async (req, res): Promise<void> => {
  const { ticker, start, end, strategy } = req.body;
  if (!ticker || !start || !end) {
    res.status(400).json({ error: "ticker, start, end are required" });
    return;
  }

  const scriptPath = path.join(agentDir, "agent", "backtest.py");
  const py = spawn(getPythonBin(), [scriptPath]);

  const input = JSON.stringify({ ticker: String(ticker).toUpperCase(), start, end, strategy: strategy ?? "rsi" });
  py.stdin.write(input);
  py.stdin.end();

  let out = "";
  let err = "";
  py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
  py.stderr.on("data", (d: Buffer) => { err += d.toString(); });

  const timeout = setTimeout(() => {
    py.kill("SIGTERM");
    if (!res.headersSent) res.status(504).json({ error: "Backtest timed out" });
  }, 60_000);

  py.on("close", (code) => {
    clearTimeout(timeout);
    if (res.headersSent) return;
    if (code !== 0) {
      res.status(500).json({ error: err || "Script failed" });
      return;
    }
    try {
      res.json(JSON.parse(out));
    } catch {
      res.status(500).json({ error: "Failed to parse script output" });
    }
  });
});

export default router;
