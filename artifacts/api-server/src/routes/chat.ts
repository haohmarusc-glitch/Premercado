import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import { agentDir } from "../lib/runner";
import { logger } from "../lib/logger";

const router: IRouter = Router();

router.post("/chat/message", (req, res): void => {
  const { message, history = [] } = req.body as {
    message?: string;
    history?: Array<{ role: string; content: string }>;
  };

  if (!message || typeof message !== "string" || !message.trim()) {
    res.status(400).json({ error: "message is required" });
    return;
  }

  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();

  const send = (event: string, data: string) => {
    res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  };

  const safeHistory = Array.isArray(history)
    ? history.filter((m) => m.role && typeof m.content === "string").slice(-20)
    : [];

  const py = spawn("python3", ["-m", "agent.run_chat"], {
    cwd: agentDir,
    env: {
      ...process.env,
      INTERNAL_API_URL: `http://localhost:${process.env.PORT ?? 5000}`,
      PYTHONPATH: agentDir,
      CHAT_MESSAGE: message.trim(),
      CHAT_HISTORY_JSON: JSON.stringify(safeHistory),
      OPERATOR_API_KEY: process.env.OPERATOR_API_KEY ?? "",
    },
  });

  let buf = "";

  py.stdout.on("data", (chunk: Buffer) => {
    buf += chunk.toString();
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";

    for (const line of lines) {
      if (line.startsWith("STEP:")) {
        send("step", line.slice(5).trim());
      } else if (line.startsWith("RESULT:")) {
        try {
          const text = JSON.parse(line.slice(7)) as string;
          send("done", text);
        } catch {
          send("done", line.slice(7));
        }
      }
    }
  });

  py.stderr.on("data", (chunk: Buffer) => {
    logger.warn({ stderr: chunk.toString() }, "Chat agent stderr");
  });

  py.on("error", (err) => {
    logger.error({ err }, "Failed to spawn chat subprocess");
    send("error", "Falha ao iniciar o agente.");
    res.end();
  });

  py.on("close", (code) => {
    if (code !== 0) {
      send("error", "Agente encerrou com erro.");
    }
    res.end();
  });

  req.on("close", () => {
    py.kill();
  });
});

export default router;
