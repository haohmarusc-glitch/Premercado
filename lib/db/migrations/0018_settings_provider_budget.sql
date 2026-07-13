ALTER TABLE settings
  ADD COLUMN IF NOT EXISTS agent_provider text,
  ADD COLUMN IF NOT EXISTS daily_budget_usd numeric(10, 2),
  ADD COLUMN IF NOT EXISTS cheap_provider text NOT NULL DEFAULT 'gemini';
