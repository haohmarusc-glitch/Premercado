"""
Modo earnings do Painel de Cenários: as três volatilidades lado a lado.

## O problema que isto resolve

A vol que o painel usa (get_scenario_params.py) é o desvio-padrão dos retornos
diários de 12 meses, anualizado. Ela é simétrica, não tem drift e DILUI o dia
do balanço entre 251 dias comuns. Quando a janela do cenário contém um
earnings, isso não é uma aproximação -- é o número errado. Medido na auditoria
de 17/08/2026:

  PDD    modelo ±4,7%/sem  vs  realizado em earnings 10,3%, centro -8,2%
         (subestimou mais de 50% E errou o centro em 8 p.p.)
  XPEV   modelo ±7,7%      vs  realizado 6,7% -- em linha. Mas a implícita das
         opções estava em 10,6%: o desalinhamento entre implícita e realizada
         É o sinal, mesmo quando o modelo acerta.
  MRVL   modelo ±11%       vs  realizado 13,4%, porém BIMODAL (±19-23% ou
         quase nada). A lognormal concentra massa num centro que a história
         desse papel não frequenta.

Nenhum dos três casos se resolve trocando a fórmula. Resolve-se mostrando as
três leituras juntas e deixando o desalinhamento visível.

## As três vols

  modelo      difusão do painel, escalada para uma semana (vem do TS, ver
              volModeloSemanalPct em @workspace/scenario-math)
  realizada   |média| das reações passadas a earnings deste ticker, com o
              centro/viés ao lado -- earnings_reaction_analysis.py
  implícita   o que as opções cobram HOJE para atravessar o evento: straddle
              ATM do primeiro vencimento após o balanço, dividido pelo spot

## Procedência da implícita, em ordem

  1. straddle ATM ao vivo (yfinance)         -> fonte "straddle_atm"
  2. coleta manual do OptionSlam, com data   -> fonte "manual"
  3. nada                                    -> null, e a tela não mostra selo

O passo 2 é o mesmo princípio da Tarefa 1: dado manual COM carimbo é honesto.
O passo 3 é deliberado -- selo comparando contra um número ausente seria pior
que a ausência do selo.

Uso:
    echo '{"tickers":["PDD"],"ate":"2026-10-07"}' | python3 -m agent.earnings_window
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime

import pandas as pd

try:  # import duplo: spawn por caminho (routes/scenarios.ts) e como pacote
    from bounded_parallel import deadline_exceeded
    import earnings_dates as _earnings_dates
    import radar_overrides as _overrides
    from earnings_reaction_analysis import analyze_ticker
    from security import sanitize_ticker
except ImportError:
    from agent.bounded_parallel import deadline_exceeded
    from agent import earnings_dates as _earnings_dates
    from agent import radar_overrides as _overrides
    from agent.earnings_reaction_analysis import analyze_ticker
    from agent.security import sanitize_ticker

import yfinance as yf

# Quantos balanços passados alimentam a "realizada". 8 = dois anos de
# trimestres: amostra grande o bastante para uma média fazer sentido e curta o
# bastante para ainda descrever a empresa de hoje. Mesmo default do painel de
# Reação a Earnings.
LOOKBACK_EVENTOS = 8

# Strikes ao redor do spot considerados "ATM" quando não existe strike exato no
# dinheiro. 1 = pega o mais próximo e pronto; o straddle é pouco sensível a um
# strike de distância, e alargar demais mistura opções fora do dinheiro (mais
# baratas), o que puxaria a implícita para baixo.
ATM_STRIKES = 1


# ── implícita: straddle ATM ─────────────────────────────────────────────────

def escolher_vencimento(vencimentos: list[str], earnings_iso: str) -> str | None:
    """Primeiro vencimento QUE COBRE o balanço.

    Tem que ser >= a data do earnings: um straddle que vence antes do evento
    não precifica o evento nenhum -- seria comprar seguro que expira na véspera
    do incêndio. Entre os que cobrem, o mais curto é o melhor proxy do move do
    evento, porque carrega menos tempo (e menos vol de calendário) além dele.
    """
    try:
        alvo = date.fromisoformat(earnings_iso)
    except (TypeError, ValueError):
        return None
    validos = []
    for v in vencimentos or []:
        try:
            d = date.fromisoformat(v)
        except (TypeError, ValueError):
            continue
        if d >= alvo:
            validos.append((d, v))
    if not validos:
        return None
    return min(validos)[1]


def _preco_meio(linha) -> float | None:
    """Meio do book, com lastPrice como último recurso.

    bid/ask é o preço que existe AGORA; lastPrice pode ser de um negócio de
    horas atrás, em outro nível de spot. Só serve quando o book veio vazio --
    fora do pregão o yfinance costuma zerar bid/ask, e uma implícita defasada
    ainda informa mais que nenhuma.
    """
    bid = float(linha.get("bid") or 0)
    ask = float(linha.get("ask") or 0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    ultimo = float(linha.get("lastPrice") or 0)
    return ultimo if ultimo > 0 else None


def move_implicito_do_chain(calls: pd.DataFrame, puts: pd.DataFrame,
                            spot: float) -> tuple[float | None, str | None]:
    """(move implícito em %, motivo da falha). Pura -- testável sem rede.

    Straddle ATM / spot é a regra de bolso padrão de mesa para "quanto o
    mercado está pagando por este evento". Não é a fórmula exata do move
    implícito (que integraria a cadeia inteira), e a diferença é de 1-2 p.p.
    numa cadeia líquida -- o que importa aqui é a COMPARAÇÃO com a realizada,
    e para essa ordem de grandeza o straddle basta.
    """
    if not spot or spot <= 0:
        return None, "sem preço spot"
    if calls is None or puts is None or calls.empty or puts.empty:
        return None, "cadeia de opções vazia"

    # O strike tem que existir nos DOIS lados -- straddle é call + put no MESMO
    # strike. Casar strikes diferentes somaria um spread diagonal e chamaria de
    # straddle.
    comuns = sorted(set(calls["strike"]).intersection(set(puts["strike"])))
    if not comuns:
        return None, "sem strike comum entre calls e puts"
    strike = min(comuns, key=lambda s: abs(s - spot))
    if abs(strike - spot) / spot > 0.10:
        # Strike mais próximo a mais de 10% do dinheiro: cadeia rala demais
        # para chamar de ATM. Melhor cair no fallback manual que devolver um
        # número que parece implícita e não é.
        return None, f"strike mais próximo ({strike}) longe demais do spot ({round(spot, 2)})"

    call = calls[calls["strike"] == strike].iloc[0]
    put = puts[puts["strike"] == strike].iloc[0]
    c, p = _preco_meio(call), _preco_meio(put)
    if c is None or p is None:
        return None, "sem preço utilizável no strike ATM"
    return round((c + p) / spot * 100, 2), None


def _implicito_ao_vivo(ticker: str, t, earnings_iso: str) -> tuple[dict | None, str | None]:
    try:
        vencimentos = list(t.options or [])
    except Exception as e:  # noqa: BLE001 — cadeia de opções é opcional
        return None, f"{type(e).__name__}: {e}"
    venc = escolher_vencimento(vencimentos, earnings_iso)
    if venc is None:
        return None, "nenhum vencimento cobre a data do balanço"
    try:
        cadeia = t.option_chain(venc)
        spot = getattr(t.fast_info, "last_price", None)
        pct, motivo = move_implicito_do_chain(cadeia.calls, cadeia.puts, float(spot or 0))
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"
    if pct is None:
        return None, motivo
    return {"pct": pct, "fonte": "straddle_atm", "vencimento": venc}, None


def _implicito_manual(ticker: str) -> dict | None:
    """Move implícito semanal da coleta do OptionSlam, com o carimbo junto.

    O carimbo viaja com o número de propósito: quem lê a tela precisa poder
    descontar a idade sozinho. Um 7,38% coletado hoje e um coletado há três
    semanas são leituras diferentes, e sem a data eles são idênticos.
    """
    reacao, coletado_em, fonte = _overrides.carregar()
    linha = reacao.get(ticker.upper()) or {}
    pct = linha.get("move_impl_sem")
    if pct is None:
        return None
    return {
        "pct": float(pct),
        "fonte": "manual",
        "fonteNome": fonte,
        "coletadoEm": coletado_em,
        "idadeDias": _overrides.idade_dias(coletado_em),
    }


# ── próximo balanço dentro da janela ────────────────────────────────────────

def proximo_earnings(datas: pd.DataFrame | None, hoje: date) -> str | None:
    """Primeira data de balanço a partir de hoje (ISO). Pura -- testável.

    `>= hoje` e não `> hoje`: um balanço marcado para HOJE ainda está à frente
    do preço em quase todo o dia (BMO reage no próprio pregão, AMC no
    seguinte). Excluí-lo esconderia o evento justamente no dia em que ele mais
    importa.
    """
    if datas is None or datas.empty:
        return None
    futuras = []
    for ts in datas.index:
        try:
            d = ts.tz_localize(None).normalize().date() if ts.tzinfo else ts.normalize().date()
        except Exception:  # noqa: BLE001 — índice heterogêneo não derruba a busca
            continue
        if d >= hoje:
            futuras.append(d)
    return min(futuras).isoformat() if futuras else None


def analisar(ticker: str, ate_iso: str, hoje: date | None = None,
             lookback: int = LOOKBACK_EVENTOS) -> dict:
    """Um ticker: há balanço até `ate_iso`? Se sim, as leituras para compará-lo."""
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": str(ticker), "error": str(e)}

    hoje = hoje or date.today()
    try:
        ate = date.fromisoformat(ate_iso)
    except (TypeError, ValueError):
        return {"ticker": ticker, "error": f"data-alvo inválida: {ate_iso!r}"}

    t = yf.Ticker(ticker)
    limite = lookback + 6
    datas, fonte_datas, erro_datas = _earnings_dates.buscar(
        ticker, lambda: t.get_earnings_dates(limit=limite), limit=limite
    )
    if datas is None:
        return {"ticker": ticker, "error": f"falha ao buscar datas de balanço: {erro_datas}"}

    proximo = proximo_earnings(datas, hoje)
    saida: dict = {"ticker": ticker, "proximoEarnings": proximo, "naJanela": False}
    if fonte_datas == "cache_vencido":
        # Mesmo vocabulário do resto do agente: dado de cópia vencida vem
        # MARCADO. Aqui pesa mais que o normal -- uma data de balanço
        # reagendada muda a resposta de "está na janela?" de sim para não.
        saida["fonteDatas"] = fonte_datas

    if proximo is None or date.fromisoformat(proximo) > ate:
        # Sem balanço na janela o painel está no seu terreno: difusão pura
        # basta, e a tela não mostra o card. Não é erro, é o caso comum.
        return saida

    saida["naJanela"] = True
    reacao = analyze_ticker(ticker, lookback)
    if "error" in reacao:
        saida["error"] = reacao["error"]
        return saida
    saida["reacao"] = reacao["summary"]

    implicito, motivo = _implicito_ao_vivo(ticker, t, proximo)
    if implicito is None:
        # Degradação anunciada, nunca silenciosa: a tela precisa poder dizer
        # "implícita veio da coleta manual de N dias atrás" em vez de exibir o
        # número como se tivesse acabado de sair do book.
        if motivo:
            print(f"[earnings_window] {ticker}: straddle indisponível ({motivo}); "
                  f"tentando coleta manual", file=sys.stderr, flush=True)
        implicito = _implicito_manual(ticker)
        if implicito is not None:
            implicito["motivoFallback"] = motivo
    saida["implicito"] = implicito
    return saida


def main() -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    tickers = [str(t).strip().upper() for t in (payload.get("tickers") or []) if str(t).strip()]
    ate = str(payload.get("ate") or "")
    lookback = int(payload.get("lookback") or LOOKBACK_EVENTOS)
    hoje = None
    if payload.get("hoje"):  # injetável para teste/replay; produção usa o relógio
        hoje = datetime.fromisoformat(str(payload["hoje"])).date()

    itens = []
    for tk in tickers:
        # Resultado parcial vale mais que timeout: sem isto o laço roda até o
        # Node matar o processo e tudo que já foi buscado se perde.
        if deadline_exceeded():
            itens.append({"ticker": tk, "error": "orçamento de tempo esgotado"})
            continue
        try:
            itens.append(analisar(tk, ate, hoje, lookback))
        except Exception as e:  # noqa: BLE001 — um ticker ruim não leva os outros
            print(f"[earnings_window] {tk}: {type(e).__name__}: {e}", file=sys.stderr, flush=True)
            itens.append({"ticker": tk, "error": f"{type(e).__name__}: {e}"})

    print(json.dumps({"items": itens}, ensure_ascii=False))


if __name__ == "__main__":
    main()
