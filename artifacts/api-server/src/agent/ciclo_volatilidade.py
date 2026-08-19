"""
ciclo_volatilidade.py — o ciclo de vida da volatilidade por ticker
==================================================================

Volatilidade, ao contrário de direção, tem ciclo observável: comprime,
dispara, persiste e decai com meia-vida conhecida. Este módulo classifica
cada ticker numa fase e entrega os números que a justificam:

  COMPRIMIDA  → mola armada (squeeze de Bollinger / vol abaixo do normal)
  GATILHO     → o range de HOJE estourou o esperado, com volume
  EXPANSAO    → EWMA rodando bem acima da vol estrutural, ATR subindo
  DECAIMENTO  → episódio morrendo (ranges encolhendo dia após dia)
  NORMAL      → nada digno de nota

## As duas escalas de vol

- `volEstrutural`: desvio-padrão de TODOS os retornos da janela (1 ano) —
  o "normal do papel". Mesma fórmula do get_scenario_params/risk_manager.
- `ewma` (λ=0.94, RiskMetrics): peso exponencial nos dias recentes — a vol
  "de agora", que reage em 2-3 pregões a uma mudança de regime. Meia-vida
  do excesso: ln(0.5)/ln(0.94) ≈ 11 pregões — é daí que sai a estimativa
  `diasParaNormalizar`, e é a única previsão honesta aqui: prevê DECAIMENTO
  ESPERADO, que um choque novo reinicia do zero.

A banda de amanhã (`bandaAmanha`) é preço × (1 ± σ_ewma) e ± 2σ — previsão
de MAGNITUDE, nunca de direção.

## Earnings à parte

Vespera de balanço quebra qualquer EWMA: a previsão certa vem da estatística
de reação (tela Reação a Earnings), não da vol histórica. Quando o calendário
do yfinance mostra balanço nos próximos 7 dias, o resultado carrega
`earningsProximo` e a fase NÃO muda — o aviso é para o usuário chavear de
ferramenta. A checagem é ao vivo (tolerante a falha) em vez do
em_janela_earnings do parametros_volatilidade, que só conhece o universo
estático do radar — esta tela aceita ticker livre.

Histórico via cadeia de fallback (série ajustada, sem fonte externa — mesmo
racional do get_scenario_params); dado degradado vira `fonteHistorico` no
resultado, nunca silêncio.

stdin: {"tickers": ["INTC", ...]}
stdout: {"items": [{...por ticker...}]}
"""
import json
import math
import sys

import numpy as np
import yfinance as yf
# Serializacao que nao emite NaN/Infinity -- ver json_seguro.py. Import
# duplo porque estes scripts rodam dos DOIS jeitos: spawn por caminho
# (imports planos) e como membro do pacote agent.
try:
    import json_seguro
except ImportError:
    from agent import json_seguro


try:  # spawn por caminho (rota) e módulo do pacote (testes)
    import market_data_provider
except ImportError:
    from agent import market_data_provider

LAMBDA_EWMA = 0.94          # RiskMetrics; meia-vida ≈ 11 pregões
PERIODO = "1y"
MIN_PREGOES = 60
RAZAO_EXPANSAO = 1.3        # EWMA/estrutural acima disso = episódio vivo
RAZAO_DECAIMENTO = 1.1      # ainda elevada, mas já morrendo
RAZAO_COMPRIMIDA = 0.7
GATILHO_RANGE_X = 2.0       # range de hoje ≥ 2× o σ diário esperado
GATILHO_RVOL = 1.5
RANGES_DECRESCENTES_MIN = 3
SQUEEZE_PERCENTIL = 20      # largura de Bollinger no quintil inferior de 120d
JANELA_EARNINGS_DIAS = 7

# Mantido em sync manual com config.NO_EARNINGS_TICKERS (mesma nota do
# get_earnings.py): ETF/índice nunca tem balanço — pular evita um
# round-trip de rede que sempre falha.
_SEM_EARNINGS = frozenset({
    "SGOV", "BIL", "SHV", "SHY", "SPY", "QQQ", "VOO", "IVV", "VTI", "DIA",
    "AGG", "BND", "TLT", "IEF", "GOVT", "MUB", "XLK", "XLF", "XLE", "XLV",
    "SMH", "SOXX", "ARKK", "VXX", "UVXY", "KWEB", "ITB", "XHB", "EWY",
})


def _ewma_sigma(retornos: np.ndarray) -> float:
    """σ diário EWMA (λ=0.94), inicializado na variância dos 20 primeiros."""
    var = float(np.var(retornos[:20]))
    for r in retornos[20:]:
        var = LAMBDA_EWMA * var + (1 - LAMBDA_EWMA) * r * r
    return math.sqrt(var)


