import os

# Tickers sob cobertura. A fonte da verdade é a tabela `settings` no banco;
# o runner Node passa a lista atual via env var AGENT_TICKERS (CSV). Quando
# rodado fora desse contexto, cai no default abaixo.
_DEFAULT_TICKERS = [
    # Originais
    "NVDA", "SMCI", "MU", "INTC", "GOOGL", "ARM", "TSLA",
    # Memória/Armazenamento
    "SNDK", "WDC",
    # Interconexão/Servidores
    "ALAB", "CRDO", "ANET",
    # Energia/Refrigeração
    "VRT",
    # Fundição/Equipamentos
    "TSM", "ASML",
]
_env_tickers = os.environ.get("AGENT_TICKERS", "")
TICKERS = [t.strip().upper() for t in _env_tickers.split(",") if t.strip()] or _DEFAULT_TICKERS

MODEL_FULL  = "claude-sonnet-4-6"          # run diária completa
MODEL_FLASH = "claude-haiku-4-5-20251001"  # varredura intradiária
MODEL_CHAT  = "claude-haiku-4-5-20251001"  # chat conversacional

MAX_TOKENS = 4096
MAX_AGENT_TURNS = 20

# Tickers com posição na carteira — recebem análise completa na FASE 2
PORTFOLIO_TICKERS = ["NVDA", "MU", "INTC", "ARM", "GOOGL", "TSLA", "SMCI"]
