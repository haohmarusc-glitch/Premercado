ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS input_tokens integer;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS output_tokens integer;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS cache_read_tokens integer;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS cache_write_tokens integer;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS cost_usd numeric(12,6);
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS llm_provider text;
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS llm_model text;
