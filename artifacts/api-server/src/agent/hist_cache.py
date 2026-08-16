"""
Cache em DISCO do histórico diário do yfinance, compartilhado entre processos.

Por que existe: cada ciclo de 5 minutos baixa o histórico de 6 meses dos mesmos
tickers várias vezes, em processos diferentes que não se enxergam --
market_alerts._HIST_CACHE é um dict em memória, então morre com o processo.
Num ciclo típico de 7 tickers:

  run_checkers (bounce/overbought/ATR/squeeze) -> 6mo x 7
  get_technicals (outro processo)              -> 6mo x 7
  ciclo seguinte, 5 min depois                 -> tudo de novo

Produção 04/08: `$NVDA: possibly delisted; no price data found` para NVDA, AVGO,
MRVL, ARM e HCC -- papéis líquidos e obviamente não deslistados. Essa mensagem é
o que o yfinance diz quando a resposta vem vazia, assinatura de bloqueio por
volume. E junto vinha `orçamento esgotado com 7 pendente(s)`: nenhum ticker
completou, todos pendurados na rede.

## Só período longo, de propósito

Candle diário de 6 meses é ~125 barras, e o candle de hoje pesa pouco em
RSI/MACD/SMA -- 10 minutos de defasagem ali não muda decisão nenhuma.

Períodos CURTOS (5d, 1d) carregam a variação DO DIA, que vai direto pro
relatório. Cachear esses trocaria uma chamada de rede por um número errado, e
número errado é bem pior que chamada repetida (playbook §2: fast_info vs
.history já produziu change_pct com o SINAL trocado em produção). Intradiário
pela mesma razão -- cachear 1m mascararia justamente o pico que o checker
procura.

## Pickle, não JSON

O índice é um DatetimeIndex COM timezone e as colunas são float64. `to_json` +
`read_json` não devolve isso fielmente, e a data do último candle é usada pra
decidir "hoje" (playbook §6). Pickle round-trip é exato. O arquivo fica em /tmp,
escrito e lido só por nós -- mesma fronteira de confiança do cache JSON que já
existe em cache.py.

## Falha aberta

Qualquer erro de leitura/escrita/serialização devolve "sem cache" e a chamada
de rede acontece como antes. Um cache quebrado nunca pode ser pior que não ter
cache.
"""
import hashlib
import os
import pickle
import sys
import time
from typing import Optional

import pandas as pd

_DIR = os.environ.get("AGENT_HIST_CACHE_DIR", "/tmp/premercado_hist_cache")

# 10 min: os checkers rodam a cada 5, então o histórico longo é baixado uma vez
# a cada dois ciclos em vez de duas vezes por ciclo -- 4x menos chamada pro
# Yahoo no item mais pesado.
TTL_S = int(os.environ.get("AGENT_HIST_CACHE_TTL_S", "600"))

# Períodos em que o candle de hoje não domina o resultado. O resto NÃO entra:
# ver a seção "Só período longo" na docstring.
#
# "18mo" é o padrão do confluence_engine e ficou de fora quando esta lista foi
# escrita -- ninguém usava esse período ainda. Pelo critério acima ele se
# qualifica igual a 1y e 2y: um candle novo em 380 pregões não move EMA50 nem
# banda de Bollinger. Sem ele o módulo baixava 18 meses do Yahoo em toda
# avaliação, e a cadeia de fallback não tinha cache nenhum pra servir numa
# queda.
PERIODOS_CACHEAVEIS = frozenset({"3mo", "6mo", "1y", "18mo", "2y", "5y", "10y", "max"})


def cacheavel(period: str, interval: str = "1d") -> bool:
    """Intradiário nunca; período curto nunca."""
    return interval == "1d" and period in PERIODOS_CACHEAVEIS


def _caminho(ticker: str, period: str, auto_adjust: bool, interval: str) -> str:
    # auto_adjust NA CHAVE: market_alerts busca com False e get_technicals com
    # True, e os preços diferem (o ajustado desconta dividendos/splits). Sem
    # isso o cache serviria série ajustada pra quem pediu bruta -- corrupção
    # silenciosa, do tipo que não levanta erro e só aparece no número final.
    chave = f"{ticker}|{period}|{interval}|{int(auto_adjust)}"
    nome = hashlib.sha1(chave.encode("utf-8")).hexdigest()
    return os.path.join(_DIR, f"{nome}.pkl")


def carregar(
    ticker: str, period: str, *, auto_adjust: bool = False, interval: str = "1d"
) -> Optional[pd.DataFrame]:
    """DataFrame do disco se existir e estiver dentro do TTL; None caso contrário."""
    if not cacheavel(period, interval):
        return None
    caminho = _caminho(ticker, period, auto_adjust, interval)
    try:
        idade = time.time() - os.path.getmtime(caminho)
        if idade > TTL_S:
            return None
        with open(caminho, "rb") as f:
            df = pickle.load(f)
        return df if isinstance(df, pd.DataFrame) and not df.empty else None
    except Exception:
        return None


def guardar(
    ticker: str, period: str, df: pd.DataFrame, *,
    auto_adjust: bool = False, interval: str = "1d",
) -> None:
    if not cacheavel(period, interval) or df is None or df.empty:
        return
    caminho = _caminho(ticker, period, auto_adjust, interval)
    try:
        os.makedirs(_DIR, exist_ok=True)
        # Escreve em temporário e renomeia: dois processos do mesmo ciclo podem
        # gravar a mesma chave ao mesmo tempo, e rename é atômico no mesmo
        # filesystem -- sem isso um leitor podia pegar pickle pela metade.
        tmp = f"{caminho}.{os.getpid()}.tmp"
        with open(tmp, "wb") as f:
            pickle.dump(df, f, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, caminho)
    except Exception as e:
        print(f"[hist_cache] falha ao gravar {ticker} {period}: {e}",
              file=sys.stderr, flush=True)
