#!/usr/bin/env python3
"""
Lightweight quote fetcher using yfinance fast_info.
Called as: python3 -m agent.get_quotes SYMBOL1 SYMBOL2 ...
Outputs a JSON array to stdout.

Além dos campos do pregão regular (via fast_info), tenta enriquecer com dados
de pré-mercado / after-hours via uma chamada batch ao endpoint de cotações do
Yahoo. Se essa chamada falhar (rate limit, bloqueio), os campos extended ficam
nulos e o restante continua funcionando (fail-open).

Busca em PARALELO (ThreadPoolExecutor, mesmo padrão de get_technicals.py/
get_market_alerts_snapshot.py) -- cada símbolo faz DUAS chamadas de rede
(fast_info + .info), e buscar sequencialmente estourava o timeout de 30s do
lado Node (portfolio-alerts.ts) com poucos tickers já, principalmente quando
o yfinance está lento (visto em produção: "get_quotes timeout" recorrente no
checker de carteira, a cada ~15min). A ordem do array de saída não importa --
quem consome (portfolio-alerts.ts) monta um Map por `symbol`, não por índice.
"""
import sys
import json

# Mede quanto do tempo do processo é interpretador+import, antes de
# qualquer trabalho útil. Ver startup_probe.py.
from agent.startup_probe import boot as _probe_boot, imports_prontos as _probe_imports

_probe_boot()
import yfinance as yf
from agent.bounded_parallel import bounded_parallel_map, budget_from_deadline, exit_now
from agent.security import friendly_error

_probe_imports()

# Timeout do lado Node (portfolio-alerts.ts::fetchPrices) é 30s. Quem consome
# o array de saída (priceMap em portfolio-alerts.ts) já é tolerante a um
# símbolo faltando (fica sem preço aquele ciclo, tenta de novo no próximo),
# então um resultado parcial aqui não é um problema.
# Fallback quando o processo roda sem AGENT_DEADLINE_TS no env (execução
# manual do script). Com a variável definida, o orçamento real vem do
# deadline do chamador via budget_from_deadline() -- constante fixa aqui
# não conseguia cobrir o custo de import (~8s de pandas/numpy/yfinance),
# que sai da mesma folga. Ver bounded_parallel.py.
BUDGET_S = 20


def _round(v, d=4):
    return round(v, d) if isinstance(v, (int, float)) else None


def fetch_extended(ticker) -> dict:
    """marketState + preços de pré/pós-mercado via .info (yfinance trata o crumb).

    Fail-open: se o Yahoo bloquear ou faltar dado, retorna chaves nulas.
    """
    try:
        info = ticker.info or {}
    except Exception:
        info = {}
    return {
        "marketState": info.get("marketState"),
        "preMarketPrice": _round(info.get("preMarketPrice")),
        "preMarketChangePct": _round(info.get("preMarketChangePercent")),
        "postMarketPrice": _round(info.get("postMarketPrice")),
        "postMarketChangePct": _round(info.get("postMarketChangePercent")),
        # Preço do pregão regular explícito do Yahoo -- diferente de
        # fast_info.last_price, que pode já refletir o último trade de
        # pré/pós-mercado (não é confiável pra isolar "só pregão regular").
        "regularMarketPrice": _round(info.get("regularMarketPrice")),
    }


def _empty_quote(symbol: str, error: str) -> dict:
    return {
        "symbol": symbol,
        "currency": None,
        "price": None,
        "change": None,
        "changePct": None,
        "open": None,
        "previousClose": None,
        "dayHigh": None,
        "dayLow": None,
        "volume": None,
        "marketCap": None,
        "marketState": None,
        "preMarketPrice": None,
        "preMarketChangePct": None,
        "postMarketPrice": None,
        "postMarketChangePct": None,
        "regularMarketPrice": None,
        "isDelayed": False,
        "source": "none",
        "sourceWarnings": [],
        "error": error,
    }


def _quote_do_fallback(symbol: str) -> dict | None:
    """Monta uma cotação a partir da fonte externa (market_data_provider).

    É EOD: só preço, fechamento anterior, variação e volume. `open`, máxima,
    mínima, market cap e os campos de pré/pós-mercado ficam nulos porque a
    fonte simplesmente não tem esse dado — preencher com o fechamento seria
    inventar. `isDelayed=True` é o que impede a tela de mostrar fechamento de
    ontem como preço ao vivo.

    Import tardio de propósito: `market_data_provider` puxa pandas e o
    ecossistema do yfinance, e este caminho quase nunca roda. O tempo de
    import sai do mesmo orçamento do processo (ver bounded_parallel.py).
    """
    try:
        from agent.market_data_provider import get_quote as _fallback_quote
    except Exception as ex:  # noqa: BLE001
        print(f"[get_quotes] fallback indisponível: {ex}", file=sys.stderr)
        return None

    r = _fallback_quote(symbol)
    if r.quote is None:
        return None

    q = _empty_quote(symbol, None)
    q.update({
        "price": r.quote.get("price"),
        "previousClose": r.quote.get("previousClose"),
        "change": r.quote.get("change"),
        "changePct": r.quote.get("changePct"),
        "volume": r.quote.get("volume"),
        "isDelayed": r.is_delayed,
        "source": r.source,
        "sourceWarnings": r.warnings,
    })
    return q