def _ranges_decrescentes(range_pct: np.ndarray) -> int:
    """Quantos dias seguidos, contando do fim, o range encolheu."""
    n = 0
    for i in range(len(range_pct) - 1, 0, -1):
        if range_pct[i] < range_pct[i - 1]:
            n += 1
        else:
            break
    return n


def _true_range(df) -> np.ndarray:
    h = df["High"].to_numpy(dtype=float)
    l = df["Low"].to_numpy(dtype=float)
    c_prev = df["Close"].shift(1).to_numpy(dtype=float)
    return np.nanmax(
        np.stack([h - l, np.abs(h - c_prev), np.abs(l - c_prev)]), axis=0
    )


def _squeeze(close) -> tuple[bool, float | None]:
    """Largura de Bollinger (20, 2σ) no quintil inferior dos últimos 120d."""
    if len(close) < 140:
        return False, None
    std20 = close.rolling(20).std()
    sma20 = close.rolling(20).mean()
    largura = (4 * std20 / sma20).dropna()
    if len(largura) < 120:
        return False, None
    janela = largura.iloc[-120:]
    percentil = float((janela <= janela.iloc[-1]).mean() * 100)
    return percentil <= SQUEEZE_PERCENTIL, round(percentil, 1)


def _earnings_proximo(ticker: str) -> dict | None:
    """Data do próximo balanço se cai em JANELA_EARNINGS_DIAS. Ao vivo e
    tolerante: sem calendário (rede fora, ETF) devolve None sem reclamar."""
    t = ticker.upper()
    if t.startswith("^") or t in _SEM_EARNINGS:
        return None
    try:
        cal = yf.Ticker(t).calendar
        data = None
        if cal is not None:
            if hasattr(cal, "empty") and not cal.empty:
                datas = cal.columns.tolist()
                data = datas[0] if datas else None
            elif isinstance(cal, dict) and cal.get("Earnings Date"):
                data = cal["Earnings Date"][0]
        if data is None:
            return None
        import datetime as _dt
        # Timestamp e datetime são subclasses de date — normaliza tudo pra
        # date puro antes de subtrair, senão datetime - date estoura.
        if isinstance(data, _dt.datetime):
            d = data.date()
        elif isinstance(data, _dt.date):
            d = data
        else:
            d = _dt.date.fromisoformat(str(data)[:10])
        dias = (d - _dt.date.today()).days
        if 0 <= dias <= JANELA_EARNINGS_DIAS:
            return {"data": str(d)[:10], "dias": dias}
    except Exception:  # noqa: BLE001 — aviso opcional, nunca derruba o cálculo
        pass
    return None


def _classificar(razao, range_hoje, sigma_d, rvol, atr_tendencia,
                 ranges_dec, squeeze) -> tuple[str, list[str]]:
    """Fase + motivos legíveis. Ordem de prioridade importa: GATILHO é o
    evento do dia (vence tudo); EXPANSAO/DECAIMENTO descrevem o episódio em
    curso; COMPRIMIDA e NORMAL são os estados de repouso."""
    motivos: list[str] = []

    if range_hoje >= GATILHO_RANGE_X * sigma_d and (rvol or 0) >= GATILHO_RVOL and razao < RAZAO_EXPANSAO:
        motivos.append(
            f"range de hoje ({range_hoje * 100:.1f}%) ≥ {GATILHO_RANGE_X:.0f}× o σ esperado ({sigma_d * 100:.1f}%)"
        )
        motivos.append(f"volume {rvol:.1f}× a média")
        return "GATILHO", motivos

    if razao >= RAZAO_EXPANSAO and not (ranges_dec >= RANGES_DECRESCENTES_MIN or atr_tendencia == "caindo"):
        motivos.append(f"EWMA rodando {razao:.2f}× a vol estrutural")
        if atr_tendencia == "subindo":
            motivos.append("ATR14 subindo")
        return "EXPANSAO", motivos

    if razao >= RAZAO_DECAIMENTO and (ranges_dec >= RANGES_DECRESCENTES_MIN or atr_tendencia == "caindo"):
        if ranges_dec >= RANGES_DECRESCENTES_MIN:
            motivos.append(f"{ranges_dec} dias seguidos de range decrescente")
        if atr_tendencia == "caindo":
            motivos.append("ATR14 caindo")
        motivos.append(f"EWMA ainda {razao:.2f}× a estrutural")
        return "DECAIMENTO", motivos

    if squeeze or razao <= RAZAO_COMPRIMIDA:
        if squeeze:
            motivos.append("squeeze: largura de Bollinger no quintil inferior de 120d")
        if razao <= RAZAO_COMPRIMIDA:
            motivos.append(f"EWMA em só {razao:.2f}× a vol estrutural")
        motivos.append("mola armada: compressão costuma preceder expansão (dia e direção incertos)")
        return "COMPRIMIDA", motivos

    return "NORMAL", [f"EWMA {razao:.2f}× a estrutural, sem squeeze nem evento de range"]


