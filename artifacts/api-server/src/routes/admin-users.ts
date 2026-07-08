import { Router, type IRouter } from "express";
import { asc, eq } from "drizzle-orm";
import { db, usersTable } from "@workspace/db";
import { ListAdminUsersResponse, UpdateUserPasswordParams, UpdateUserPasswordBody } from "@workspace/api-zod";
import { requireAdmin } from "../middleware/require-auth";
import { hashPassword } from "../lib/auth";

const router: IRouter = Router();

// Sem heartbeat por 90s (~3 batidas perdidas no intervalo de 30s do
// frontend) considera o usuário offline.
const ONLINE_THRESHOLD_MS = 90_000;

function serializeUser(u: typeof usersTable.$inferSelect) {
  const online = u.lastSeenAt != null && Date.now() - u.lastSeenAt.getTime() < ONLINE_THRESHOLD_MS;
  return {
    id: u.id,
    email: u.email,
    isAdmin: u.isAdmin,
    isClaimed: u.isClaimed,
    createdAt: u.createdAt.toISOString(),
    lastSeenAt: u.lastSeenAt?.toISOString() ?? null,
    lastPath: u.lastPath ?? null,
    online,
  };
}

// requireAdmin aplicado só nestas rotas específicas -- ver comentário em
// runs.ts sobre o vazamento que router.use(requireAdmin) causaria.
router.get("/admin/users", requireAdmin, async (_req, res): Promise<void> => {
  const rows = await db.select().from(usersTable).orderBy(asc(usersTable.id));
  res.json(ListAdminUsersResponse.parse(rows.map(serializeUser)));
});

router.patch("/admin/users/:id/password", requireAdmin, async (req, res): Promise<void> => {
  const p = UpdateUserPasswordParams.safeParse(req.params);
  const body = UpdateUserPasswordBody.safeParse(req.body);
  if (!p.success || !body.success) { res.status(400).json({ error: "invalid input" }); return; }

  const passwordHash = await hashPassword(body.data.newPassword);
  const [updated] = await db
    .update(usersTable)
    .set({ passwordHash, updatedAt: new Date() })
    .where(eq(usersTable.id, p.data.id))
    .returning({ id: usersTable.id });

  if (!updated) { res.status(404).json({ error: "Not found" }); return; }
  res.status(204).send();
});

export default router;
