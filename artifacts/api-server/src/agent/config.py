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
# Carteira real (Nomad), conferida posição a posição contra o extrato em
# 17/07 -- MU e INTC foram vendidos (ver "Ações Vendidas" na Carteira do
# app) e saíram; AVGO, MRVL e SKHY são posições novas. SGOV fica de fora:
# é um ETF de caixa (T-bill), sem notícia/sentimento pra analisar como as
# demais (já está em config.NO_EARNINGS_TICKERS por não ter fundamentos).
PORTFOLIO_TICKERS = (
    [t.strip().upper() for t in _env_portfolio.split(",") if t.strip()]
    or ["NVDA", "SMCI", "GOOGL", "ARM", "AVGO", "MRVL", "SKHY", "TSLA"]
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

# runner.ts passa um epoch (ms) com folga antes do SIGTERM de hard-kill --
# quando o agent loop cruza esse instante, ele força UM turno final sem
# ferramentas (tools=[]) pra escrever o relatório com o que já foi coletado,
# em vez de deixar o processo ser morto sem nunca produzir REPORT: (visto em
# produção: runs de 18-19min mortas no timeout viravam falha total, mesmo já
# tendo gasto o dinheiro das chamadas parciais). Ausente/vazio = sem deadline
# suave (ex.: rodando fora do runner.ts, como em testes/CLI manual).
_soft_deadline_ms = os.environ.get("AGENT_SOFT_DEADLINE_MS", "")
SOFT_DEADLINE_TS = float(_soft_deadline_ms) / 1000.0 if _soft_deadline_ms else None

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
