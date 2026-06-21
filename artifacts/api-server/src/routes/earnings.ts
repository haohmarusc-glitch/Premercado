import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import path from "path";
import { db, settingsTable } from "@workspace/db";
import { getPythonBin, agentDir } from "../lib/runner";

const router: IRouter = Router();

router.get("/earnings", async (req, res): Promise<void> => {
  let tickers: string[] = [];
  if (req.query.tickers && typeof req.query.tickers === "string") {
    tickers = req.query.tickers.split(",").map((t) => t.trim().toUpperCase()).filter(Boolean);
  }
  if (tickers.length === 0) {
    try {
      const [settings] = await db.select().from(settingsTable).limit(1);
      if (settings?.tickers?.length) tickers = settings.tickers;
    } catch { /* ignore */ }
  }
  if (tickers.length === 0) {
    res.json([]);
    return;
  }

  const scriptPath = path.join(agentDir, "agent", "get_earnings.py");
  const py = spawn(getPythonBin(), [scriptPath, tickers.join(",")]);

  let out = "";
  let err = "";
  py.stdout.on("data", (d: Buffer) => { out += d.toString(); });
  py.stderr.on("data", (d: Buffer) => { err += d.toString(); });
  py.on("close", (code) => {
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
