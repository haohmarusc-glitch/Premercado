"""
Coleta as seis fontes do risco macro e devolve o retrato do dia.

Roda 1x por pregão no pré-mercado. O stdout é EXCLUSIVO do JSON final (o Node
faz JSON.parse); todo diagnóstico vai para stderr.

## A regra que organiza este arquivo

Fonte que falha vira `None`, nunca zero. O macro_risk.py distingue "medi e está
calmo" de "não consegui medir" -- e essa distinção só sobrevive se a coleta
respeitá-la. Um `except` que devolvesse 0.0 aqui reintroduziria, na camada de
baixo, exatamente o bug que o módulo corrige: cegueira lida como segurança.

Por isso cada bloco é isolado: uma fonte fora não pode derrubar as outras
cinco, e cada falha é nomeada em `coleta.erros`.

Rodar à mão (dentro do container, de artifacts/api-server/src):
    python3 -m agent.macro_risk_snapshot
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta

try:
    import json_seguro
    from macro_risk import MacroRiskModule
    from http_retry import SESSION
except ImportError:  # rodando como membro do pacote agent
    from agent import json_seguro
    from agent.macro_risk import MacroRiskModule
    from agent.http_retry import SESSION


def _log(msg: str) -> None:
    print(f"[macro_risk] {msg}", file=sys.stderr, flush=True)


# ── FRED ────────────────────────────────────────────────────────────────────
#
# A chave já existe (FRED_API_KEY, ver get_macro_indicators em tools.py). O que
# não existia era a leitura de DUAS observações: get_macro_indicators pede
# limit=1 porque só quer o nível corrente, e aqui o sinal é a VARIAÇÃO.
#
# DGS30/DGS10 saem com ~1 dia de atraso e o WTI com 2-3. "Hoje" aqui é sempre a
# observação mais recente publicada, não o pregão corrente -- por isso as datas
# viajam no payload: um delta calculado entre observações distantes não é
# variação de um dia, e quem lê a tela precisa poder ver isso.

SERIES_FRED = {
    "yield_30y": "DGS30",
    "yield_10y": "DGS10",
    "wti": "DCOILWTICO",
}


def _fred_duas_ultimas(series_id: str) -> tuple[float | None, float | None, list[str]]:
    """(mais recente, anterior, datas). Pede 10 e filtra: a série vem com '.'
    em feriado, e pedir limit=2 devolveria dois pontos vazios numa emenda."""
    chave = os.environ.get("FRED_API_KEY", "").strip()
    if not chave:
        raise RuntimeError("FRED_API_KEY não configurada")

    r = SESSION.get(
        "https://api.stlouisfed.org/fred/series/observations",
        params={"series_id": series_id, "api_key": chave, "file_type": "json",
                "sort_order": "desc", "limit": 10},
        timeout=15,
    )
    r.raise_for_status()
    validas = [
        o for o in ((r.json() or {}).get("observations") or [])
        if o.get("value") not in (None, ".", "")
    ]
    if len(validas) < 2:
        raise RuntimeError(f"{series_id}: menos de 2 observações válidas")
    return (
        float(validas[0]["value"]),
        float(validas[1]["value"]),
        [validas[0].get("date", ""), validas[1].get("date", "")],
    )


# ── Ásia ────────────────────────────────────────────────────────────────────

SK_HYNIX = "000660.KS"
SAMSUNG = "005930.KS"


def _variacao_do_dia(ticker: str) -> float | None:
    """Variação do último pregão via provider (que já descarta a barra
    incompleta do dia corrente -- ver market_data_provider.sem_barra_incompleta)."""
    try:
        from agent import market_data_provider as mdp
    except ImportError:
        import market_data_provider as mdp  # type: ignore

    res = mdp.get_daily_history(ticker, "1mo")
    if not res.ok or res.df is None or len(res.df) < 2:
        return None
    fech = res.df["Close"]
    anterior = float(fech.iloc[-2])
    if not anterior:
        return None
    return round((float(fech.iloc[-1]) / anterior - 1) * 100, 2)


def _kospi_do_snapshot_global() -> tuple[float | None, str]:
    """Reusa get_global_market_snapshot (playbook §2b: não recalcular o que já
    existe). Devolve (pct, motivo_da_recusa).

    Índice marcado `suspect` NÃO é usado: o rótulo existe justamente porque o
    número pode ser comparação atravessando sessões. Perder o índice não é
    grave -- check_asia_contagion dispara por ação OU índice, e SK Hynix e
    Samsung vêm de outra fonte.

    Nota para quem revisar: o limite de implausibilidade do snapshot é 8,0% e o
    Kospi fechou a -8,0% em 28/07/2026, com circuit breaker acionado. Ou seja,
    uma queda REAL um pouco maior chegaria aqui rotulada como suspeita. Esse
    limite serve outros consumidores e não foi mexido, mas é por isso que este
    sinal não depende só do índice."""
    try:
        from agent import market_alerts as ma
    except ImportError:
        import market_alerts as ma  # type: ignore

    itens = (ma.get_global_market_snapshot() or {}).get("items") or []
    kospi = next((i for i in itens if i.get("ticker") == "^KS11"), None)
    if not kospi:
        return None, "^KS11 ausente do snapshot global"
    if kospi.get("suspect"):
        return None, f"marcado suspeito: {kospi.get('suspectReason', 'sem motivo')}"
    return kospi.get("changePct"), ""


# ── SOX ─────────────────────────────────────────────────────────────────────

# ^SOX é o índice; SOXX é o ETF que o replica. A reserva existe porque o
# yfinance serve índices de forma menos confiável que ações/ETFs, e para a
# medida que importa aqui -- retorno de 9 semanas -- os dois são
# intercambiáveis na prática.
SOX_TICKERS = ["^SOX", "SOXX"]


def _serie_sox(pregoes_minimos: int = 46) -> tuple[list[float] | None, str]:
    try:
        from agent import market_data_provider as mdp
    except ImportError:
        import market_data_provider as mdp  # type: ignore

    ultimo_erro = "nenhum ticker tentado"
    for t in SOX_TICKERS:
        try:
            res = mdp.get_daily_history(t, "1y")
            if res.ok and res.df is not None and len(res.df) >= pregoes_minimos:
                return [float(v) for v in res.df["Close"]], ""
            ultimo_erro = f"{t}: série com {0 if res.df is None else len(res.df)} pregões"
        except Exception as e:  # noqa: BLE001
            ultimo_erro = f"{t}: {e}"
    return None, ultimo_erro


# ── FOMC ────────────────────────────────────────────────────────────────────

JANELA_FOMC_DIAS = 1


def _perto_do_fomc(hoje: date | None = None) -> bool:
    """As datas oficiais já existem em market_alerts.MACRO_EVENTS -- manter uma
    segunda lista aqui seria criar fonte divergente (playbook §10)."""
    try:
        from agent import market_alerts as ma
    except ImportError:
        import market_alerts as ma  # type: ignore

    hoje = hoje or date.today()
    for iso in ma.MACRO_EVENTS.get("FOMC", []):
        try:
            d = datetime.strptime(iso, "%Y-%m-%d").date()
        except ValueError:
            continue
        if abs((d - hoje).days) <= JANELA_FOMC_DIAS:
            return True
    return False


# ── Manchetes China/semis ───────────────────────────────────────────────────

def _manchetes_china() -> tuple[list[dict] | None, str]:
    """get_geopolitical_news já cobre controle de exportação de semicondutores
    (China/Taiwan) numa chamada só, sem ticker.

    O tom sai do léxico de get_trend.py, o mesmo usado na análise de tendência
    -- duplicar uma lista de palavras aqui faria duas medidas de "notícia ruim"
    divergirem com o tempo."""
    try:
        from agent import tools
        from agent.get_trend import _NEGATIVE_RE, _POSITIVE_RE
    except ImportError:
        import tools  # type: ignore
        from get_trend import _NEGATIVE_RE, _POSITIVE_RE  # type: ignore

    por_tema = tools.get_geopolitical_news() or {}
    manchetes: list[dict] = []
    for lista in por_tema.values():
        for n in lista or []:
            titulo = str(n.get("title") or "")
            if not titulo:
                continue
            baixo = titulo.lower()
            neg = len(set(_NEGATIVE_RE.findall(baixo)))
            pos = len(set(_POSITIVE_RE.findall(baixo)))
            # -0.3 / 0 / +0.3: o check só compara contra um limiar negativo, e
            # o léxico dá tom, não intensidade. Fingir uma escala contínua aqui
            # sugeriria precisão que a medida não tem.
            escore = -0.3 if neg > pos else (0.3 if pos > neg else 0.0)
            manchetes.append({"title": titulo, "overall_sentiment_score": escore})
    return manchetes, ""


# ── Coleta ──────────────────────────────────────────────────────────────────

def coletar() -> tuple[dict, dict]:
    """(kwargs do evaluate, diagnóstico da coleta).

    Cada fonte no seu próprio try: uma fora não pode levar as outras cinco.
    """
    dados: dict = {}
    erros: dict[str, str] = {}
    datas: dict[str, list[str]] = {}

    for campo, series_id in SERIES_FRED.items():
        try:
            hoje, ant, ds = _fred_duas_ultimas(series_id)
            dados[f"{campo}_hoje"], dados[f"{campo}_ant"] = hoje, ant
            datas[campo] = ds
        except Exception as e:  # noqa: BLE001
            erros[series_id] = str(e)
            _log(f"{series_id} indisponível: {e}")

    for nome, ticker in (("sk_hynix", SK_HYNIX), ("samsung", SAMSUNG)):
        try:
            dados[nome] = _variacao_do_dia(ticker)
            if dados[nome] is None:
                erros[ticker] = "histórico insuficiente"
        except Exception as e:  # noqa: BLE001
            dados[nome] = None
            erros[ticker] = str(e)
            _log(f"{ticker} indisponível: {e}")

    try:
        dados["kospi"], motivo = _kospi_do_snapshot_global()
        if motivo:
            erros["^KS11"] = motivo
            _log(f"Kospi descartado -- {motivo}")
    except Exception as e:  # noqa: BLE001
        dados["kospi"] = None
        erros["^KS11"] = str(e)

    try:
        dados["sox"], motivo = _serie_sox()
        if motivo:
            erros["SOX"] = motivo
    except Exception as e:  # noqa: BLE001
        dados["sox"] = None
        erros["SOX"] = str(e)

    try:
        dados["manchetes"], _ = _manchetes_china()
    except Exception as e:  # noqa: BLE001
        dados["manchetes"] = None
        erros["noticias"] = str(e)
        _log(f"manchetes indisponíveis: {e}")

    try:
        dados["fomc"] = _perto_do_fomc()
    except Exception:  # noqa: BLE001
        dados["fomc"] = False

    return dados, {"erros": erros, "datasFred": datas}


def montar() -> dict:
    dados, diag = coletar()
    saida = MacroRiskModule().evaluate(
        yield_30y_today=dados.get("yield_30y_hoje"),
        yield_30y_prev=dados.get("yield_30y_ant"),
        near_fomc_window=bool(dados.get("fomc")),
        sk_hynix_pct=dados.get("sk_hynix"),
        samsung_pct=dados.get("samsung"),
        kospi_pct=dados.get("kospi"),
        # Earnings ainda não entram: PRICED_FOR_PERFECTION precisa do balanço do
        # dia cruzado com a reação em pré-mercado, e ligar isso pela metade
        # produziria um sinal pior que ausente. Sem os três, o módulo devolve
        # NAO_APLICAVEL, que é honesto: hoje o sistema não avalia este eixo.
        manchetes=dados.get("manchetes"),
        sox_precos=dados.get("sox"),
        wti_hoje=dados.get("wti_hoje"),
        wti_anterior=dados.get("wti_ant"),
        yield_10y_hoje=dados.get("yield_10y_hoje"),
        yield_10y_anterior=dados.get("yield_10y_ant"),
    )
    saida["coleta"] = diag
    saida["snapshotDate"] = date.today().isoformat()
    _log(
        f"cobertura {saida['cobertura_pct']}% | score {saida['aggregate_score']} | "
        f"{len(diag['erros'])} fonte(s) com erro"
    )
    return saida


if __name__ == "__main__":
    try:
        print(json_seguro.dumps(montar()))
    except Exception as e:  # noqa: BLE001
        print(json_seguro.dumps({"error": str(e) or e.__class__.__name__}))
