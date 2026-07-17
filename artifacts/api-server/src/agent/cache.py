"""
Cache simples em disco para as chamadas de rede das ferramentas (yfinance, EDGAR,
Fear & Greed). Não tem relação com o billing da Anthropic — o objetivo aqui é
evitar refazer a mesma chamada de rede (lenta, e às vezes sujeita a rate limit)
quando várias ferramentas pedem o mesmo dado dentro da mesma execução, ou quando
o scan intraday roda de novo poucos minutos depois.

Design:
- Cache em arquivo JSON em /tmp (sobrevive entre subprocessos do mesmo container,
  mas não precisa de infraestrutura nova — nada de Redis/DB extra).
- Chave = nome lógico + args relevantes (ex.: "stock_data:NVDA").
- TTL configurável por chamada; default vem de config.CACHE_TTL_SECONDS.
- Falha aberta: qualquer erro de leitura/escrita do cache não deve quebrar a
  ferramenta — pior caso é buscar de novo, como antes de existir o cache.
"""
import inspect
import json
import os
import threading
import time
from typing import Any, Callable

from . import config

_CACHE_PATH = os.environ.get("AGENT_CACHE_PATH", "/tmp/premercado_tools_cache.json")
_mem: dict[str, tuple[float, Any]] = {}
_loaded = False
# O agent loop agora roda as ferramentas de um turno em paralelo (threads) --
# sem lock, duas threads que dão cache miss ao mesmo tempo podiam interleavar
# a escrita do JSON em _flush() e corromper o arquivo. Falha aberta continua
# valendo (ver docstring do módulo), o lock só evita a corrupção evitável.
_lock = threading.Lock()


def _load() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        if os.path.exists(_CACHE_PATH):
            with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for k, (ts, val) in raw.items():
                _mem[k] = (ts, val)
    except Exception:
        pass  # cache corrompido ou ilegível: segue sem ele


def _flush() -> None:
    try:
        with open(_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(_mem, f, ensure_ascii=False, default=str)
    except Exception:
        pass  # disco cheio, sem permissão etc.: não é crítico


def cached(key: str, ttl: int | None = None) -> Callable:
    """
    Decorator: cacheia o retorno de `fn(*args, **kwargs)` em disco por `ttl` segundos.
    `key` pode conter `{0}`, `{1}`... para incluir os args posicionais na chave
    (ex.: key="stock_data:{0}" com ticker como primeiro arg -> "stock_data:NVDA").
    """
    effective_ttl = ttl if ttl is not None else config.CACHE_TTL_SECONDS

    def decorator(fn: Callable) -> Callable:
        sig = inspect.signature(fn)

        def wrapper(*args, **kwargs):
            if not config.CACHE_ENABLED:
                return fn(*args, **kwargs)

            with _lock:
                _load()
                try:
                    # Resolve both positional and keyword args to parameter names so
                    # {0}-style keys work whether the caller used positional or keyword
                    # args (the Anthropic tool-use SDK always passes keyword args).
                    bound = sig.bind(*args, **kwargs)
                    bound.apply_defaults()
                    positional_vals = list(bound.arguments.values())
                    cache_key = key.format(*positional_vals, **bound.arguments)
                except Exception:
                    cache_key = key  # chave sem placeholders, ou args não combinam

                now = time.time()
                hit = _mem.get(cache_key)
                if hit is not None:
                    ts, value = hit
                    if now - ts < effective_ttl:
                        return value

            # A chamada de rede em si fica FORA do lock -- é a parte lenta, e
            # travar aqui serializaria as ferramentas de novo, anulando o
            # ganho de paralelizar o turno inteiro.
            result = fn(*args, **kwargs)

            # Não cacheia erros — uma falha temporária de rede não deve "travar"
            # como resposta válida pelo TTL inteiro.
            is_error = (
                (isinstance(result, dict) and "error" in result)
                or (isinstance(result, list) and result and isinstance(result[0], dict) and "error" in result[0])
                or (isinstance(result, str) and result.startswith("[erro"))
            )
            if not is_error:
                with _lock:
                    _mem[cache_key] = (now, result)
                    _flush()

            return result
        wrapper.__name__ = getattr(fn, "__name__", "wrapped")
        wrapper.__doc__ = fn.__doc__
        return wrapper

    return decorator