def analisar(ticker: str) -> dict:
    hist = market_data_provider.get_daily_history(
        ticker, PERIODO, auto_adjust=True, permitir_externa=False
    )
    if not hist.ok:
        return {"ticker": ticker, "error": "; ".join(hist.warnings) or "Sem histórico"}
    df = hist.df
    if len(df) < MIN_PREGOES:
        return {"ticker": ticker, "error": f"Histórico insuficiente ({len(df)} pregões)"}

    close = df["Close"]
    retornos = close.pct_change().dropna().to_numpy(dtype=float)
    preco = float(close.iloc[-1])

    vol_estrutural_d = float(np.std(retornos, ddof=1))
    sigma_d = _ewma_sigma(retornos)
    razao = sigma_d / vol_estrutural_d if vol_estrutural_d > 0 else 1.0

    range_pct = ((df["High"] - df["Low"]) / df["Close"]).to_numpy(dtype=float)
    range_hoje = float(range_pct[-1])
    ranges_dec = _ranges_decrescentes(range_pct)

    tr = _true_range(df)
    atr_agora = float(np.nanmean(tr[-14:]))
    atr_antes = float(np.nanmean(tr[-19:-5]))
    if atr_antes > 0 and atr_agora > atr_antes * 1.05:
        atr_tendencia = "subindo"
    elif atr_antes > 0 and atr_agora < atr_antes * 0.95:
        atr_tendencia = "caindo"
    else:
        atr_tendencia = "estavel"

    volume = df["Volume"]
    vol_medio = float(volume.iloc[-21:-1].mean())
    rvol = float(volume.iloc[-1] / vol_medio) if vol_medio > 0 else None

    squeeze, squeeze_percentil = _squeeze(close)

    fase, motivos = _classificar(
        razao, range_hoje, sigma_d, rvol, atr_tendencia, ranges_dec, squeeze
    )

    # Decaimento exponencial do excesso: com λ=0.94 o excesso decai ~6%/dia;
    # dias até o excesso cair a 0.1 (razão ~1.1, de volta ao ruído).
    dias_normalizar = None
    excesso = razao - 1.0
    if excesso > 0.1:
        dias_normalizar = math.ceil(math.log(0.1 / excesso) / math.log(LAMBDA_EWMA))

    saida = {
        "ticker": ticker,
        "fase": fase,
        "motivos": motivos,
        "preco": round(preco, 2),
        "sigmaDiaPct": round(sigma_d * 100, 2),
        "volEstruturalAnualPct": round(vol_estrutural_d * math.sqrt(252) * 100, 1),
        "ewmaAnualPct": round(sigma_d * math.sqrt(252) * 100, 1),
        "razaoRegime": round(razao, 2),
        "bandaAmanha": {
            "low": round(preco * (1 - sigma_d), 2),
            "high": round(preco * (1 + sigma_d), 2),
            "low2": round(preco * (1 - 2 * sigma_d), 2),
            "high2": round(preco * (1 + 2 * sigma_d), 2),
        },
        "rangeHojePct": round(range_hoje * 100, 2),
        "rangesDecrescentes": ranges_dec,
        "atrTendencia": atr_tendencia,
        "rvol": round(rvol, 2) if rvol is not None else None,
        "squeeze": squeeze,
        "squeezePercentil": squeeze_percentil,
        "diasParaNormalizar": dias_normalizar,
        "diasUsados": len(retornos),
    }
    earnings = _earnings_proximo(ticker)
    if earnings:
        saida["earningsProximo"] = earnings
        saida["motivos"].append(
            f"balanço em {earnings['dias']} dia(s) ({earnings['data']}) — use o threshold da Reação a Earnings, não a banda de vol"
        )
    if hist.source not in ("yfinance", "yfinance_cache"):
        saida["fonteHistorico"] = hist.source
    return saida


if __name__ == "__main__":
    args = json.loads(sys.stdin.read() or "{}")
    tickers = [str(t).strip().upper() for t in args.get("tickers", []) if str(t).strip()]
    items = []
    for t in dict.fromkeys(tickers):
        try:
            items.append(analisar(t))
        except Exception as e:  # noqa: BLE001 — um ticker ruim não derruba o lote
            items.append({"ticker": t, "error": str(e) or e.__class__.__name__})
    print(json_seguro.dumps({"items": items}, ensure_ascii=False))
