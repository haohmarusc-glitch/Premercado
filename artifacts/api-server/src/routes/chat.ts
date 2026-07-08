import { Router, type IRouter } from "express";
import { spawn } from "child_process";
import { and, asc, desc, eq, sql } from "drizzle-orm";
import { db, chatSessionsTable, chatMessagesTable } from "@workspace/db";
import {
  ListChatSessionsResponse,
  GetChatMessagesParams as GetChatSessionParams,
  GetChatMessagesResponse,
  DeleteChatSessionParams,
} from "@workspace/api-zod";
import { agentDir, getPythonBin } from "../lib/runner";
import { logger } from "../lib/logger";

const router: IRouter = Router();

async function ownsSession(sessionId: number, userId: number): Promise<boolean> {
  const [row] = await db
    .select({ id: chatSessionsTable.id })
    .from(chatSessionsTable)
    .where(and(eq(chatSessionsTable.id, sessionId), eq(chatSessionsTable.userId, userId)));
  return !!row;
}

// GET /chat/sessions
router.get("/chat/sessions", async (req, res): Promise<void> => {
  const rows = await db
    .select({
      id: chatSessionsTable.id,
      title: chatSessionsTable.title,
      createdAt: chatSessionsTable.createdAt,
      updatedAt: chatSessionsTable.updatedAt,
      messageCount: sql<number>`count(${chatMessagesTable.id})::int`,
    })
    .from(chatSessionsTable)
    .leftJoin(chatMessagesTable, eq(chatMessagesTable.sessionId, chatSessionsTable.id))
    .where(eq(chatSessionsTable.userId, req.userId!))
    .groupBy(chatSessionsTable.id)
    .orderBy(desc(chatSessionsTable.updatedAt));

  res.json(
    ListChatSessionsResponse.parse(
      rows.map((r) => ({
        ...r,
        createdAt: r.createdAt.toISOString(),
        updatedAt: r.updatedAt.toISOString(),
      })),
    ),
  );
});

// GET /chat/sessions/:id/messages
router.get("/chat/sessions/:id/messages", async (req, res): Promise<void> => {
  const parsed = GetChatSessionParams.safeParse(req.params);
  if (!parsed.success) { res.status(400).json({ error: "invalid id" }); return; }
  if (!(await ownsSession(parsed.data.id, req.userId!))) { res.status(404).json({ error: "Not found" }); return; }

  const messages = await db
    .select()
    .from(chatMessagesTable)
    .where(eq(chatMessagesTable.sessionId, parsed.data.id))
    .orderBy(asc(chatMessagesTable.createdAt));

  res.json(
    GetChatMessagesResponse.parse(
      messages.map((m) => ({ ...m, createdAt: m.createdAt.toISOString() })),
    ),
  );
});

// DELETE /chat/sessions/:id
router.delete("/chat/sessions/:id", async (req, res): Promise<void> => {
  const parsed = DeleteChatSessionParams.safeParse(req.params);
  if (!parsed.success) { res.status(400).json({ error: "invalid id" }); return; }
  const deleted = await db
    .delete(chatSessionsTable)
    .where(and(eq(chatSessionsTable.id, parsed.data.id), eq(chatSessionsTable.userId, req.userId!)))
    .returning({ id: chatSessionsTable.id });
  if (!deleted.length) { res.status(404).json({ error: "Not found" }); return; }
  res.status(204).send();
});

// POST /chat/message — persists user + assistant messages, streams via SSE
router.post("/chat/message", async (req, res): Promise<void> => {
  const { message, history = [], sessionId } = req.body as {
    message?: string;
    history?: Array<{ role: string; content: string }>;
    sessionId?: number;
  };

  if (!message || typeof message !== "string" || !message.trim()) {
    res.status(400).json({ error: "message is required" });
    return;
  }

  // Persist: create or reuse session, save user message
  let currentSessionId: number;
  try {
    if (sessionId) {
      if (!(await ownsSession(sessionId, req.userId!))) {
        res.status(404).json({ error: "Session not found" });
        return;
      }
      currentSessionId = sessionId;
      await db
        .update(chatSessionsTable)
        .set({ updatedAt: new Date() })
        .where(eq(chatSessionsTable.id, sessionId));
    } else {
      const [session] = await db
        .insert(chatSessionsTable)
        .values({ title: message.trim().slice(0, 80), userId: req.userId! })
        .returning();
      currentSessionId = session.id;
    }
    await db.insert(chatMessagesTable).values({
      sessionId: currentSessionId,
      role: "user",
      content: message.trim(),
    });
  } catch (err) {
    logger.error({ err }, "Failed to persist chat session/user message");
    res.status(500).json({ error: "Database error" });
    return;
  }

  // SSE headers
  res.setHeader("Content-Type", "text/event-stream");
  res.setHeader("Cache-Control", "no-cache");
  res.setHeader("Connection", "keep-alive");
  res.flushHeaders();

  const send = (event: string, data: unknown) =>
    res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);

  // Notify frontend of the session ID immediately
  send("session", { sessionId: currentSessionId });

  const safeHistory = Array.isArray(history)
    ? history.filter((m) => m.role && typeof m.content === "string").slice(-20)
    : [];

  const py = spawn(getPythonBin(), ["-m", "agent.run_chat"], {
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
  let responseText = "";

  py.stdout.on("data", (chunk: Buffer) => {
    buf += chunk.toString();
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) {
      if (line.startsWith("STEP:")) {
        send("step", line.slice(5).trim());
      } else if (line.startsWith("RESULT:")) {
        try { responseText = JSON.parse(line.slice(7)) as string; }
        catch { responseText = line.slice(7); }
        send("done", responseText);
      } else if (line.startsWith("TITLE:")) {
        try {
          const title = JSON.parse(line.slice(6)) as string;
          void db
            .update(chatSessionsTable)
            .set({ title })
            .where(eq(chatSessionsTable.id, currentSessionId))
            .then(() => { send("title", title); })
            .catch((err: unknown) => { logger.error({ err }, "Failed to update session title"); });
        } catch { /* ignore */ }
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

  py.on("close", async (code) => {
    if (code !== 0 && !responseText) {
      send("error", "Agente encerrou com erro.");
    }
    if (responseText) {
      try {
        await db.insert(chatMessagesTable).values({
          sessionId: currentSessionId,
          role: "assistant",
          content: responseText,
        });
        await db
          .update(chatSessionsTable)
          .set({ updatedAt: new Date() })
          .where(eq(chatSessionsTable.id, currentSessionId));
      } catch (err) {
        logger.error({ err }, "Failed to persist assistant message");
      }
    }
    res.end();
  });

  req.on("close", () => { py.kill(); });
});

export default router;
