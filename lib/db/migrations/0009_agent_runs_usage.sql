-- Uso de LLM por execução do agente (tokens agregados + custo estimado em US$)
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS input_tokens integer;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS output_tokens integer;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS cache_read_tokens integer;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS cache_write_tokens integer;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS cost_usd numeric(12,6);
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS llm_provider text;
ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS llm_model text;
