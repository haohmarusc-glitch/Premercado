ALTER TABLE reports ADD COLUMN IF NOT EXISTS user_id integer REFERENCES users(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_reports_user_id ON reports(user_id);
