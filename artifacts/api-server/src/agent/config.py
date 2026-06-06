import os

# Tickers sob cobertura. A fonte da verdade é a tabela `settings` no banco;
# o runner Node passa a lista atual via env var AGENT_TICKERS (CSV). Quando
# rodado fora desse contexto, cai no default abaixo.
_DEFAULT_TICKERS = ["NVDA", "SMCI", "MU", "INTC", "GOOGL", "ARM", "TSLA"]
_env_tickers = os.environ.get("AGENT_TICKERS", "")
TICKERS = [t.strip().upper() for t in _env_tickers.split(",") if t.strip()] or _DEFAULT_TICKERS

MODEL = "claude-opus-4-5"
MAX_TOKENS = 4096
MAX_AGENT_TURNS = 20
