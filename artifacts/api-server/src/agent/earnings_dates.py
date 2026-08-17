"""
Datas de balanço do yfinance com retry e cache em disco -- inclusive cache
VENCIDO quando a rede falha (stale-if-error).

Por que existe: `yf.Ticker.get_earnings_dates()` é uma das chamadas mais
instáveis do yfinance. Ela não passa pela cadeia de fallback do
market_data_provider (que trata série de PREÇO), e os três call sites do repo
a chamavam crua, sem retry: uma resposta vazia ou um 429 passageiro derrubava
a análise inteira daquele ticker.

Visto em produção (NBIS, 17/08/2026 11:36 BRT): o painel "Reação a earnings"
da Análise Rápida saiu com "falha na busca das datas" e a tela ficou sem
níveis, sem run-up e sem histórico de eventos -- minutos depois de o MESMO
script rodar com sucesso no terminal, contra o mesmo ticker. Intermitência
pura.

## Stale-if-error é o que realmente resolve

Retry ajuda pouco quando a causa é rate limit: tentar de novo em 2 segundos
esbarra no mesmo bloqueio. O que salva é ter a resposta anterior em disco.

E aqui isso é especialmente seguro: datas de balanço PASSADAS não mudam
nunca. Uma data futura pode ser reagendada, e é por isso que o TTL existe --
mas servir uma cópia de algumas horas atrás é incomparavelmente melhor que
servir nada, que é o que acontecia.

Contraste com o hist_cache (§"Só período longo"): lá, cachear período curto
trocaria uma chamada de rede por um NÚMERO ERRADO, o que é pior que a
chamada. Aqui não há esse risco -- o dado é uma agenda, não uma cotação.

## Mesmas convenções do hist_cache

Pickle em /tmp (o índice é DatetimeIndex com timezone, e `to_json`/`read_json`
não faz round-trip fiel disso), escrita atômica via tmp+rename, e falha
aberta: qualquer problema de disco vira "sem cache", nunca exceção.
"""
import hashlib
import os
import pickle
import sys
import time
from typing import Callable, Optional

import pandas as pd

_DIR = os.environ.get("AGENT_EARNINGS_CACHE_DIR", "/tmp/premercado_earnings_dates")

# 6h: datas passadas não mudam, e uma data futura reagendada aparece no
# máximo um quarto de dia depois. O ganho de disponibilidade compensa com
# folga -- a alternativa real não é "dado mais fresco", é "sem dado".
TTL_S = int(os.environ.get("AGENT_EARNINGS_CACHE_TTL_S", "21600"))

# 3 tentativas com 0,8s e 1,6s de espera = 2,4s a mais no pior caso. Curto de
# propósito: o script roda em subprocesso com timeout do lado Node, e um
# backoff generoso aqui só troca "sem dado" por "processo morto".
TENTATIVAS = int(os.environ.get("AGENT_EARNINGS_TENTATIVAS", "3"))
ESPERA_INICIAL_S = float(os.environ.get("AGENT_EARNINGS_ESPERA_S", "0.8"))


def _caminho(ticker: str, limit: int) -> str:
    # limit NA CHAVE: um consumidor pede 8 eventos e outro 14; servir a resposta
    # curta a quem pediu a longa truncaria o histórico em silêncio.
    nome = hashlib.sha1(f"{ticker.upper()}|{limit}".encode("utf-8")).hexdigest()
    return os.path.join(_DIR, f"{nome}.pkl")


def _carregar(caminho: str) -> tuple[Optional[pd.DataFrame], float]:
    """(DataFrame, idade_em_segundos). (None, inf) se não houver nada usável."""
    try:
        idade = time.time() - os.path.getmtime(caminho)
        with open(caminho, "rb") as f:
            df = pickle.load(f)
        if isinstance(df, pd.DataFrame) and not df.empty:
            return df, idade
    except Exception:
        pass
    return None, float("inf")


def _guardar(caminho: str, df: pd.DataFrame) -> None:
    try:
        os.makedirs(_DIR, exist_ok=True)
        tmp = f"{caminho}.{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            pickle.dump(df, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, caminho)
    except Exception as e:  # noqa: BLE001 — disco cheio não pode derrubar análise
        print(f"[earnings_dates] falha ao gravar {caminho}: {e}", file=sys.stderr, flush=True)


def buscar(
    ticker: str,
    fetch: Callable[[], Optional[pd.DataFrame]],
    *,
    limit: int = 14,
    dormir: Callable[[float], None] = time.sleep,
) -> tuple[Optional[pd.DataFrame], str, Optional[str]]:
    """Datas de balanço, com cache e retry.

    `fetch` é a chamada crua (normalmente `lambda: t.get_earnings_dates(limit=n)`)
    -- injetada em vez de construída aqui para o teste não precisar de rede e
    para o chamador reaproveitar o `yf.Ticker` que já tem.

    Devolve `(df, fonte, erro)` com fonte em:
      "cache"          — cópia fresca em disco, nem tocou na rede
      "yfinance"       — busca ao vivo bem-sucedida
      "cache_vencido"  — rede falhou, servindo cópia velha (df ainda utilizável)
      "erro"           — sem rede e sem cache; df é None e `erro` diz o porquê
    """
    caminho = _caminho(ticker, limit)
    em_disco, idade = _carregar(caminho)
    if em_disco is not None and idade <= TTL_S:
        return em_disco, "cache", None

    ultimo_erro: Optional[str] = None
    for tentativa in range(TENTATIVAS):
        try:
            df = fetch()
            if df is not None and not df.empty:
                _guardar(caminho, df)
                return df, "yfinance", None
            ultimo_erro = "yfinance devolveu resposta vazia"
        except Exception as e:  # noqa: BLE001 — rate limit/timeout/parse, todos iguais aqui
            ultimo_erro = f"{type(e).__name__}: {e}"
        if tentativa < TENTATIVAS - 1:
            dormir(ESPERA_INICIAL_S * (2 ** tentativa))

    # Rede esgotada. Cópia velha vale muito mais que nada -- e o chamador
    # recebe a marca "cache_vencido" para poder avisar quem lê.
    if em_disco is not None:
        print(
            f"[earnings_dates] {ticker}: rede falhou ({ultimo_erro}); "
            f"servindo cache de {int(idade / 60)}min atrás",
            file=sys.stderr, flush=True,
        )
        return em_disco, "cache_vencido", ultimo_erro

    return None, "erro", ultimo_erro
