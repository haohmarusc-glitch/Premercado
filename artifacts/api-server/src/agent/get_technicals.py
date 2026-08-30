"""Structured technical indicators per ticker — standalone subprocess.

Input (stdin JSON):  {"tickers": ["NVDA", "ARM"]}
Output (stdout JSON): {"items": [ {ticker, price, rsi, rsiSignal, macd..., sma...}, ... ]}

Stdout isolation: during computation fd-1 is redirected to fd-2 (stderr) so
any library print/warn output never reaches the pipe Node.js is reading.
The final JSON is written via os.write(real_stdout_fd, ...) — bypasses all
Python text buffering and guarantees a clean pipe.
"""
import os, sys, json, warnings, logging
from concurrent.futures import ThreadPoolExecutor, as_completed

# ── Save the real stdout fd BEFORE any library can pollute it ────────────────
_real_stdout_fd = os.dup(1)          # save a copy of fd-1
os.dup2(2, 1)                        # redirect fd-1 → stderr for the entire run
sys.stdout = open(os.devnull, "w")   # also redirect Python's sys.stdout object

# ── Suppress all warning channels ─────────────────────────────────────────────
warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

import yfinance as yf
import pandas as pd
from agent.security import sanitize_ticker, friendly_error
from agent.volume_intradiario import barras_da_sessao, rvol_da_sessao
from agent import json_seguro
try:
    from agent import market_data_provider
except ImportError:  # execução standalone (sys.path[0] = src/agent)
    import market_data_provider

# Cópia de tools.py::_rvol_signal -- ver o comentário longo lá para o porquê
# (volume intradiário é em U, a fração linear superestima o rvol na abertura;
# NBIS 17/08/2026 saiu com rvol 5,81 "alto" aos sete minutos de pregão).
# Duplicado porque este arquivo roda por spawn e não importa do pacote;
# test_rvol_abertura.py garante que as duas cópias não divirjam.
_RVOL_FRACAO_MINIMA = 6 / 78  # ~30min do pregão nominal de 6.5h


def _rvol_signal(rvol: float, fraction_elapsed: float) -> str:
    if fraction_elapsed < _RVOL_FRACAO_MINIMA:
        return "indefinido_abertura"
    return "alto" if rvol >= 1.5 else "baixo" if rvol < 0.7 else "normal"


