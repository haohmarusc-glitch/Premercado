"""
provider_health.py — Circuit breaker simples por provedor de dado de mercado
(yfinance, stooq, ...), persistido em disco e compartilhado entre processos.

## Por que existe

Sem isto, cada um dos ~24 módulos que chamam yfinance descobre que o Yahoo
está bloqueando/lento a cada chamada individual — e como os checkers rodam em
processos separados a cada 5 min (README, "Jobs de background"), a mesma
descoberta ("Yahoo mudou de humor") é refeita, isolada, dezenas de vezes por
ciclo, cada uma pagando o timeout de rede inteiro antes de desistir.

Com o breaker: depois de FAILURE_THRESHOLD falhas seguidas o disjuntor abre
por `COOLDOWN_S`; toda chamada seguinte, em qualquer processo, pula direto pro
fallback sem tentar a rede de novo — economia de latência e de mais uma
chamada fadada a falhar, não de dinheiro (yfinance não tem custo por
chamada), mas o princípio de "medir antes de decidir" é o mesmo do resto do
repo.

## Falha aberta, como todo cache do projeto

Erro de leitura/escrita do arquivo de estado nunca derruba o chamador — na
pior hipótese o breaker "esquece" o estado e a próxima chamada tenta a rede
de novo, exatamente como se este módulo não existisse.

## Por que arquivo e não em memória

`market_alerts.py`, `get_quotes.py`, `get_technicals.py` etc. rodam como
processos Python distintos (spawnados pelo Node) — um dict em memória morre
com o processo e nunca vê o padrão entre ciclos. Mesmo padrão de
`hist_cache.py`: arquivo em `/tmp`, escrita atômica via tmp+`os.replace`.

## O disjuntor é por PROVEDOR, não por ticker

`record_failure("yfinance")` não distingue "o Yahoo caiu" de "este ticker foi
deslistado". Um ticker morto falharia em qualquer fonte e não deveria
penalizar o provedor inteiro. O threshold de 3 falhas consecutivas mitiga o
caso isolado; em módulos que varrem lote, registre sucesso/falha uma vez por
LOTE, não por símbolo.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass

_PATH = os.environ.get(
    "AGENT_PROVIDER_HEALTH_PATH", "/tmp/premercado_provider_health.json"
)

# 3 falhas seguidas antes de abrir: uma falha isolada (timeout de rede
# pontual) não deve desviar tráfego do provedor primário — só um padrão.
FAILURE_THRESHOLD = int(os.environ.get("AGENT_PROVIDER_FAILURE_THRESHOLD", "3"))

# 5 min == cadência do checker mais frequente (README, "todo ciclo"). Abrir
# por mais que isso faria o próximo ciclo nem tentar o provedor primário
# mesmo que ele já tenha se recuperado.
COOLDOWN_S = int(os.environ.get("AGENT_PROVIDER_COOLDOWN_S", "300"))


@dataclass
class _ProviderState:
    consecutive_failures: int = 0
    opened_until_ts: float = 0.0
    last_failure_ts: float = 0.0
    last_success_ts: float = 0.0
    total_failures: int = 0
    total_successes: int = 0


def _load() -> dict[str, _ProviderState]:
    try:
        with open(_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {k: _ProviderState(**v) for k, v in raw.items()}
    except Exception:
        return {}


def _save(state: dict[str, _ProviderState]) -> None:
    try:
        os.makedirs(os.path.dirname(_PATH) or ".", exist_ok=True)
        tmp = f"{_PATH}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: asdict(v) for k, v in state.items()}, f)
        os.replace(tmp, _PATH)
    except Exception:
        pass  # falha aberta — ver docstring do módulo


def is_open(provider: str, *, now: float | None = None) -> bool:
    """True se o disjuntor está aberto: chamador deve pular direto pro fallback."""
    now = now if now is not None else time.time()
    st = _load().get(provider)
    if st is None:
        return False
    return now < st.opened_until_ts


def record_success(provider: str) -> None:
    state = _load()
    st = state.get(provider, _ProviderState())
    st.consecutive_failures = 0
    st.opened_until_ts = 0.0
    st.last_success_ts = time.time()
    st.total_successes += 1
    state[provider] = st
    _save(state)


def record_failure(provider: str) -> None:
    state = _load()
    st = state.get(provider, _ProviderState())
    now = time.time()
    st.consecutive_failures += 1
    st.last_failure_ts = now
    st.total_failures += 1
    if st.consecutive_failures >= FAILURE_THRESHOLD:
        st.opened_until_ts = now + COOLDOWN_S
    state[provider] = st
    _save(state)


def status() -> dict:
    """Snapshot pra debug/observabilidade (usado por provider_preflight.py)."""
    now = time.time()
    out = {}
    for name, st in _load().items():
        out[name] = {
            **asdict(st),
            "open_now": now < st.opened_until_ts,
            "seconds_until_close": max(0, round(st.opened_until_ts - now)),
        }
    return out


def reset(provider: str | None = None) -> None:
    """Zera o estado (um provedor específico, ou todos). Uso manual/teste."""
    if provider is None:
        _save({})
        return
    state = _load()
    state.pop(provider, None)
    _save(state)


if __name__ == "__main__":
    import sys

    print(json.dumps(status(), indent=2, ensure_ascii=False), file=sys.stderr)
