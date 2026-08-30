import os
# Import relativo, e isso hoje basta: desde a unificacao do spawn todo
# script roda como modulo do pacote. Antes nao bastava -- o folego_de_caixa
# rodava por runScript SEM PYTHONPATH, tentava `import config`, e o
# fallback dele (`from agent import`) morria no agent.py que fazia sombra
# ao pacote; o folego-checker falhou em silencio desde a criacao ate
# 27/08/2026.
from .security import validate_api_key

_DEFAULT_TICKERS = [
    "NVDA", "SMCI", "MU", "INTC", "GOOGL", "ARM", "TSLA",
    "SNDK", "WDC", "ALAB", "CRDO", "ANET", "VRT", "TSM", "ASML",
    "HCC", "AMR",
]
_env_tickers = os.environ.get("AGENT_TICKERS", "")
TICKERS = [t.strip().upper() for t in _env_tickers.split(",") if t.strip()] or _DEFAULT_TICKERS
_env_portfolio = os.environ.get("AGENT_PORTFOLIO_TICKERS", "")
# ÚLTIMO recurso, não a fonte da verdade.
#
# Quem manda é a carteira do banco (posições reais, filtradas pelos lotes) --
# runner.ts a lê e a passa em AGENT_PORTFOLIO_TICKERS a cada spawn, então em
# produção esta lista abaixo não é usada. Ela só entra quando o processo roda
# fora do servidor (script manual, teste) e nem banco nem env var existem.
#
# Antes de 04/08 era o contrário: o modo diário não passava a carteira, caía
# aqui, e o agente exigia observação de GOOGL e TSLA muito depois de eles saírem
# da carteira -- uma lista fixa no código competindo com a que o usuário edita
# na tela, e ganhando.
#
# Instantâneo de 17/07 (conferido posição a posição contra o extrato Nomad): MU
# e INTC vendidos, AVGO/MRVL/SKHY novas. SGOV fica de fora por ser ETF de caixa
# (T-bill), sem notícia/sentimento pra analisar (já está em NO_EARNINGS_TICKERS
# por não ter fundamentos).
PORTFOLIO_TICKERS = (
    [t.strip().upper() for t in _env_portfolio.split(",") if t.strip()]
    or ["NVDA", "SMCI", "GOOGL", "ARM", "AVGO", "MRVL", "SKHY", "TSLA"]
)

# ── Notícias (fontes múltiplas) ───────────────────────────────────────────────
# get_news/get_geopolitical_news agregam várias fontes por baixo do pano e
# devolvem UMA lista já mesclada por ticker/tema -- o LLM continua vendo uma
# ferramenta só (nada de uma tool por provedor, que gastaria turnos à toa).
# A ordem desta lista também é a PRIORIDADE de desempate no dedupe: quando a
# mesma manchete chega por duas fontes, fica a versão da que aparece antes.
# Yahoo vem primeiro por já ser a fonte validada em produção e a única que
# traz resumo junto; as demais entram pra cobrir ticker que o Yahoo não cobre
# e pra não zerar a ferramenta quando o Yahoo cai.
# 'fmp', 'finnhub' e 'alphavantage' se auto-desativam sem a respectiva chave
# (ver news_sources.py) -- deixá-las no default não custa nada em quem não
# tem chave. 'alphavantage' só participa de get_geopolitical_news (busca por
# tema, não por ticker) -- ver headlines_for_macro_topics.
_env_news_sources = os.environ.get("NEWS_SOURCES", "")
NEWS_SOURCES = [
    s.strip().lower() for s in _env_news_sources.split(",") if s.strip()
] or ["yahoo", "google_rss", "fmp", "finnhub", "alphavantage"]

NEWS_MAX_ITEMS = int(os.environ.get("NEWS_MAX_ITEMS", "6"))
# Resumo por manchete: com 3+ fontes por ticker o payload de input cresce
# rápido, e o modelo só precisa do gist pra decidir se a notícia é catalisador.
NEWS_SUMMARY_CHARS = int(os.environ.get("NEWS_SUMMARY_CHARS", "200"))
# Janela das buscas de RSS ("when:2d"): 1d perde a sexta-feira quando a run
# roda numa segunda de manhã, que é justamente quando o pré-mercado importa.
NEWS_RSS_WINDOW = os.environ.get("NEWS_RSS_WINDOW", "2d")
# Orçamento total das buscas paralelas de UMA chamada de get_news. Tem que
# ficar abaixo de TOOL_TIMEOUT_SECONDS (15s) -- fonte lenta é abandonada, as
# que responderam continuam valendo (fail-open).
NEWS_FETCH_BUDGET_S = float(os.environ.get("NEWS_FETCH_BUDGET_S", "10"))

MODEL_FULL = os.environ.get("ANTHROPIC_MODEL_FULL", "claude-sonnet-5")
MODEL_FLASH = os.environ.get("ANTHROPIC_MODEL_FLASH", "claude-haiku-4-5")
MODEL_CHAT = os.environ.get("ANTHROPIC_MODEL_CHAT", "claude-haiku-4-5")
MODEL_FALLBACK = os.environ.get("ANTHROPIC_MODEL_FALLBACK", "claude-haiku-4-5")

# 4096 -> 8192. Um turno do fluxo diário emite o fan-out inteiro numa resposta
# só (visto em produção 03/08: 9 e 12 tool_use no mesmo turno, ~2.700 tokens de
# saída em média e picos no teto). Estourar o limite no meio do JSON de um
# tool_use deixa o input incompleto, que chega à ferramenta como {} e vira
# TypeError de argumento faltando -- foi assim que aquela run terminou com 0 de
# 8 observações e US$ 0,57 gastos. O relatório final também compete pelo mesmo
# teto, e agora precisa passar de 800 caracteres (ver PREFLIGHT_MIN_CHARS).
#
# Subir o teto não custa mais por si só: max_tokens é limite, não cobrança --
# só paga o que o modelo realmente escrever. O fluxo de carteira já usava
# max(MAX_TOKENS, 8192) justamente por isso; aqui o diário só estava para trás.
MAX_TOKENS = int(os.environ.get("AGENT_MAX_TOKENS", "8192"))
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

# Prompt caching Anthropic (API Messages):
#   "5m" — ephemeral padrão (write ~1.25× input); TTL ~5 min
#   "1h" — extended (write ~2× input); TTL 1 hora — melhor para system+tools
#          estáveis reusados entre turns da mesma run e entre runs da manhã
# Histórico do loop continua em 5m (conteúdo muda a cada turno; 1h só encarece write).
# Ref: https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
_raw_cache_ttl = os.environ.get("ANTHROPIC_CACHE_TTL", "1h").strip().lower()
ANTHROPIC_CACHE_TTL = "1h" if _raw_cache_ttl in ("1h", "1hr", "60m", "3600") else "5m"


def anthropic_cache_control(ttl: str | None = None) -> dict:
    """Breakpoint de prompt cache. ttl None → ANTHROPIC_CACHE_TTL (default 1h)."""
    t = (ttl or ANTHROPIC_CACHE_TTL).strip().lower()
    if t in ("1h", "1hr", "60m"):
        return {"type": "ephemeral", "ttl": "1h"}
    return {"type": "ephemeral"}  # 5m implícito


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
