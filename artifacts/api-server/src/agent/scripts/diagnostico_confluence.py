"""Diagnóstico do ConfluenceEngine: ONDE a estratégia perde do buy & hold.

Script manual de pesquisa, irmão do backtest_confluence.py (mesmo padrão: NÃO
é invocado pelo Node; rode direto). Ele responde a pergunta que o grid search
deixou aberta. O grid mostrou +3,36% contra +656,71% de buy & hold na MU e
parou aí -- um número final que mistura DOIS suspeitos com defesas opostas:

  1. O SINAL é ruim (compra na hora errada, vende na certa)?
  2. O SIZING esmaga o resultado?

A suspeita 2 tem uma pista aritmética que este script confirma com dados: os
priors placeholder do Kelly (win 0.5, avg_win 0.05, avg_loss 0.03) produzem

    full_kelly = (1,667*0,5 - 0,5)/1,667 = 0,20  ->  * 0,3 = size_frac 0,06

ou seja, CADA TRADE DO BACKTEST ARRISCA 6% DO CAPITAL. O headline compara uma
estratégia exposta a 6% contra um buy & hold exposto a 100% -- réguas
diferentes. Se o retorno cru dos trades (a 100% de exposição) for positivo, o
motor tem sinal aproveitável e o problema é dimensionamento e tempo fora do
mercado; se for negativo, o sinal é ruim e nenhum sizing conserta.

O que este script decompõe, por ticker x regime, sempre com min_votes=4 (o
default de produção em routes/confluence.ts e o melhor do grid onde algo
dispara):

  a. Retorno composto CRU dos trades a 100% de exposição -- e separado por
     direção, porque vender a descoberto dentro de um superciclo de alta é um
     candidato óbvio a ralo de dinheiro que o agregado esconde.
  b. Tempo de exposição: % de pregões comprado / vendido / fora. Estratégia
     fora do mercado na maior parte de um rali perde por ausência, não por
     erro de leitura.
  c. Captura: o retorno composto DO ATIVO separado pelos dias em que a
     estratégia estava comprada / fora / vendida. É a resposta direta a
     "ficou de fora dos dias que mais subiram?".
  d. Os 10 melhores e 10 piores dias do ativo e o estado da posição em cada
     um -- concentração importa: em papel de memória, meia dúzia de pregões
     decide o ano.
  e. Duração dos trades e % de trades de 1 dia (whipsaw): confluência de 4/5
     votos desmonta com qualquer voto que oscile, e saída no primeiro
     enfraquecimento é hipótese concreta de "sai cedo demais".

As funções de métrica são PURAS (DataFrame/lista entram, dict sai) de
propósito: os testes cobrem a aritmética com fixtures sintéticas, sem rede --
regra da suíte desde o incidente dos testes que passavam sem rede e quebravam
no CI (ver test_macro_risk_snapshot.py).

Rode (na VPS, dentro do container -- este sandbox não alcança o yfinance):

    docker compose exec -T -w /app/artifacts/api-server/src/agent/scripts app \
      /app/.venv/bin/python3 diagnostico_confluence.py < /dev/null
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from confluence_engine import ConfluenceEngine, run_backtest  # noqa: E402
from backtest_confluence import (  # noqa: E402
    REGIMES, TICKERS, _fetch_all, _sector_returns_excluding,
)

# O default de produção (routes/confluence.ts: `minVotes ?? 4`) e o único
# nível do grid em que a estratégia opera de fato nos dois regimes.
MIN_VOTES = 4

RESULTS_MD_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "confluence_diagnostico_results.md"
)


# ── métricas puras ───────────────────────────────────────────────────────────

def decompor_trades(trades: list) -> dict:
    """Retorno cru dos trades fechados, agregado e por direção.

    "Cru" = cada trade com 100% do capital: prod(1 + pnl). É a régua que
    isola o SINAL do sizing. `so_long` responde "e se os sells virassem
    apenas saída, nunca short?" -- comparação direta com um mercado em alta.
    """
    fechados = [t for t in trades if "pnl_pct" in t]
    longs = [t for t in fechados if t["direction"] == 1]
    shorts = [t for t in fechados if t["direction"] == -1]

    def _composto(ts: list) -> float:
        acc = 1.0
        for t in ts:
            acc *= 1.0 + t["pnl_pct"]
        return (acc - 1.0) * 100

    def _duracao_dias(t: dict) -> int:
        return (pd.Timestamp(t["exit_date"]) - pd.Timestamp(t["entry_date"])).days

    duracoes = [_duracao_dias(t) for t in fechados]
    return {
        "n_trades": len(fechados),
        "n_long": len(longs),
        "n_short": len(shorts),
        "retorno_cru_pct": _composto(fechados),
        "retorno_cru_long_pct": _composto(longs),
        "retorno_cru_short_pct": _composto(shorts),
        "win_rate_long": (sum(1 for t in longs if t["pnl_pct"] > 0) / len(longs)) if longs else None,
        "win_rate_short": (sum(1 for t in shorts if t["pnl_pct"] > 0) / len(shorts)) if shorts else None,
        "duracao_mediana_dias": float(np.median(duracoes)) if duracoes else None,
        # Trade aberto e fechado em pregões consecutivos: a confluência
        # desmontou no primeiro enfraquecimento. Muitos deles = whipsaw.
        "pct_trades_curtos": (
            100.0 * sum(1 for d in duracoes if d <= 3) / len(duracoes) if duracoes else None
        ),
    }


def serie_de_posicao(indice: pd.DatetimeIndex, trades: list) -> pd.Series:
    """Posição vigente para ATRIBUIR o retorno de cada pregão.

    A entrada acontece no fechamento do dia da entrada (semântica de
    run_backtest), então o primeiro retorno que a posição captura é o do
    pregão SEGUINTE -- e o último é o do próprio dia de saída. Por isso o
    intervalo é (entry_date, exit_date]: exclusivo na entrada, inclusivo na
    saída. Errar essa borda desloca a atribuição em um dia e, em papel que
    gapa 10% em earnings, um dia é a análise inteira.
    """
    pos = pd.Series(0, index=indice, dtype=int)
    for t in trades:
        entrada = pd.Timestamp(t["entry_date"])
        saida = pd.Timestamp(t.get("exit_date", str(indice[-1])[:10]))
        mask = (indice > entrada) & (indice <= saida)
        pos[mask] = t["direction"]
    return pos


def atribuicao_diaria(df: pd.DataFrame, pos: pd.Series) -> dict:
    """Quanto o ATIVO rendeu enquanto a estratégia estava em cada estado.

    `comprado_pct` alto com `fora_pct` alto = o rali aconteceu com e sem a
    estratégia, e ela perdeu a parte de fora. `fora_pct` concentrando o
    retorno = o motor sistematicamente não está lá quando o papel anda.
    """
    ret = df["close"].pct_change()

    def _composto(mask: pd.Series) -> float:
        r = ret[mask].dropna()
        return (float(np.prod(1.0 + r.values)) - 1.0) * 100 if len(r) else 0.0

    dias_validos = ret.notna()
    n = int(dias_validos.sum())
    melhores = ret.nlargest(10).index
    piores = ret.nsmallest(10).index
    return {
        "pregoes": n,
        "pct_dias_comprado": 100.0 * int(((pos == 1) & dias_validos).sum()) / n if n else 0.0,
        "pct_dias_vendido": 100.0 * int(((pos == -1) & dias_validos).sum()) / n if n else 0.0,
        "pct_dias_fora": 100.0 * int(((pos == 0) & dias_validos).sum()) / n if n else 0.0,
        "ativo_enquanto_comprado_pct": _composto(pos == 1),
        "ativo_enquanto_fora_pct": _composto(pos == 0),
        "ativo_enquanto_vendido_pct": _composto(pos == -1),
        # Estado nos dias extremos. Dict e não contagem: o relatório mostra a
        # data, o retorno do dia e onde a estratégia estava.
        "melhores_dias": [
            {"data": str(d)[:10], "ret_pct": float(ret[d]) * 100, "posicao": int(pos[d])}
            for d in melhores
        ],
        "piores_dias": [
            {"data": str(d)[:10], "ret_pct": float(ret[d]) * 100, "posicao": int(pos[d])}
            for d in piores
        ],
    }


def equity_hipotetica(trades: list, frac: float) -> float:
    """Retorno total (%) se cada trade usasse `frac` do capital.

    Com frac=0.06 reproduz o headline do grid; com frac=1.0 dá a régua
    comparável ao buy & hold. A diferença entre os dois é o custo do sizing.
    """
    acc = 1.0
    for t in trades:
        if "pnl_pct" in t:
            acc *= 1.0 + t["pnl_pct"] * frac
    return (acc - 1.0) * 100


# ── relatório ────────────────────────────────────────────────────────────────

def _estado(p: int) -> str:
    return {1: "comprado", 0: "FORA", -1: "vendido"}[p]


def _diagnosticar(ticker: str, df: pd.DataFrame, sector_returns) -> str:
    engine = ConfluenceEngine(min_votes=MIN_VOTES, kelly_fraction=0.3)
    # long_only=False de propósito: o diagnóstico mede o SINAL cru, dos dois
    # lados. Foi a medição dos dois lados que justificou o default long_only
    # do run_backtest -- herdá-lo aqui apagaria a régua que o sustenta.
    res = run_backtest(df, engine, sector_returns=sector_returns, long_only=False)
    trades = res["trades"]

    dec = decompor_trades(trades)
    pos = serie_de_posicao(df.index, trades)
    atr = atribuicao_diaria(df, pos)
    bh_pct = (float(df["close"].iloc[-1] / df["close"].iloc[0]) - 1.0) * 100

    linhas = [
        f"### {ticker} ({df.index[0].date()} a {df.index[-1].date()}, min_votes={MIN_VOTES})",
        "",
        f"- Buy & hold: **{bh_pct:.2f}%**",
        f"- Estratégia como o grid mediu (size_frac={res['kelly_size_frac_used']:.3f}): "
        f"**{res['total_return_pct']:.2f}%**",
        f"- Mesmos trades a 100% de exposição: **{equity_hipotetica(trades, 1.0):.2f}%**",
        f"- Retorno cru só dos LONGS: **{dec['retorno_cru_long_pct']:.2f}%** "
        f"({dec['n_long']} trades, win {100*(dec['win_rate_long'] or 0):.0f}%)",
        f"- Retorno cru só dos SHORTS: **{dec['retorno_cru_short_pct']:.2f}%** "
        f"({dec['n_short']} trades, win {100*(dec['win_rate_short'] or 0):.0f}%)",
        "",
        f"- Exposição: {atr['pct_dias_comprado']:.1f}% dos pregões comprado, "
        f"{atr['pct_dias_vendido']:.1f}% vendido, {atr['pct_dias_fora']:.1f}% fora",
        f"- O ativo rendeu **{atr['ativo_enquanto_comprado_pct']:.2f}%** nos dias comprados, "
        f"**{atr['ativo_enquanto_fora_pct']:.2f}%** nos dias fora, "
        f"**{atr['ativo_enquanto_vendido_pct']:.2f}%** nos dias vendidos",
        f"- Duração mediana do trade: {dec['duracao_mediana_dias']:.0f} dias; "
        f"{dec['pct_trades_curtos']:.0f}% duram até 3 dias",
        "",
        "| 10 melhores dias | ret | posição |  | 10 piores dias | ret | posição |",
        "|---|---|---|---|---|---|---|",
    ]
    for m, p in zip(atr["melhores_dias"], atr["piores_dias"]):
        linhas.append(
            f"| {m['data']} | {m['ret_pct']:+.1f}% | {_estado(m['posicao'])} |  "
            f"| {p['data']} | {p['ret_pct']:+.1f}% | {_estado(p['posicao'])} |"
        )
    return "\n".join(linhas)


def main() -> None:
    secoes = [
        "# Diagnóstico — onde o ConfluenceEngine perde do buy & hold\n",
        "O grid search parou no placar; este relatório abre o jogo. As três "
        "réguas por ticker: o número que o grid mostrou (trades a ~6% do "
        "capital, pelos priors placeholder do Kelly), os MESMOS trades a 100% "
        "de exposição, e o retorno cru separado por direção. Ver docstring de "
        "scripts/diagnostico_confluence.py para o porquê de cada métrica.\n",
    ]
    for regime, fetch_kwargs in REGIMES.items():
        print(f"\n{'#'*70}\nREGIME: {regime}\n{'#'*70}", file=sys.stderr)
        dfs = _fetch_all(TICKERS, fetch_kwargs)
        if not dfs:
            print(f"ERRO: nenhum ticker buscado para '{regime}'", file=sys.stderr)
            continue
        secoes.append(f"\n## {regime}\n")
        for t in dfs:
            print(f"-- {t}...", file=sys.stderr)
            secoes.append(_diagnosticar(t, dfs[t], _sector_returns_excluding(dfs, t)))

    with open(RESULTS_MD_PATH, "w") as f:
        f.write("\n\n".join(secoes) + "\n")
    print(f"\nRelatório salvo em {RESULTS_MD_PATH}", file=sys.stderr)
    # stdout fica livre pro usuário redirecionar; o relatório também sai nele
    # porque na VPS o arquivo fica DENTRO do container e morre no rebuild.
    with open(RESULTS_MD_PATH) as f:
        print(f.read())


if __name__ == "__main__":
    main()
