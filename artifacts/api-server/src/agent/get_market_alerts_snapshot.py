"""Snapshot standalone do RISCO MACRO/GEOPOLÍTICO (market_alerts.py) pra
exibição direta no frontend, sem precisar rodar o agente completo (LLM) --
pensado pro card "Alertas de Mercado" do Dashboard.

Propositalmente NÃO chama run_all_alerts() inteiro -- aquele roda também
check_overbought/volume_gap/candle_patterns/dead_cat_bounce/earnings_
proximity/analyst_changes/sell_the_news/trading_halt POR TICKER, que são
alertas técnicos/de empresa sem relação com o parâmetro de risco macro
(juros + petróleo + geopolítica) que este card existe pra mostrar -- e cada
um desses checks é uma chamada de rede a mais POR TICKER, o que com a cesta
cheia (15 tickers) passava de 2 minutos neste sandbox (rede bloqueada torna
cada chamada falha lenta) e seria lento demais pra um card de Dashboard
mesmo com rede saudável. Aqui só roda: check_macro_triggers (calendário
FOMC/CPI/JOBS/PPI + yield + choque de petróleo), check_macro_regime_risk
(sinal combinado) e check_geopolitical_news por ticker (Taiwan, Irã,
Coreia, Fed, rating soberano, tarifas, antitruste, terras raras) -- exatamente
os checks que este PR adicionou/estende.

Diferente da maioria dos scripts em agent/ (invocados por CAMINHO direto,
ex.: get_quotes.py), este roda como `python -m agent.get_market_alerts_snapshot`
(import absoluto via pacote) porque market_alerts.py faz `from .cache import
cached` -- import relativo que só funciona com esse contexto de pacote.
Mesmo motivo/solução do `from agent.security import friendly_error` em
get_quotes.py.

Busca manchetes reais direto via yfinance, em PARALELO (ThreadPoolExecutor,
mesmo padrão de get_technicals.py) -- função própria, não importa
get_news_feed.py (aquele usa `from security import ...` top-level, que só
resolve quando invocado por CAMINHO direto; nesse contexto de import via
pacote quebraria, mesmo motivo documentado acima).

O MATCH de keyword (GEO_KEYWORDS) roda sempre contra o texto ORIGINAL em
inglês -- é o que sai cru do yfinance e é o que as keywords cobrem melhor.
A TRADUÇÃO pra pt-BR acontece DEPOIS, só nos `detail` já montados pelos
alerts (regex genérica pega qualquer trecho entre aspas, cobre tanto
`Manchete: "..."` de check_geopolitical_news quanto `categoria -- "..."` de
check_macro_regime_risk) -- mesmo endpoint gratuito do Google Translate já
usado em get_news_feed.py (não importado por aqui pelo mesmo motivo de
import citado acima; reimplementado local). Fail-open: se a tradução falhar
(ex.: rede bloqueada), mantém o texto original em inglês -- nunca quebra o
card por causa disso.

Input (stdin JSON): {"tickers": ["NVDA", ...]}  (default: config.TICKERS)
Output (stdout JSON): {"total": N, "alerts": [...]}
"""
import sys
import json
import re
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed

from agent import config
from agent.market_alerts import (
    check_macro_triggers,
    check_macro_regime_risk,
    check_geopolitical_news,
    Severity,
)


def _headlines_for(ticker: str, max_items: int = 5) -> list[str]:
    import yfinance as yf
    try:
        news = yf.Ticker(ticker).news or []
        titles = []
        for item in news[:max_items]:
            content = item.get("content", {}) if isinstance(item.get("content"), dict) else {}
            title = content.get("title", item.get("title", ""))
            if title:
                titles.append(str(title))
        return titles
    except Exception as e:
        print(f"[get_market_alerts_snapshot] {ticker}: {e}", file=sys.stderr)
        return []


def _headlines_by_ticker(tickers: list[str], max_items: int = 5) -> dict[str, list]:
    out: dict[str, list] = {t: [] for t in tickers}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(_headlines_for, t, max_items): t for t in tickers}
        for future in as_completed(futures):
            t = futures[future]
            try:
                out[t] = future.result()
            except Exception as e:
                print(f"[get_market_alerts_snapshot] {t}: {e}", file=sys.stderr)
    return out


# Trecho entre aspas com pelo menos 8 caracteres -- comprimento mínimo evita
# pegar aspas curtas incidentais (não há nenhuma nos alerts atuais, mas é
# uma rede de segurança barata). Único trecho entre aspas por `detail` em
# todos os alerts que este script gera (title/detail nunca aninham aspas
# fora da manchete citada).
_QUOTED_RE = re.compile(r'"([^"]{8,})"')


def _translate_batch(texts: list[str]) -> list[str]:
    """en->pt-BR via agent/traducao.py (cache em disco -> Google -> LLM).

    Era uma cópia local da chamada ao endpoint gratuito do Google, dentro de
    try/except -- quando ele passou a responder 429, as manchetes citadas nos
    alertas voltaram ao inglês sem que nada aparecesse. A migração para o
    ponto único ficou para trás em 25/08/2026 porque o conserto olhou só
    get_news_feed.py; esta cópia e a de get_trend.py só apareceram no sweep
    seguinte."""
    if not texts:
        return texts
    try:
        from traducao import traduzir
    except ImportError:
        from agent.traducao import traduzir
    traduzidos, _origens = traduzir(list(texts))
    return traduzidos


def _translate_alert_headlines(alerts: list) -> None:
    """Traduz só o trecho entre aspas de cada `detail` (a manchete citada),
    em lote -- muta os Alert in-place. Cada `detail` tem no máximo 1 trecho
    citado hoje (ver _QUOTED_RE)."""
    matches = [(a, m) for a in alerts for m in [_QUOTED_RE.search(a.detail)] if m]
    if not matches:
        return
    translated = _translate_batch([m.group(1) for _, m in matches])
    for (a, m), pt in zip(matches, translated):
        a.detail = a.detail[: m.start(1)] + pt + a.detail[m.end(1) :]


if __name__ == "__main__":
    try:
        args = json.loads(sys.stdin.read() or "{}")
    except Exception:
        args = {}

    tickers = args.get("tickers") or config.TICKERS
    headlines_by_ticker = _headlines_by_ticker(tickers)

    today = dt.date.today()
    alerts = check_macro_triggers(today)
    alerts += check_macro_regime_risk(headlines_by_ticker)
    for t in tickers:
        alerts += check_geopolitical_news(t, headlines_by_ticker.get(t, []))

    order = {Severity.CRITICO: 0, Severity.ATENCAO: 1, Severity.INFO: 2}
    alerts.sort(key=lambda a: order[a.severity])

    _translate_alert_headlines(alerts)

    result = {
        "total": len(alerts),
        "criticalCount": sum(1 for a in alerts if a.severity == Severity.CRITICO),
        "alerts": [a.to_dict() for a in alerts],
    }
    print(json.dumps(result, ensure_ascii=False))
