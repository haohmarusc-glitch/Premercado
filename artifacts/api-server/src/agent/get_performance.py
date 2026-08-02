import sys, json

# Mede quanto do tempo do processo é interpretador+import (ver
# startup_probe.py). Importado dos dois jeitos pelo mesmo motivo do
# bounded_parallel abaixo: este script roda como arquivo solto.
try:
    from startup_probe import boot as _probe_boot, imports_prontos as _probe_imports
except ImportError:
    from agent.startup_probe import boot as _probe_boot, imports_prontos as _probe_imports

_probe_boot()

import yfinance as yf

# bounded_parallel é importado dos DOIS jeitos porque este script roda dos dois
# jeitos: como arquivo solto (scenarios.ts spawna por caminho, sem PYTHONPATH --
# aí sys.path[0] é o próprio diretório agent/) e, em outros pontos, como módulo
# do pacote. Só stdlib dentro dele, então o import flat é seguro.
try:
    from bounded_parallel import deadline_exceeded
except ImportError:
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
    except:
        result[t] = {"price": None, "previousClose": None}
print(json.dumps(result))
