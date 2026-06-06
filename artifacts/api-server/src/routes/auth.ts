import { Router, type IRouter } from "express";

const router: IRouter = Router();

router.post("/auth/login", (req, res): void => {
  const { password } = req.body as { password?: string };
  const expected = process.env.OPERATOR_PASSWORD?.trim();

  if (!expected) {
    res.status(503).json({ error: "Server not configured for authentication" });
    return;
  }

  if (!password || password !== expected) {
    res.status(401).json({ error: "Invalid password" });
    return;
  }

  req.session.authenticated = true;
  req.session.save((err) => {
    if (err) {
      res.status(500).json({ error: "Failed to create session" });
      return;
    }
    res.json({ authenticated: true });
  });
});

router.post("/auth/logout", (req, res): void => {
  req.session.destroy(() => {
    res.clearCookie("sid");
    res.json({ authenticated: false });
  });
});

router.get("/auth/me", (req, res): void => {
  res.json({ authenticated: req.session?.authenticated === true });
});

export default router;