def fetch_quote(symbol: str) -> dict:
    try:
        ticker = yf.Ticker(symbol)
        fi = ticker.fast_info
        e = fetch_extended(ticker)

        price = getattr(fi, "last_price", None)
        # previous_close vem do candle diario oficial (.history()), NAO de
        # fast_info.previous_close -- as duas fontes podem divergir (mesmo
        # motivo/mesmo padrão de tools.py::get_stock_data, já corrigido
        # por causa do mesmo bug no Veredito do Dia: change_pct errado,
        # às vezes com sinal trocado. changePct daqui aparece direto pro
        # usuário em quase toda a UI -- dashboard, quotes, portfolio,
        # gráfico -- então vale a chamada de rede extra.
        prev_close = None
        try:
            hist = ticker.history(period="5d")
            if hist is not None and len(hist) >= 2:
                prev_close = float(hist["Close"].iloc[-2])
        except Exception:
            pass
        if prev_close is None:
            prev_close = getattr(fi, "previous_close", None)
        currency = getattr(fi, "currency", None)
        open_ = getattr(fi, "open", None)
        day_high = getattr(fi, "day_high", None)
        day_low = getattr(fi, "day_low", None)
        volume = getattr(fi, "last_volume", None)
        market_cap = getattr(fi, "market_cap", None)

        change = None
        change_pct = None
        if price is not None and prev_close is not None and prev_close != 0:
            change = round(price - prev_close, 4)
            change_pct = round((price - prev_close) / prev_close * 100, 4)

        return {
            "symbol": symbol,
            "currency": currency,
            "price": round(price, 4) if price is not None else None,
            "change": change,
            "changePct": change_pct,
            "open": round(open_, 4) if open_ is not None else None,
            "previousClose": round(prev_close, 4) if prev_close is not None else None,
            "dayHigh": round(day_high, 4) if day_high is not None else None,
            "dayLow": round(day_low, 4) if day_low is not None else None,
            "volume": int(volume) if volume is not None else None,
            "marketCap": int(market_cap) if market_cap is not None else None,
            "marketState": e.get("marketState"),
            "preMarketPrice": e.get("preMarketPrice"),
            "preMarketChangePct": e.get("preMarketChangePct"),
            "postMarketPrice": e.get("postMarketPrice"),
            "postMarketChangePct": e.get("postMarketChangePct"),
            "regularMarketPrice": e.get("regularMarketPrice"),
            "isDelayed": False,
            "source": "yfinance",
            "sourceWarnings": [],
            "error": None,
        }
    except Exception as ex:
        print(f"[get_quotes] {symbol}: {ex}", file=sys.stderr)
        return _empty_quote(symbol, friendly_error(ex))


def aplicar_fallback(results: list[dict]) -> list[dict]:
    """Decide, olhando o LOTE inteiro, se vale acionar a fonte externa.

    A regra é deliberada: o fallback só entra quando NENHUM símbolo do lote
    trouxe preço. Dois motivos, os dois concretos:

    1. Um símbolo isolado sem preço quase sempre é o próprio símbolo
       (deslistado, ticker digitado errado) — falharia em qualquer fonte, e
       gastar cota da Alpha Vantage nele é jogar fora a cota que o feed de
       notícias divide com a gente.
    2. O lote inteiro sem preço é o sintoma do problema que este caminho
       existe para cobrir: o Yahoo bloqueando ou fora do ar.

    O registro no disjuntor também é UMA vez por lote, não por símbolo — um
    ticker morto não pode penalizar o provedor inteiro (ver
    provider_health.py, seção "O disjuntor é por PROVEDOR").
    """
    if not results:
        return results

    try:
        from agent import provider_health
    except Exception:  # noqa: BLE001 — nunca derruba a cotação
        provider_health = None  # type: ignore[assignment]

    sem_preco = [r for r in results if r.get("price") is None]
    houve_sucesso = len(sem_preco) < len(results)

    if provider_health is not None:
        try:
            if houve_sucesso:
                provider_health.record_success("yfinance")
            else:
                provider_health.record_failure("yfinance")
        except Exception:  # noqa: BLE001
            pass

    if houve_sucesso or not sem_preco:
        return results

    print(
        f"[get_quotes] lote inteiro sem preço ({len(sem_preco)} símbolos) — "
        "tentando fonte externa",
        file=sys.stderr,
    )
    por_symbol = {r["symbol"]: r for r in results}
    for r in sem_preco:
        alternativo = _quote_do_fallback(r["symbol"])
        if alternativo is not None:
            # Preserva o erro original: a cotação veio, mas continua sendo
            # útil saber por que a fonte primária não respondeu.
            alternativo["error"] = r.get("error")
            por_symbol[r["symbol"]] = alternativo
    return list(por_symbol.values())


if __name__ == "__main__":
    symbols = sys.argv[1:]
    if not symbols:
        print("[]")
        sys.exit(0)

    results = bounded_parallel_map(
        fetch_quote,
        symbols,
        budget_s=budget_from_deadline(BUDGET_S, label="get_quotes"),
        label="get_quotes",
    )
    # bounded_parallel_map devolve só quem terminou a tempo -- sem isto, um
    # símbolo que estourou o orçamento (ex.: SMCI num dia de earnings, alto
    # volume) simplesmente sumia da resposta, sem nenhum sinal de erro pro
    # consumidor (visto em produção: /api/tickers/quotes vazio pra um ticker
    # com posição aberta, 12/08/2026). Preenche o que faltou com um erro
    # explícito em vez de omitir.
    fetched = {r["symbol"] for r in results}
    for symbol in symbols:
        if symbol not in fetched:
            results.append(_empty_quote(symbol, "Tempo esgotado buscando cotação"))

    results = aplicar_fallback(results)
    exit_now(json.dumps(results) + "\n")
