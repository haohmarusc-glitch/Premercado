"""
A forma 3: "capex acelerando" muda o comportamento do papel? -- MEDIDO, com
critério declarado ANTES de olhar o resultado.

O pedido original era transformar a tese de IA/data center em fator de
decisão (peso, multiplicador de sizing). Isso, feito direto, é exatamente o
RegimeStage arquivado em 20/08/2026: modulador sobre uma base sem edge. Mas
a pergunta por trás é legítima e testável -- e agora existe régua para
testá-la: execução honesta (D+1, stops por OHLC), auditor independente,
bootstrap e correção de múltiplos testes.

Então a forma 3 vira ISTO: um experimento que mede se o regime de capex
separa os retornos, com o critério de aprovação FIXADO no código antes de
qualquer rodada. Se passar, vira proposta de mudança em produção. Se não
passar -- o desfecho que eu espero --, fica registrado que não passou, e a
tese continua valendo como CONTEXTO (que é como ela já entrou no Veredito),
não como gatilho.

Duas decisões metodológicas que definem a honestidade do teste:

1. O regime usa a data de DIVULGAÇÃO, não o fim do trimestre. O capex do
   trimestre encerrado em 30/06 só existe para o mercado semanas depois;
   condicionar a partir de 01/07 é look-ahead -- o mesmo vício que o
   backtest carregou até 20/08 e que o auditor independente hoje vigia.

2. O tamanho da amostra é declarado antes e provavelmente reprova sozinho:
   capex dá ~4 pontos por ano, e três anos de história são ~12 trimestres,
   dos quais talvez 7 ou 8 sejam "acelerando". Nenhum teste sério separa
   médias com isso. O script CALCULA e MOSTRA o poder da amostra em vez de
   produzir um p-valor bonito sobre nada.

O capex vem do OVERLAY que o coletor semanal grava, não de coleta própria.
Na primeira semana este script chamava `montar()` direto e gastava as cinco
chamadas de Alpha Vantage a cada rodada -- num orçamento de 15/dia dividido
com earnings e notícias, rodar o experimento duas vezes esgotava a cota e
fazia a COLETA seguinte vir rasa. Um experimento não pode degradar o dado que
ele mede. Com `--coletar` a coleta é forçada, para quando for essa a intenção.

Rodar (na VPS, dentro do container -- lê o overlay, não usa rede):
    docker compose exec -T -w /app/artifacts/api-server/src/agent/scripts app \
      /app/.venv/bin/python3 capex_regime_teste.py < /dev/null
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import padroes_estatisticos as pe  # noqa: E402
import capex_hyperscalers as cap  # noqa: E402

# ── CRITÉRIO DE APROVAÇÃO, DECLARADO ANTES DE QUALQUER RODADA ────────────────
#
# Mesmo protocolo do arquivamento de 20/08: o critério é escrito antes de ver
# o resultado, senão a régua se ajusta ao que se quer concluir.
#
# Para "capex acelerando" virar fator de DECISÃO em produção, é preciso, TUDO
# ao mesmo tempo:
MIN_TRIMESTRES_POR_REGIME = 6   # menos que isso não é amostra, é anedota
MIN_PREGOES_POR_REGIME = 120    # ~6 meses de pregões em cada lado
ALFA = 0.05                     # p-valor de permutação, bicaudal
MIN_DIFERENCA_DIARIA_PP = 0.05  # 0,05pp/dia ~ 12pp/ano: abaixo disso, mesmo
                                # significativo, não paga custo de execução

TICKERS = ["NVDA", "MU", "AVGO", "MRVL", "SMCI"]
ANOS = 5


def serie_de_regime(trimestres: list, indice: pd.DatetimeIndex) -> pd.Series:
    """Regime diário a partir dos trimestres, ancorado em `disponivelEm`.

    Cada pregão recebe o regime do último trimestre JÁ DIVULGADO naquele dia.
    Pregão anterior à primeira divulgação fica sem regime (NaN) em vez de
    herdar o primeiro -- inventar passado é o começo do look-ahead."""
    marcos = []
    for t in trimestres:
        disp, var = t.get("disponivelEm"), t.get("variacaoQoQPct")
        if not disp or var is None or not t.get("completo"):
            continue
        marcos.append((pd.Timestamp(disp), "acelerando" if var > 3
                       else "desacelerando" if var < -3 else "estável"))
    marcos.sort()
    if not marcos:
        return pd.Series(index=indice, dtype=object)
    regime = pd.Series(index=indice, dtype=object)
    for data, r in marcos:
        regime.loc[indice >= data] = r
    return regime


def medir(ret: pd.Series, regime: pd.Series) -> dict:
    """Compara retorno diário em 'acelerando' contra o resto, com o mesmo
    teste de permutação da análise 9 e o IC por bootstrap."""
    alinhado = pd.concat([ret, regime.rename("regime")], axis=1).dropna()
    if alinhado.empty:
        return {"erro": "sem sobreposição entre preço e regime"}
    dentro = alinhado[alinhado["regime"] == "acelerando"].iloc[:, 0].to_numpy(float)
    fora = alinhado[alinhado["regime"] != "acelerando"].iloc[:, 0].to_numpy(float)
    if len(dentro) < 2 or len(fora) < 2:
        return {"erro": f"um dos lados ficou vazio (dentro={len(dentro)}, fora={len(fora)})"}
    dif_pp = (float(dentro.mean()) - float(fora.mean())) * 100
    return {
        "pregoesAcelerando": len(dentro),
        "pregoesResto": len(fora),
        "mediaAcelerandoPct": round(float(dentro.mean()) * 100, 4),
        "mediaRestoPct": round(float(fora.mean()) * 100, 4),
        "diferencaPP": round(dif_pp, 4),
        "ic95Acelerando": pe._ic_bootstrap(dentro),
        "pValor": pe.teste_permutacao(dentro, fora),
    }


def avaliar(resultado: dict, n_trimestres_por_regime: dict) -> tuple:
    """(passou, motivos) contra o critério declarado no topo."""
    motivos = []
    n_acel = n_trimestres_por_regime.get("acelerando", 0)
    n_outro = sum(v for k, v in n_trimestres_por_regime.items() if k != "acelerando")
    if n_acel < MIN_TRIMESTRES_POR_REGIME or n_outro < MIN_TRIMESTRES_POR_REGIME:
        motivos.append(f"amostra de trimestres insuficiente (acelerando={n_acel}, "
                       f"resto={n_outro}; mínimo {MIN_TRIMESTRES_POR_REGIME} de cada)")
    if resultado.get("erro"):
        motivos.append(resultado["erro"])
        return False, motivos
    if (resultado["pregoesAcelerando"] < MIN_PREGOES_POR_REGIME
            or resultado["pregoesResto"] < MIN_PREGOES_POR_REGIME):
        motivos.append(f"pregões insuficientes em um dos lados "
                       f"({resultado['pregoesAcelerando']} x {resultado['pregoesResto']}; "
                       f"mínimo {MIN_PREGOES_POR_REGIME})")
    p = resultado.get("pValor")
    if p is None or p > ALFA:
        motivos.append(f"p-valor {p} não passa de {ALFA}")
    if abs(resultado.get("diferencaPP") or 0) < MIN_DIFERENCA_DIARIA_PP:
        motivos.append(f"diferença de {resultado.get('diferencaPP')}pp/dia abaixo do "
                       f"mínimo operacional de {MIN_DIFERENCA_DIARIA_PP}pp")
    return (not motivos), motivos


def carregar_capex(coletar: bool = False, *, overlay=cap.ler_overlay,
                   montar=cap.montar) -> dict | None:
    """Overlay por padrão; rede só sob pedido explícito.

    Devolve None quando não há overlay -- em vez de coletar por conta
    própria, o script diz qual comando gera o dado. Cair na rede
    silenciosamente é como a cota foi parar em zero na primeira semana."""
    if coletar:
        return montar()
    dados = overlay()
    if dados and dados.get("trimestres"):
        return dados
    return None


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    print("CRITÉRIO (declarado antes da rodada): "
          f"{MIN_TRIMESTRES_POR_REGIME}+ trimestres e {MIN_PREGOES_POR_REGIME}+ pregões "
          f"de cada lado, p<={ALFA}, diferença >= {MIN_DIFERENCA_DIARIA_PP}pp/dia\n")

    forcar_coleta = "--coletar" in argv
    capex = carregar_capex(forcar_coleta)
    if capex is None:
        print("sem overlay de capex em "
              f"{cap.OVERLAY_PATH_DEFAULT} -- rode o coletor primeiro:\n"
              "  docker compose exec -T -w /app/artifacts/api-server/src app \\\n"
              "    /app/.venv/bin/python -m agent.capex_hyperscalers < /dev/null\n"
              "(ou passe --coletar para coletar aqui, gastando cota da Alpha Vantage)",
              file=sys.stderr)
        return 2
    print(f"capex: {'coletado agora' if forcar_coleta else 'lido do overlay'}"
          f" (coletadoEm={capex.get('coletadoEm')})")
    trimestres = capex.get("trimestres", [])
    completos = [t for t in trimestres if t.get("completo") and t.get("variacaoQoQPct") is not None]
    contagem: dict = {}
    for t in completos:
        r = ("acelerando" if t["variacaoQoQPct"] > 3
             else "desacelerando" if t["variacaoQoQPct"] < -3 else "estável")
        contagem[r] = contagem.get(r, 0) + 1
    print(f"Trimestres completos com variação: {len(completos)} -> {contagem}")
    # Com a janela ampliada, a contagem sozinha esconde o que importa: se o
    # lado de contraste é um bloco contíguo de um ciclo antigo, a comparação
    # mede aquele período, não o regime. Os trimestres de cada lado, à vista.
    for r in sorted(contagem):
        quais = [t["trimestre"] for t in completos
                 if ("acelerando" if t["variacaoQoQPct"] > 3
                     else "desacelerando" if t["variacaoQoQPct"] < -3 else "estável") == r]
        print(f"  {r}: {', '.join(quais)}")
    if capex.get("falhas"):
        print(f"AVISO: sem capex para {', '.join(capex['falhas'])} -- o agregado está incompleto",
              file=sys.stderr)

    reprovados, aprovados = [], []
    for tk in TICKERS:
        preco = pe._historico(tk, ANOS)
        if preco is None or len(preco) < 60:
            print(f"{tk}: histórico insuficiente"); continue
        ret = preco.pct_change().dropna()
        res = medir(ret, serie_de_regime(trimestres, ret.index))
        passou, motivos = avaliar(res, contagem)
        (aprovados if passou else reprovados).append(tk)
        print(f"\n{tk}: {res}")
        print(f"  -> {'PASSA' if passou else 'NÃO passa'} no critério"
              + ("" if passou else ": " + "; ".join(motivos)))

    print("\n" + "=" * 70)
    if aprovados:
        print(f"PASSARAM: {', '.join(aprovados)}. Isso NÃO liga nada em produção -- é "
              f"o gatilho para levar ao walk-forward com embargo (Backtest) antes de "
              f"qualquer mudança de decisão.")
    else:
        print("NENHUM ticker passa no critério declarado. A tese de IA/data center "
              "segue valendo como CONTEXTO medido (capex no snapshot do Veredito), "
              "não como gatilho de operação -- que é exatamente onde ela já está.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
