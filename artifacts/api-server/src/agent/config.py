import os
from .security import validate_api_key

_DEFAULT_TICKERS = [
    "NVDA", "SMCI", "MU", "INTC", "GOOGL", "ARM", "TSLA",
    "SNDK", "WDC", "ALAB", "CRDO", "ANET", "VRT", "TSM", "ASML",
    "HCC", "AMR",
]
_env_tickers = os.environ.get("AGENT_TICKERS", "")
TICKERS = [t.strip().upper() for t in _env_tickers.split(",") if t.strip()] or _DEFAULT_TICKERS
_env_portfolio = os.environ.get("AGENT_PORTFOLIO_TICKERS", "")
PORTFOLIO_TICKERS = (
    [t.strip().upper() for t in _env_portfolio.split(",") if t.strip()]
    or ["NVDA", "MU", "INTC", "ARM", "GOOGL", "TSLA", "SMCI"]
)

MODEL_FULL = os.environ.get("ANTHROPIC_MODEL_FULL", "claude-sonnet-5")
MODEL_FLASH = os.environ.get("ANTHROPIC_MODEL_FLASH", "claude-haiku-4-5")
MODEL_CHAT = os.environ.get("ANTHROPIC_MODEL_CHAT", "claude-haiku-4-5")
MODEL_FALLBACK = os.environ.get("ANTHROPIC_MODEL_FALLBACK", "claude-haiku-4-5")

MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "4096"))
MAX_TOKENS_PREMARKET = int(os.environ.get("AGENT_MAX_TOKENS_PREMARKET", "512"))
MAX_TOKENS_CHAT = int(os.environ.get("AGENT_MAX_TOKENS_CHAT", "2048"))
MAX_AGENT_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "13"))
MAX_AGENT_TURNS_PREMARKET = int(os.environ.get("AGENT_MAX_TURNS_PREMARKET", "8"))

TOOL_TIMEOUT_SECONDS = int(os.environ.get("TOOL_TIMEOUT_SECONDS", "15"))
API_TIMEOUT_SECONDS = float(os.environ.get("API_TIMEOUT_SECONDS", "60.0"))
TURN_TIMEOUT_SECONDS = int(os.environ.get("TURN_TIMEOUT_SECONDS", "120"))

MAX_RETRIES = int(os.environ.get("AGENT_MAX_RETRIES", "3"))
RETRY_DELAY_BASE = float(os.environ.get("AGENT_RETRY_DELAY_BASE", "1.0"))

CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "300"))
CACHE_ENABLED = os.environ.get("CACHE_ENABLED", "true").lower() in ("true", "1", "yes")

# ETFs/fundos e índices nunca têm data de resultados/fundamentos no Yahoo Finance
# — consultar isso pra eles sempre falha (404 "No fundamentals data found") depois
# de um round-trip de rede completo. Pular isso de cara evita gastar o tempo de
# rede (às vezes 10s+ por chamada) numa consulta que nunca vai ter resposta.
NO_EARNINGS_TICKERS = frozenset({
    "SGOV", "BIL", "SHV", "SHY", "SPY", "QQQ", "VOO", "IVV", "VTI", "DIA",
    "AGG", "BND", "TLT", "IEF", "GOVT", "MUB", "XLK", "XLF", "XLE", "XLV",
    "SMH", "SOXX", "ARKK", "VXX", "UVXY",
})


def has_no_earnings_data(ticker: str) -> bool:
    t = (ticker or "").strip().upper()
    return t.startswith("^") or t in NO_EARNINGS_TICKERS

def validate_anthropic_key():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    return validate_api_key(key, expected_prefix="sk-ant-")

def get_anthropic_client_config():
    return {
        "api_key": os.environ.get("ANTHROPIC_API_KEY"),
        "timeout": API_TIMEOUT_SECONDS,
        "max_retries": MAX_RETRIES,
    }