def technicals(ticker: str, period: str = "6mo") -> dict:
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": str(ticker), "error": str(e)}
    try:
        # Mesmo cache em disco do market_alerts._history: este script roda num
        # PROCESSO À PARTE e baixava o mesmo 6mo dos mesmos tickers que o
        # run_checkers acabara de baixar, a cada 5 minutos. auto_adjust=True
        # aqui (contra False lá) faz parte da chave -- as séries diferem.
        #
        # permitir_externa=False de propósito: aqui a série é AJUSTADA, e a
        # fonte externa devolve "as traded". Um desdobramento dentro dos 6
        # meses viraria um degrau de preço, e RSI/médias/tendência sairiam
        # com um salto que nunca existiu -- pior que ficar sem indicador,
        # porque o número errado tem cara de número certo. O cache vencido
        # continua valendo: foi gravado do yfinance, já ajustado.
        resultado = market_data_provider.get_daily_history(
            ticker, period, auto_adjust=True, permitir_externa=False
        )
        if not resultado.ok:
            return {"ticker": ticker, "error": "Dados insuficientes"}
        hist = resultado.df
        if resultado.source not in ("yfinance", "yfinance_cache"):
            print(f"[get_technicals] {ticker}: fonte {resultado.source}", file=sys.stderr)
        if hist.empty or len(hist) < 30:
            return {"ticker": ticker, "error": "Dados insuficientes"}
        if hasattr(hist.columns, "levels"):
            hist.columns = hist.columns.get_level_values(0)

        # Linha sem Close fora, ANTES de qualquer conta -- mesmo tratamento do
        # get_chart.py:42. O yfinance devolve a barra do dia corrente com Close
        # vazio fora do pregão, e ela entra como última linha: `close.iloc[-1]`
        # virava NaN e contaminava price, changePct, pctAboveSma* e
        # priceVsVwapPct de uma vez. O guarda de `len(hist) < 30` não pega isso,
        # porque as linhas existem -- só a última é que está vazia.
        hist = hist[hist["Close"].notna()]
        if len(hist) < 30:
            return {"ticker": ticker, "error": "Dados insuficientes"}

        close = hist["Close"]
        volume = hist["Volume"]
        price = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) >= 2 else price
        change_pct = round((price - prev) / prev * 100, 2) if prev else None

        # RSI 14 de WILDER (ewm alpha=1/14) -- mesma conta de
        # get_trend.rsi_wilder, confluence_engine e tools.py.
        #
        # Era `rolling(14).mean()` (variante de Cutler, indicador DIFERENTE) e
        # este script é quem serve /api/technicals: o painel "Técnica" da
        # Análise Rápida mostrava um RSI e o painel "Tendência", que usa
        # get_trend, mostrava outro -- mesmo ticker, mesmo instante (visto em
        # produção, NBIS 17/08/2026: 64,6 contra 67,2). A #298 unificou a
        # cópia de tools.py, que é a ferramenta do AGENTE, e não esta, que é a
        # da TELA -- por isso a divergência sobreviveu à primeira correção.
        #
        # Quando os 14 períodos não têm nenhuma queda, avg_loss = 0 e a
        # divisão vira NaN; json.dumps serializa isso como o token `NaN`, que
        # não é JSON válido (quebra o JSON.parse do Node). RSI=100 é o valor
        # tecnicamente correto pra essa condição (só alta, máximo sobrecomprado).
        delta = close.diff()
        avg_gain = delta.clip(lower=0).ewm(alpha=1 / 14, min_periods=14).mean()
        avg_loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14).mean()
        avg_gain_last = float(avg_gain.iloc[-1])
        avg_loss_last = float(avg_loss.iloc[-1])
        if avg_loss_last == 0:
            rsi = 100.0 if avg_gain_last > 0 else 50.0
        else:
            rsi = round(100 - 100 / (1 + avg_gain_last / avg_loss_last), 2)

        # MACD (12, 26, 9)
        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd_line = ema12 - ema26
        signal_line = macd_line.ewm(span=9).mean()
        histogram = macd_line - signal_line

        def _safe(series):
            val = series.iloc[-1]
            return round(float(val), 2) if not pd.isna(val) else None

        sma20 = _safe(close.rolling(20).mean())
        sma50 = _safe(close.rolling(50).mean())
        sma200 = _safe(close.rolling(200).mean()) if len(close) >= 200 else None

        def _pct_diff(a, b):
            return round((a - b) / b * 100, 2) if a and b else None

        # MEDIANA, não média -- mesma correção de tools.py: um dia de earnings
        # negocia 2-3x o normal e, numa MÉDIA de 20 pregões, inflava a base por
        # um mês inteiro, deprimindo rvol e volumeRatio justo no período mais
        # ativo do papel. A mediana ignora o outlier sem precisar detectá-lo.
        vol_base20 = float(volume.rolling(20).median().iloc[-1])
        vol_5d_avg = float(volume.iloc[-5:].mean())
        vol_ratio = round(vol_5d_avg / vol_base20, 2) if vol_base20 > 0 else None

        # RVOL + VWAP intradiários -- mesma lógica de agent/tools.py::get_technical_indicators
        # (não extraído pra função compartilhada porque esse arquivo roda em
        # subprocesso isolado com stdout redirecionado, ver comentário no topo).
        # Falha (mercado fechado, sem rede) não derruba os indicadores diários
        # acima, só deixa rvol/vwap como None.
        rvol = rvol_signal = vwap = price_vs_vwap_pct = vwap_signal = None
        try:
            intraday = yf.Ticker(ticker).history(period="1d", interval="5m")
            if not intraday.empty:
                # RVOL e VWAP passam a sair SO' das barras do pregao regular,
                # e a fracao decorrida vem do RELOGIO. Ver volume_intradiario.py:
                # a conta antiga derivava o tempo da CONTAGEM de barras, e no
                # dia de balanco AMC da NVDA o pos-mercado entrou no numerador
                # enquanto o denominador o tratava como tempo de pregao --
                # rvol 8,89 num dia de volume comum.
                #
                # A VWAP vinha do mesmo frame, entao herdava a contaminacao:
                # uma VWAP ponderada por pos-mercado nao e' a VWAP do pregao.
                sessao = barras_da_sessao(intraday)
                rvol, fraction_elapsed = rvol_da_sessao(intraday, vol_base20)
                if rvol is not None:
                    rvol_signal = _rvol_signal(rvol, fraction_elapsed)

                if sessao is not None and len(sessao) > 0:
                    intraday_volume = sessao["Volume"]
                    typical_price = (sessao["High"] + sessao["Low"] + sessao["Close"]) / 3
                    vol_sum = float(intraday_volume.sum())
                    if vol_sum > 0:
                        vwap = round(float((typical_price * intraday_volume).sum() / vol_sum), 2)
                        price_vs_vwap_pct = round((price - vwap) / vwap * 100, 2) if vwap else None
                        vwap_signal = "acima" if price > vwap else "abaixo" if price < vwap else "no vwap"
        except Exception:
            pass  # mercado fechado / sem dado intradiário -- rvol/vwap ficam None

        hist_val = float(histogram.iloc[-1])
        return {
            "ticker": ticker,
            "price": round(price, 2),
            # Até que sessão este painel alcança -- ver `_DEFASAGEM_*` em
            # analise_rapida_ia.py.
            "dadosAte": str(close.index[-1].date()),
            "changePct": change_pct,
            "rsi": rsi,
            "rsiSignal": "sobrecomprado" if rsi > 70 else "sobrevendido" if rsi < 30 else "neutro",
            "macdHistogram": round(hist_val, 4),
            "macdTrend": "bullish" if hist_val > 0 else "bearish",
            "sma20": sma20,
            "sma50": sma50,
            "sma200": sma200,
            "pctAboveSma50": _pct_diff(price, sma50),
            "pctAboveSma200": _pct_diff(price, sma200),
            "volumeRatio": vol_ratio,
            "volAvg20": round(vol_base20) if vol_base20 > 0 else None,
            "rvol": rvol,
            "rvolSignal": rvol_signal,
            "vwap": vwap,
            "priceVsVwapPct": price_vs_vwap_pct,
            "vwapSignal": vwap_signal,
        }
    except Exception as e:
        print(f"[get_technicals] {ticker}: {e}", file=sys.stderr)
        return {"ticker": ticker, "error": friendly_error(e)}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    tickers = args.get("tickers", [])
    # Busca em paralelo (I/O-bound) — sequencial para ~25+ tickers arrisca
    # estourar o timeout do subprocesso no Node quando o yfinance está lento.
    # `technicals()` já captura suas próprias exceções por ticker; o
    # try/except aqui é só uma rede de segurança extra por chamada.
    items = [None] * len(tickers)
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(technicals, t): i for i, t in enumerate(tickers)}
        for future in as_completed(futures):
            i = futures[future]
            try:
                items[i] = future.result()
            except Exception as e:
                print(f"[get_technicals] {tickers[i]}: {e}", file=sys.stderr)
                items[i] = {"ticker": tickers[i], "error": friendly_error(e)}
    # json_seguro e não json.dumps: um único NaN em qualquer campo torna a
    # resposta INTEIRA ilegível para o Node, derrubando junto os tickers que
    # vieram certos. Ver o cabeçalho de json_seguro.py.
    result = json_seguro.dumps({"items": items}) + "\n"
    # Write directly to the saved real stdout fd — clean, no buffering issues
    os.write(_real_stdout_fd, result.encode("utf-8"))
    os.close(_real_stdout_fd)
