import { Router, type IRouter } from "express";
import { eq } from "drizzle-orm";
import { db, usersTable } from "@workspace/db";
import { ActivityHeartbeatBody } from "@workspace/api-zod";

const router: IRouter = Router();

router.post("/activity/heartbeat", async (req, res): Promise<void> => {
  const parsed = ActivityHeartbeatBody.safeParse(req.body);
  if (!parsed.success) { res.status(400).json({ error: parsed.error.message }); return; }
  await db
    .update(usersTable)
    .set({ lastSeenAt: new Date(), lastPath: parsed.data.path })
    .where(eq(usersTable.id, req.userId!));
  res.status(204).send();
});

export default router;
