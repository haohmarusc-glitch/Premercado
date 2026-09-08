"""Em que dia do mês a carteira sobe mais?

RESPOSTA CURTA, antes dos números: com cinco anos de histórico esta pergunta
não tem resposta conclusiva, e o script diz por quê em vez de escondê-lo.

O poder do teste
----------------
Retorno diário de uma carteira concentrada em semis/IA tem desvio-padrão de
~2% ao dia. Um dia específico do mês aparece ~60 vezes em cinco anos. Com
isso, a menor diferença que o teste enxerga com 80% de confiança é da ordem
de 0,7% AO DIA -- e o efeito de calendário mais conhecido da literatura (a
"virada do mês", Ariel 1987 / Lakonishok & Smidt 1988) vale 0,1% a 0,2%.

A lupa é de 4 a 7 vezes menos sensível que a coisa procurada. Então:

  - "nada significativo" NÃO é evidência de que não há padrão;
  - algo que aparecesse com 0,7%/dia quase certamente seria ruído, porque
    uma anomalia de calendário desse tamanho já teria sido arbitrada.

O relatório imprime esse piso ao lado de cada bloco, para nenhum número ser
lido sem a sua resolução.

Desenho
-------
DUAS famílias, e a diferença entre elas é o que separa evidência de garimpo:

  PRIMÁRIA (1 teste)     a janela de virada do mês -- último pregão do mês
                         mais os três primeiros do seguinte. Hipótese
                         ESCOLHIDA ANTES de olhar os dados, vinda da
                         literatura. Um teste só, sem correção a fazer.

  EXPLORATÓRIA (~55)     varredura de todo dia de pregão do mês, dos três
                         últimos, e de todo dia do calendário. Aqui a
                         correção de Holm é obrigatória: varrer 55 buckets a
                         5% produz ~3 "achados" por acaso em toda rodada, e
                         o achado sempre vem com uma história convincente
                         depois de encontrado.

Nada da família exploratória sustenta uma decisão sozinho. Ela serve para
gerar hipótese a ser testada em OUTRA amostra -- outro período, outro
conjunto de papéis.

Estatística reaproveitada de padroes_estatisticos.py (permutação + Holm +
bootstrap), não reescrita: indicador com duas implementações no mesmo repo é
o §2b do playbook esperando para acontecer.

Uso
---
    python3 -m agent.scripts.dia_do_mes                # carteira do config
    python3 -m agent.scripts.dia_do_mes NVDA AVGO MRVL # lista explícita
    ANOS=15 python3 -m agent.scripts.dia_do_mes        # mais história = mais poder
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

from agent import config, market_data_provider
from agent.padroes_estatisticos import (
    ANOS_PADRAO, MIN_OBS, _linha, holm,
)

# Fração mínima da carteira com preço no dia para o dia entrar na série. Um
# pregão em que só um papel tem dado não é "a carteira": é aquele papel.
COBERTURA_MINIMA = 0.5

# A janela pré-registrada. Último pregão do mês + os três primeiros do
# seguinte, que é a definição da literatura -- não uma escolhida por dar
# resultado.
TOM_INICIO = (1, 2, 3)
TOM_FIM = (-1,)


def carteira() -> tuple[list[str], str]:
    """(tickers, de onde vieram). A origem sai no relatório de propósito: a
    lista fixa do config é ÚLTIMO recurso, e já competiu com a carteira real
    do banco e ganhou (ver o comentário dela)."""
    if len(sys.argv) > 1:
        return [t.strip().upper() for t in sys.argv[1:] if t.strip()], "argumentos"
    env = os.environ.get("AGENT_PORTFOLIO_TICKERS", "")
    if env.strip():
        return [t.strip().upper() for t in env.split(",") if t.strip()], "AGENT_PORTFOLIO_TICKERS"
    return list(config.PORTFOLIO_TICKERS), "lista fixa do config (NÃO é a carteira do banco)"


def serie_da_carteira(tickers: list[str], anos: int) -> tuple[pd.Series, dict]:
    """Retorno diário equiponderado. Equiponderado e não por tamanho de
    posição: a pergunta é sobre o comportamento dos papéis, e peso atual
    mudaria a resposta a cada compra."""
    lote = market_data_provider.get_daily_closes_batch(
        tickers, f"{anos}y", auto_adjust=True, permitir_externa=False
    )
    if not lote.ok:
        raise SystemExit(f"sem dados: {'; '.join(lote.warnings) or 'lote vazio'}")

    ret = lote.closes.pct_change()
    cobertura = ret.notna().sum(axis=1) / max(1, ret.shape[1])
    ret = ret[cobertura >= COBERTURA_MINIMA]
    serie = ret.mean(axis=1, skipna=True).dropna()
    return serie, lote.fontes


def _indices_do_mes(idx: pd.DatetimeIndex) -> tuple[np.ndarray, np.ndarray]:
    """(ordem do pregão no mês a partir de 1, e a partir do fim com sinal
    negativo). Pregão e não dia do calendário: dia 15 cai em fim de semana um
    terço das vezes, e o investidor não consegue negociar nele."""
    chave = pd.Series(idx.year * 100 + idx.month, index=idx)
    do_inicio = chave.groupby(chave).cumcount().to_numpy() + 1
    total = chave.map(chave.value_counts()).to_numpy()
    do_fim = do_inicio - total - 1          # último pregão = -1
    return do_inicio, do_fim


def piso_detectavel(n_grupo: int, n_resto: int, sigma: float) -> float:
    """Menor diferença de média detectável com ~80% de poder, em % ao dia."""
    if n_grupo < 1 or n_resto < 1:
        return float("nan")
    se = sigma * np.sqrt(1.0 / n_grupo + 1.0 / n_resto)
    return 2.8 * se * 100


def _com_piso(linha: dict, n_resto: int, sigma: float) -> dict:
    linha["piso_detectavel_pct"] = round(piso_detectavel(linha["n"], n_resto, sigma), 3)
    return linha


def analisar(serie: pd.Series) -> dict:
    valores = serie.to_numpy(dtype=float)
    sigma = float(valores.std(ddof=1))
    do_inicio, do_fim = _indices_do_mes(serie.index)
    dia_cal = serie.index.day.to_numpy()

    # ── primária: a janela pré-registrada ────────────────────────────────
    na_janela = np.isin(do_inicio, TOM_INICIO) | np.isin(do_fim, TOM_FIM)
    primaria = _com_piso(
        _linha("virada do mês (último pregão + 3 primeiros)",
               valores[na_janela], valores[~na_janela]),
        int((~na_janela).sum()), sigma)

    # ── exploratória ─────────────────────────────────────────────────────
    exploratoria = []
    for d in range(1, int(do_inicio.max()) + 1):
        m = do_inicio == d
        exploratoria.append(_com_piso(
            _linha(f"{d}º pregão do mês", valores[m], valores[~m]),
            int((~m).sum()), sigma))
    for d in (-1, -2, -3):
        m = do_fim == d
        rot = {-1: "último pregão", -2: "penúltimo pregão", -3: "antepenúltimo"}[d]
        exploratoria.append(_com_piso(
            _linha(rot, valores[m], valores[~m]), int((~m).sum()), sigma))
    for d in range(1, 32):
        m = dia_cal == d
        exploratoria.append(_com_piso(
            _linha(f"dia {d} do calendário", valores[m], valores[~m]),
            int((~m).sum()), sigma))

    holm(exploratoria)
    return {"sigma_diario_pct": round(sigma * 100, 3),
            "primaria": primaria, "exploratoria": exploratoria}


def _fmt(linha: dict) -> str:
    ret = linha["retorno_medio_pct"]
    ic = linha["ic95_pct"]
    p = linha["p_valor"]
    marca = "  <-- SOBREVIVE A HOLM" if linha.get("sobrevive") else ""
    corpo = (f"{linha['rotulo']:<44} n={linha['n']:>4}  "
             f"média={ret:+.3f}%" if ret is not None else
             f"{linha['rotulo']:<44} n={linha['n']:>4}  média=  ---  ")
    if ic:
        corpo += f"  IC95=[{ic[0]:+.3f}, {ic[1]:+.3f}]"
    corpo += f"  p={p}" if p is not None else "  p=(amostra pequena)"
    corpo += f"  piso={linha['piso_detectavel_pct']:.2f}%"
    return corpo + marca


def main() -> None:
    tickers, origem = carteira()
    anos = int(os.environ.get("ANOS", ANOS_PADRAO))
    serie, fontes = serie_da_carteira(tickers, anos)

    print("=" * 100)
    print(f"Dia do mês x retorno — carteira equiponderada, {anos} anos")
    print(f"Papéis ({origem}): {', '.join(tickers)}")
    degradadas = {t: f for t, f in fontes.items() if f not in ("yfinance", "yfinance_cache")}
    if degradadas:
        print(f"ATENÇÃO — séries de origem degradada: {degradadas}")
    print(f"Pregões na série: {len(serie)}  ({serie.index[0].date()} a {serie.index[-1].date()})")
    print("=" * 100)

    r = analisar(serie)
    print(f"\nDesvio-padrão diário da carteira: {r['sigma_diario_pct']:.3f}%")
    print("`piso` = menor diferença que o teste enxerga com ~80% de poder.")
    print("Referência: o efeito 'virada do mês' da literatura vale 0,10% a 0,20%/dia.\n")

    print("-" * 100)
    print("PRIMÁRIA — hipótese escolhida ANTES de olhar os dados. Um teste, sem correção.")
    print("-" * 100)
    print(_fmt(r["primaria"]))

    print("\n" + "-" * 100)
    print(f"EXPLORATÓRIA — {len(r['exploratoria'])} buckets, corrigidos por Holm.")
    print("Nada daqui sustenta decisão sozinho: serve para gerar hipótese a testar")
    print("em OUTRA amostra (outro período, outros papéis).")
    print("-" * 100)
    testados = [x for x in r["exploratoria"] if x["p_valor"] is not None]
    sobreviventes = [x for x in testados if x.get("sobrevive")]
    for linha in sorted(testados, key=lambda x: x["p_valor"])[:12]:
        print(_fmt(linha))
    if len(testados) > 12:
        print(f"... e mais {len(testados) - 12} buckets com p maior.")
    pulados = len(r["exploratoria"]) - len(testados)
    if pulados:
        print(f"\n{pulados} buckets ficaram fora do teste por terem menos de {MIN_OBS} observações.")

    print("\n" + "=" * 100)
    if sobreviventes:
        print(f"VEREDITO: {len(sobreviventes)} bucket(s) sobrevive(m) a Holm.")
        print("Antes de agir: confira se o efeito é MAIOR que o piso da linha. Um")
        print("achado logo acima do piso é o que uma amostra pequena produz por acaso.")
    else:
        print("VEREDITO: nenhum dia do mês sobrevive à correção de Holm.")
        print("Isso NÃO é 'não existe padrão' -- é 'esta amostra não consegue vê-lo'.")
        print()
        print("Quanta história seria preciso para enxergar 0,15%/dia com 80% de poder:")
        print("                                     virada do mês   um dia específico")
        print("  carteira concentrada (sigma 2,0%)      29 anos          116 anos")
        print("  setor via SMH        (sigma 1,6%)      19 anos           74 anos")
        print("  mercado via SPY      (sigma 1,1%)       9 anos           35 anos")
        print()
        print("O que domina não é a falta de anos: é a VOLATILIDADE da carteira.")
        print("Trocar 5 por 20 anos ajuda; trocar a carteira concentrada por um")
        print("índice ajuda três vezes mais, porque corta o ruído idiossincrático")
        print("que esconde o efeito. Um dia ESPECÍFICO do mês continua fora de")
        print("alcance em qualquer cenário -- só a janela agregada é testável.")
    print("=" * 100)


if __name__ == "__main__":
    main()
