import sys

# Mede quanto do tempo do processo é interpretador+import (ver
# startup_probe.py).
from agent.startup_probe import boot as _probe_boot, imports_prontos as _probe_imports

_probe_boot()

import yfinance as yf
# Serializacao que nao emite NaN/Infinity -- ver json_seguro.py.
from agent import json_seguro

from agent.bounded_parallel import deadline_exceeded

_probe_imports()

tickers = sys.argv[1].split(",") if len(sys.argv) > 1 else []
result = {}
for t in tickers:
    # Para antes de o Node matar o processo: assim o que já foi buscado ainda
    # é impresso, em vez de morrer junto com o timeout (scenarios.ts cai pro
    # custo como fallback quando não recebe nada). Ver bounded_parallel.py.
    if deadline_exceeded():
        result[t] = {"price": None, "previousClose": None, "skipped": "sem tempo"}
        continue
    try:
        ticker_obj = yf.Ticker(t)
        fi = ticker_obj.fast_info
        price = getattr(fi, "last_price", None)
        # previous_close vem do candle diario oficial (.history()), NAO de
        # fast_info.previous_close -- as duas fontes podem divergir (mesmo
        # motivo/mesmo padrão de tools.py::get_stock_data, já corrigido
        # por causa do mesmo bug no Veredito do Dia: change_pct errado,
        # às vezes com sinal trocado, sistemático em vários tickers).
        prev_close = None
        try:
            hist = ticker_obj.history(period="5d")
            if hist is not None and len(hist) >= 2:
                prev_close = float(hist["Close"].iloc[-2])
        except Exception:
            pass
        if prev_close is None:
            prev_close = getattr(fi, "previous_close", None)
        result[t] = {"price": price, "previousClose": prev_close}
    # `except Exception`, não `except:`. O bare except engole
    # KeyboardInterrupt e SystemExit junto -- e este script roda como
    # subprocesso que o Node encerra com SIGTERM. Num laço por ticker, ele
    # pegaria o sinal, gravaria "sem preço" e seguiria para o próximo, em vez
    # de sair. Ver #103: fechar o parcial no SIGTERM em vez de perder a run.
    except Exception:
        result[t] = {"price": None, "previousClose": None}
print(json_seguro.dumps(result))
