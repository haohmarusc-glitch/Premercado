"""
Retrato rápido de um ticker avulso — o terceiro painel da tela Análise Rápida
(os dois primeiros reusam get_trend/get_technicals, cujas rotas já aceitam
ticker por query string).

Duas fontes independentes, cada uma com seu próprio erro parcial:

- fast_info (yfinance ao vivo): preço, faixa de 52 semanas, MM50/MM200.
  Ao vivo de propósito — este painel responde "onde o papel está AGORA";
  servir uma média móvel de cache vencido rotulada de "agora" seria mentira
  silenciosa, então aqui não há cadeia de fallback: sem yfinance, o campo
  falha visível (quoteError).
- get_scenario_params.compute: vol anual e beta vs benchmark — este SIM já
  passa pela cadeia (lote, série ajustada, sem fonte externa) e propaga
  fontesDegradadas quando serviu cache vencido.

stdin:  {"ticker": "INTC", "benchmark": "SMH"}
stdout: JSON com as duas seções; quoteError/cenarioError marcam falha
parcial (a tela mostra o que veio); "error" só quando nada veio.
"""
import json
import sys

import yfinance as yf

try:
    from get_scenario_params import compute as compute_cenario
except ImportError:  # rodando como módulo do pacote (testes)
    from agent.get_scenario_params import compute as compute_cenario


def _num(v):
    """float arredondado ou None — fast_info devolve None/NaN sem avisar."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, 2)


def snapshot(ticker: str, benchmark: str) -> dict:
    out = {"ticker": ticker, "benchmark": benchmark}

    try:
        fi = yf.Ticker(ticker).fast_info
        out["price"] = _num(fi.last_price)
        out["yearLow"] = _num(fi.year_low)
        out["yearHigh"] = _num(fi.year_high)
        out["sma50"] = _num(fi.fifty_day_average)
        out["sma200"] = _num(fi.two_hundred_day_average)
        if out["price"] is None:
            out["quoteError"] = "Sem preço no yfinance"
    except Exception as e:  # noqa: BLE001 — qualquer falha vira erro parcial
        out["quoteError"] = str(e) or e.__class__.__name__

    try:
        cen = compute_cenario([ticker], benchmark)
        params = (cen.get("params") or {}).get(ticker) or {}
        if "error" in params:
            out["cenarioError"] = str(params["error"])
        else:
            out["volAnnual"] = params.get("volAnnual")
            out["betaSector"] = params.get("betaSector")
            out["daysUsed"] = params.get("daysUsed")
        out["sectorMomentum"] = cen.get("sectorMomentum")
        degradadas = cen.get("fontesDegradadas")
        if degradadas:
            out["fontesDegradadas"] = degradadas
    except Exception as e:  # noqa: BLE001
        out["cenarioError"] = str(e) or e.__class__.__name__

    if out.get("quoteError") and out.get("cenarioError"):
        out["error"] = "Sem dados: yfinance e cadeia de cenário falharam"
    return out


if __name__ == "__main__":
    args = json.loads(sys.stdin.read() or "{}")
    ticker = str(args.get("ticker") or "").strip().upper()
    benchmark = str(args.get("benchmark") or "SMH").strip().upper() or "SMH"
    if not ticker:
        print(json.dumps({"error": "ticker é obrigatório"}))
        sys.exit(0)
    print(json.dumps(snapshot(ticker, benchmark), ensure_ascii=False))
