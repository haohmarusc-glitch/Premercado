import os
from .security import validate_api_key

_DEFAULT_TICKERS = [
    "NVDA", "SMCI", "MU", "INTC", "GOOGL", "ARM", "TSLA",
    "SNDK", "WDC", "ALAB", "CRDO", "ANET", "VRT", "TSM", "ASML",
]
_env_tickers = os.environ.get("AGENT_TICKERS", "")
TICKERS = [t.strip().upper() for t in _env_tickers.split(",") if t.strip()] or _DEFAULT_TICKERS
PORTFOLIO_TICKERS = ["NVDA", "MU", "INTC", "ARM", "GOOGL", "TSLA", "SMCI"]

# Free-tier Gemini models — gemini-2.5-flash is the recommended default as of mid-2026.
# When the daily quota for the primary model is exhausted, MODEL_FALLBACKS are tried in order.
MODEL_FULL = os.environ.get("GEMINI_MODEL_FULL", "gemini-2.5-flash")
MODEL_FLASH = os.environ.get("GEMINI_MODEL_FLASH", "gemini-2.5-flash")
MODEL_CHAT = os.environ.get("GEMINI_MODEL_CHAT", "gemini-2.5-flash")

_fallbacks_env = os.environ.get(
    "GEMINI_MODEL_FALLBACKS",
    "gemini-2.5-flash,gemini-2.0-flash-lite,gemini-1.5-flash-8b",
)
MODEL_FALLBACKS: list[str] = [m.strip() for m in _fallbacks_env.split(",") if m.strip()]

MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "4096"))
MAX_TOKENS_PREMARKET = int(os.environ.get("AGENT_MAX_TOKENS_PREMARKET", "512"))
MAX_TOKENS_CHAT = int(os.environ.get("AGENT_MAX_TOKENS_CHAT", "2048"))
MAX_AGENT_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "20"))
MAX_AGENT_TURNS_PREMARKET = int(os.environ.get("AGENT_MAX_TURNS_PREMARKET", "8"))

TOOL_TIMEOUT_SECONDS = int(os.environ.get("TOOL_TIMEOUT_SECONDS", "15"))
API_TIMEOUT_SECONDS = float(os.environ.get("API_TIMEOUT_SECONDS", "60.0"))
TURN_TIMEOUT_SECONDS = int(os.environ.get("TURN_TIMEOUT_SECONDS", "120"))

MAX_RETRIES = int(os.environ.get("AGENT_MAX_RETRIES", "3"))
RETRY_DELAY_BASE = float(os.environ.get("AGENT_RETRY_DELAY_BASE", "1.0"))

CACHE_TTL_SECONDS = int(os.environ.get("CACHE_TTL_SECONDS", "300"))
CACHE_ENABLED = os.environ.get("CACHE_ENABLED", "true").lower() in ("true", "1", "yes")

# Groq — OpenAI-compatible API, first non-Gemini fallback.
# Get a free key at: https://console.groq.com (login with Google, no verification required)
# Set GROQ_API_KEY as a Replit Secret to enable.
GROQ_MODEL_FULL = os.environ.get("GROQ_MODEL_FULL", "llama-3.1-8b-instant")
GROQ_MODEL_CHAT = os.environ.get("GROQ_MODEL_CHAT", "llama-3.1-8b-instant")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")

# Kimi (Moonshot AI) — second non-Gemini fallback.
# Get a free key at: https://platform.moonshot.cn/
# Set KIMI_API_KEY as a Replit Secret to enable.
KIMI_MODEL_FULL = os.environ.get("KIMI_MODEL_FULL", "moonshot-v1-32k")
KIMI_MODEL_CHAT = os.environ.get("KIMI_MODEL_CHAT", "moonshot-v1-8k")
KIMI_BASE_URL = os.environ.get("KIMI_BASE_URL", "https://api.moonshot.cn/v1")

def validate_gemini_key():
    key = os.environ.get("GEMINI_API_KEY", "")
    return validate_api_key(key, expected_prefix="AIzaSy")

def get_gemini_client_config():
    return {
        "api_key": os.environ.get("GEMINI_API_KEY"),
    }
