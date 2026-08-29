"""
Retrato rápido de um ticker avulso — o terceiro painel da tela Análise Rápida
(os dois primeiros reusam get_trend/get_technicals, cujas rotas já aceitam
ticker por query string).

Duas fontes independentes, cada uma com seu próprio erro parcial:

- fast_info (yfinance ao vivo): preço e faixa de 52 semanas. Ao vivo de
  propósito — este painel responde "onde o papel está AGORA"; servir um preço
  de cache vencido rotulado de "agora" seria mentira silenciosa, então aqui
  não há cadeia de fallback: sem yfinance, o campo falha visível (quoteError).
- MM50/MM200 calculadas da SÉRIE, não lidas do `fast_info`. Ver
  `_medias_moveis` — a razão está lá, e é um incidente de produção.
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

import datetime as _dt
from zoneinfo import ZoneInfo

_NY_TZ = ZoneInfo("America/New_York")
# Serializacao que nao emite NaN/Infinity -- ver json_seguro.py. Import
# duplo porque estes scripts rodam dos DOIS jeitos: spawn por caminho
# (imports planos) e como membro do pacote agent.
try:
    import json_seguro
except ImportError:
    from agent import json_seguro


try:
    from get_scenario_params import compute as compute_cenario
except ImportError:  # rodando como módulo do pacote (testes)
    from agent.get_scenario_params import compute as compute_cenario

try:
    import market_data_provider
except ImportError:
    from agent import market_data_provider


def _num(v):
    """float arredondado ou None — fast_info devolve None/NaN sem avisar."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return round(f, 2)


# Janela da série usada para as médias. 2 anos porque a MM200 precisa de 200
# pregões e `get_technicals` roda com 6mo -- e é por isso que a MM200 nunca
# aparece no painel Técnica (`rolling(200)` sobre ~126 barras é NaN) e aparece
# aqui. O valor da MM50 não muda com a janela: `rolling(50).iloc[-1]` só olha
# as últimas 50 barras.
_PERIODO_MEDIAS = "2y"


def _medias_moveis(ticker: str) -> tuple:
    """(sma50, sma200, origem) calculadas da MESMA série que o painel Técnica.

    Incidente real (SNDK, 26/08/2026, terceira ocorrência): o painel Técnica
    mostrava MM50 US$ 1624,02 e o painel Níveis US$ 1636,42, para o mesmo
    papel no mesmo instante. Antes disso, 106,02 contra 106,85 -- sempre a
    mesma ordem de grandeza, ~0,8%, sempre o Níveis acima.

    A causa eram duas DEFINIÇÕES diferentes com o mesmo nome:

        get_technicals.py   close.rolling(50).mean()   -- 50 pregões da série
        get_ticker_snapshot fi.fifty_day_average       -- campo pronto do Yahoo

    O campo do Yahoo é caixa-preta: não dá para saber quantas barras entraram,
    se o dia corrente conta, nem qual ajuste foi aplicado. Não dá para
    reconciliar, então não dá para dizer qual dos dois números está certo --
    e duas respostas para a mesma pergunta na mesma tela é pior que uma
    resposta imperfeita.

    Duas das três fontes do app já concordavam (`tools.py`, que alimenta o
    Veredito, também usa `rolling(50)`); o snapshot era a exceção. E isso
    tinha consequência além da estética: a checagem `SMA50_DISTANCIA_ERRADA`
    compara a prosa contra `pct_above_sma50` do agente. Com a prosa citando o
    número do painel Níveis e o validador usando o do agente, o apontamento
    sairia contra um texto correto -- falso positivo, que é o defeito mais
    caro que este validador pode ter.

    Mesmos flags de `get_technicals`: `auto_adjust=True` e
    `permitir_externa=False`. A fonte externa devolve "as traded", e um
    desdobramento dentro da janela viraria um degrau -- número errado com
    cara de número certo.

    Quando a série não vem, cai no campo do Yahoo MARCADO como tal. Recusar
    o dado seria trocar uma imprecisão de 0,8% por um traço; cair em silêncio
    seria repor o defeito. A origem vai junto e a tela mostra.
    """
    try:
        resultado = market_data_provider.get_daily_history(
            ticker, _PERIODO_MEDIAS, auto_adjust=True, permitir_externa=False
        )
        if resultado.ok:
            hist = resultado.df
            if hasattr(hist.columns, "levels"):
                hist.columns = hist.columns.get_level_values(0)
            # Barra do dia corrente com Close vazio fora do pregão entra como
            # última linha e contamina a média -- mesmo tratamento de
            # get_technicals.py e get_chart.py.
            close = hist["Close"].dropna()
            if len(close) >= 50:
                m50 = _num(close.rolling(50).mean().iloc[-1])
                m200 = _num(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else None
                return m50, m200, "serie"
            motivo = f"serie com {len(close)} pregao(oes), minimo 50"
        else:
            motivo = f"serie indisponivel ({resultado.source})"
    except Exception as e:  # noqa: BLE001
        motivo = f"{type(e).__name__}: {e}"

    print(f"[get_ticker_snapshot] {ticker}: medias do fast_info -- {motivo}",
          file=sys.stderr, flush=True)
    try:
        fi = yf.Ticker(ticker).fast_info
        return (_num(fi.fifty_day_average), _num(fi.two_hundred_day_average),
                "yahoo")
    except Exception as e:  # noqa: BLE001
        print(f"[get_ticker_snapshot] {ticker}: fast_info tambem falhou: {e}",
              file=sys.stderr, flush=True)
        return None, None, "indisponivel"


def snapshot(ticker: str, benchmark: str) -> dict:
    # Este painel é AO VIVO (`fast_info.last_price`), não sai de barra
    # fechada. `dadosAte` aqui é a sessão de HOJE em Nova York, e é isso que o
    # torna comparável com os painéis de barra: se a técnica alcança 27/08 e
    # este alcança 29/08, os dois estão medindo mundos diferentes.
    out = {"ticker": ticker, "benchmark": benchmark,
           "dadosAte": str(_dt.datetime.now(_NY_TZ).date()), "aoVivo": True}

    try:
        fi = yf.Ticker(ticker).fast_info
        out["price"] = _num(fi.last_price)
        out["yearLow"] = _num(fi.year_low)
        out["yearHigh"] = _num(fi.year_high)
        if out["price"] is None:
            out["quoteError"] = "Sem preço no yfinance"
    except Exception as e:  # noqa: BLE001 — qualquer falha vira erro parcial
        out["quoteError"] = str(e) or e.__class__.__name__

    sma50, sma200, origem = _medias_moveis(ticker)
    out["sma50"] = sma50
    out["sma200"] = sma200
    # A ORIGEM viaja junto: com ela a tela pode dizer que aquele número veio
    # de outro cálculo, em vez de o leitor comparar dois painéis e achar que
    # um deles está errado.
    out["smaOrigem"] = origem

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
    print(json_seguro.dumps(snapshot(ticker, benchmark), ensure_ascii=False))
