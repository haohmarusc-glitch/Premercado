"""
Padrões estatísticos e sensibilidade a fatores -- a versão HONESTA da
pergunta "esta ação tem padrão explorável?".

Procurar sazonalidade, efeito de dia da semana e comportamento em dias de
evento macro é fácil; o difícil é não se enganar. Testando 12 meses + 5 dias
da semana + N eventos, a 5% de significância UM em cada vinte testes dá
"significativo" por puro acaso -- e é exatamente assim que se produz uma
tabela bonita de padrões que não sobrevivem ao próximo ano.

Por isso cada padrão aqui vem com três coisas, sempre:

  n           -- o tamanho da amostra (padrão de 4 observações não é padrão);
  IC 95%      -- por bootstrap, semente FIXA (reprodutível é requisito de
                 auditoria: a mesma série tem que dar o mesmo intervalo);
  p-valor     -- teste de PERMUTAÇÃO contra o resto da série (não-paramétrico,
                 sem supor normalidade de retorno diário, que notoriamente
                 não é normal), e depois a correção de Holm-Bonferroni sobre
                 TODOS os testes da rodada.

O veredito final conta quantos padrões sobrevivem à correção. Quando a
resposta é "nenhum" -- que é o caso comum e esperado -- o relatório diz isso
com todas as letras, em vez de listar o mês menos ruim como se fosse um edge.

A sensibilidade a fatores (setor, juros, dólar, volatilidade) é outra coisa:
não é procura de padrão escondido, é medição de exposição. Beta e R² de
regressão simples, com o R² dizendo quanto do movimento do papel aquele
fator explica de fato. Juros e VIX entram como VARIAÇÃO do nível (regredir
retorno contra nível de taxa é espúrio), setor e dólar como retorno.

Todas as funções de conta são PURAS (série entra, dict sai) -- a suíte cobre
a aritmética com fixtures sintéticas, sem rede.

Rodar (na VPS, dentro do container):
    docker compose exec -T -w /app/artifacts/api-server/src app \
      /app/.venv/bin/python -m agent.padroes_estatisticos <<< '{"ticker":"NVDA"}'
"""
import sys

import numpy as np
import pandas as pd
import yfinance as yf

from agent import json_seguro
from agent.security import sanitize_ticker

# ── config ───────────────────────────────────────────────────────────────────

ANOS_PADRAO = 5
ALFA = 0.05
PERMUTACOES = 2000
BOOTSTRAP_AMOSTRAS = 2000
SEMENTE = 0
# Abaixo disso o "padrão" é anedota: não entra no teste, e o relatório diz
# por que ficou de fora em vez de omitir a linha em silêncio.
MIN_OBS = 8
# R² abaixo disso: o fator explica tão pouco do movimento que o beta, ainda
# que estatisticamente diferente de zero, não descreve o papel.
R2_RELEVANTE = 0.05

MESES_PT = ["jan", "fev", "mar", "abr", "mai", "jun",
            "jul", "ago", "set", "out", "nov", "dez"]
DIAS_PT = ["segunda", "terça", "quarta", "quinta", "sexta"]

# Fatores medidos. (rótulo, símbolo, "retorno" | "variacao_nivel")
FATORES = [
    ("Setor (SMH)", "SMH", "retorno"),
    ("Juros 10y (^TNX)", "^TNX", "variacao_nivel"),
    ("Dólar (UUP)", "UUP", "retorno"),
    ("Volatilidade (^VIX)", "^VIX", "variacao_nivel"),
    # Energia para data center (25/08/2026). A tese de IA/data center tem um
    # gargalo físico que virou preço: energia. As geradoras independentes
    # (VST) passaram a negociar como proxy do buildout, e -- diferente de um
    # ETF de semis -- NÃO são mecanicamente coladas aos papéis da carteira.
    # É isso que torna o R² aqui informação nova em vez de tautologia:
    # regredir NVDA contra SMH responde "semicondutor anda junto?" (sabemos
    # que sim, 0,82); contra VST responde "quanto deste papel é a tese de
    # data center e não semis genérico?".
    #
    # Limitação declarada: é um NOME, não um índice -- carrega notícia
    # idiossincrática (regulatória, de contrato) que não é a tese. Por isso
    # entra como fator medido com R² ao lado, nunca como sinal.
    ("Energia p/ data center (VST)", "VST", "retorno"),
]


# ── estatística ──────────────────────────────────────────────────────────────

def _ic_bootstrap(valores: np.ndarray, semente: int = SEMENTE) -> list | None:
    """IC 95% da MÉDIA por bootstrap. Semente fixa: dois operadores olhando o
    mesmo papel têm que ver o mesmo intervalo."""
    n = len(valores)
    if n < MIN_OBS:
        return None
    rng = np.random.default_rng(semente)
    medias = valores[rng.integers(0, n, size=(BOOTSTRAP_AMOSTRAS, n))].mean(axis=1)
    lo, hi = np.percentile(medias, [2.5, 97.5])
    return [round(float(lo), 3), round(float(hi), 3)]


def teste_permutacao(grupo: np.ndarray, resto: np.ndarray,
                     amostras: int = PERMUTACOES, semente: int = SEMENTE) -> float | None:
    """p-valor bicaudal de "a média do grupo difere da do resto".

    Permutação em vez de teste-t porque retorno diário tem cauda gorda e
    assimetria -- a aproximação normal do t superestima significância
    exatamente nos casos extremos, que são os que mais chamam atenção numa
    varredura de padrões."""
    if len(grupo) < MIN_OBS or len(resto) < MIN_OBS:
        return None
    observado = abs(float(grupo.mean() - resto.mean()))
    juntos = np.concatenate([grupo, resto])
    n_grupo = len(grupo)
    rng = np.random.default_rng(semente)
    extremos = 0
    for _ in range(amostras):
        rng.shuffle(juntos)
        diff = abs(float(juntos[:n_grupo].mean() - juntos[n_grupo:].mean()))
        if diff >= observado:
            extremos += 1
    # +1 no numerador e denominador: p-valor de permutação nunca é ZERO --
    # "não vi em 2000 sorteios" é diferente de "é impossível".
    return round((extremos + 1) / (amostras + 1), 4)


def holm(padroes: list, alfa: float = ALFA) -> list:
    """Marca `sobrevive` em cada padrão pela correção de Holm-Bonferroni.

    Sem correção, varrer 21 padrões a 5% produz ~1 "achado" por acaso em toda
    rodada -- e o achado tem sempre uma história convincente depois de
    encontrado. Holm é uniformemente mais poderoso que Bonferroni puro e
    igualmente conservador no controle do erro familiar."""
    testados = [p for p in padroes if p.get("p_valor") is not None]
    m = len(testados)
    for p in padroes:
        p["sobrevive"] = False
    if m == 0:
        return padroes
    ordenados = sorted(testados, key=lambda p: p["p_valor"])
    for i, p in enumerate(ordenados):
        limite = alfa / (m - i)
        p["limite_holm"] = round(limite, 5)
        if p["p_valor"] <= limite:
            p["sobrevive"] = True
        else:
            break  # Holm para no primeiro que falha; os seguintes caem junto
    return padroes


# ── padrões ──────────────────────────────────────────────────────────────────

def _linha(rotulo: str, grupo: np.ndarray, resto: np.ndarray) -> dict:
    linha = {
        "rotulo": rotulo,
        "n": int(len(grupo)),
        "retorno_medio_pct": round(float(grupo.mean()) * 100, 3) if len(grupo) else None,
        "positivos_pct": round(float((grupo > 0).mean()) * 100, 1) if len(grupo) else None,
        "ic95_pct": None,
        "p_valor": None,
    }
    ic = _ic_bootstrap(grupo)
    if ic:
        linha["ic95_pct"] = [round(ic[0] * 100, 3), round(ic[1] * 100, 3)]
    if len(grupo) < MIN_OBS:
        linha["nota"] = f"amostra de {len(grupo)} pregões — abaixo do mínimo de {MIN_OBS} para testar"
        return linha
    linha["p_valor"] = teste_permutacao(grupo, resto)
    return linha


def analisar_sazonalidade(ret: pd.Series) -> list:
    """Retorno médio por mês do calendário vs todos os outros meses."""
    valores = ret.to_numpy(dtype=float)
    meses = ret.index.month.to_numpy()
    saida = []
    for m in range(1, 13):
        mask = meses == m
        saida.append(_linha(MESES_PT[m - 1], valores[mask], valores[~mask]))
    return saida


def analisar_dia_semana(ret: pd.Series) -> list:
    valores = ret.to_numpy(dtype=float)
    dias = ret.index.dayofweek.to_numpy()
    saida = []
    for d in range(5):
        mask = dias == d
        saida.append(_linha(DIAS_PT[d], valores[mask], valores[~mask]))
    return saida


def analisar_eventos_macro(ret: pd.Series, eventos: dict) -> list:
    """Retorno nos dias de FOMC/CPI/PCE etc. vs os demais pregões.

    `eventos` é {tipo: [datas]} -- o MESMO calendário que market_alerts usa
    para os alertas macro (importado tarde no main, não duplicado aqui: uma
    lista de datas divergente entre dois módulos é a armadilha nº 1 do
    playbook do repo)."""
    valores = ret.to_numpy(dtype=float)
    idx = ret.index.normalize()
    saida = []
    for tipo, datas in sorted(eventos.items()):
        alvo = pd.to_datetime(pd.Series(list(datas))).dt.normalize()
        mask = idx.isin(alvo).to_numpy() if hasattr(idx.isin(alvo), "to_numpy") else np.asarray(idx.isin(alvo))
        saida.append(_linha(f"dias de {tipo}", valores[mask], valores[~mask]))
    return saida


# ── sensibilidade a fatores ──────────────────────────────────────────────────

def beta_e_r2(y: np.ndarray, x: np.ndarray) -> dict:
    """Regressão simples y = a + b*x. Devolve beta, R² e n.

    R² é o número que impede a leitura errada do beta: um beta de 1,4 com R²
    de 0,02 significa "quando esse fator se move, o papel FAZ O QUE QUISER" --
    a inclinação existe, a explicação não."""
    n = len(y)
    if n < MIN_OBS or len(x) != n:
        return {"beta": None, "r2": None, "n": int(n)}
    var_x = float(np.var(x, ddof=1))
    if var_x == 0:
        return {"beta": None, "r2": None, "n": int(n)}
    beta = float(np.cov(y, x, ddof=1)[0, 1]) / var_x
    corr = float(np.corrcoef(y, x)[0, 1]) if float(np.var(y, ddof=1)) > 0 else 0.0
    return {"beta": round(beta, 3), "r2": round(corr ** 2, 3), "n": int(n)}


def sensibilidade_a_fatores(ret: pd.Series, series_fatores: dict) -> list:
    """series_fatores: {rotulo: (serie_alinhavel, modo)} -- modo "retorno"
    (pct_change) ou "variacao_nivel" (diff, para taxa e VIX: regredir retorno
    contra NÍVEL de taxa é espúrio)."""
    saida = []
    for rotulo, (serie, modo) in series_fatores.items():
        s = serie.reindex(ret.index)
        x = (s.pct_change() if modo == "retorno" else s.diff())
        par = pd.concat([ret, x], axis=1).dropna()
        if par.empty:
            saida.append({"fator": rotulo, "modo": modo, "beta": None, "r2": None, "n": 0,
                          "nota": "sem sobreposição de pregões com o ticker"})
            continue
        r = beta_e_r2(par.iloc[:, 0].to_numpy(dtype=float), par.iloc[:, 1].to_numpy(dtype=float))
        r.update({"fator": rotulo, "modo": modo})
        r["relevante"] = bool(r["r2"] is not None and r["r2"] >= R2_RELEVANTE)
        saida.append(r)
    return saida


# ── relatório ────────────────────────────────────────────────────────────────

def montar_relatorio(ticker: str, ret: pd.Series, eventos: dict,
                     series_fatores: dict) -> dict:
    sazonal = analisar_sazonalidade(ret)
    semana = analisar_dia_semana(ret)
    macro = analisar_eventos_macro(ret, eventos) if eventos else []

    # A correção roda sobre TODOS os padrões da rodada de uma vez -- corrigir
    # por bloco (12 meses de um lado, 5 dias de outro) seria escolher o
    # denominador depois de ver os dados.
    todos = sazonal + semana + macro
    holm(todos)

    testados = [p for p in todos if p.get("p_valor") is not None]
    sobreviventes = [p for p in todos if p.get("sobrevive")]
    fatores = sensibilidade_a_fatores(ret, series_fatores)

    if not testados:
        veredito = ("Nenhum padrão tinha amostra suficiente para teste no período "
                    "pedido — sem conclusão, e sem tabela sugerindo o contrário.")
    elif not sobreviventes:
        veredito = (f"Nenhum dos {len(testados)} padrões testados sobrevive à correção "
                    f"de múltiplos testes (Holm, α={ALFA}). É o resultado esperado na "
                    f"maioria dos papéis: o mês/dia 'melhor' do histórico é ruído com "
                    f"história convincente. Operar qualquer linha desta tabela é "
                    f"apostar em acaso já medido como acaso.")
    else:
        nomes = ", ".join(p["rotulo"] for p in sobreviventes)
        veredito = (f"{len(sobreviventes)} de {len(testados)} padrões sobrevivem à correção "
                    f"de Holm: {nomes}. Sobreviver não é edge operável — é motivo para "
                    f"investigar fora da amostra (o walk-forward do Backtest é o lugar).")

    relevantes = [f for f in fatores if f.get("relevante")]
    if relevantes:
        top = max(relevantes, key=lambda f: f["r2"])
        leitura_fatores = (f"O fator que mais explica o papel é {top['fator']}: beta "
                           f"{top['beta']}, R² {top['r2']} — {round(top['r2'] * 100)}% da "
                           f"variação diária. Os demais explicam menos de "
                           f"{round(R2_RELEVANTE * 100)}% e não descrevem o movimento.")
    else:
        leitura_fatores = (f"Nenhum fator medido explica ao menos {round(R2_RELEVANTE * 100)}% "
                           f"da variação diária deste papel no período — o movimento é "
                           f"idiossincrático (ou o período é curto demais).")

    return {
        "ticker": ticker,
        "inicio": str(ret.index[0])[:10],
        "fim": str(ret.index[-1])[:10],
        "pregoes": int(len(ret)),
        "alfa": ALFA,
        "permutacoes": PERMUTACOES,
        "sazonalidade": sazonal,
        "diaDaSemana": semana,
        "eventosMacro": macro,
        "fatores": fatores,
        "testados": len(testados),
        "sobreviventes": len(sobreviventes),
        "veredito": veredito,
        "leituraFatores": leitura_fatores,
    }


# ── coleta (rede) ────────────────────────────────────────────────────────────

def _historico(simbolo: str, anos: int) -> pd.Series | None:
    df = yf.Ticker(simbolo).history(period=f"{anos}y", interval="1d", auto_adjust=True)
    if df is None or df.empty:
        return None
    if hasattr(df.columns, "levels"):
        df.columns = df.columns.get_level_values(0)
    s = df["Close"].dropna()
    if s.index.tz is not None:
        s.index = s.index.tz_localize(None)
    return s if len(s) > 0 else None


def analisar(ticker: str, anos: int = ANOS_PADRAO) -> dict:
    ticker = sanitize_ticker(ticker)
    preco = _historico(ticker, anos)
    if preco is None or len(preco) < 60:
        return {"error": f"Histórico insuficiente para {ticker} no período pedido"}
    ret = preco.pct_change().dropna()

    # Import tardio do calendário macro: a lista de datas vive em
    # market_alerts (fonte única) e importar no topo traria o módulo inteiro
    # -- com yfinance e rede -- para dentro dos testes de aritmética.
    try:
        try:
            from market_alerts import MACRO_EVENTS
        except ImportError:
            from agent.market_alerts import MACRO_EVENTS
        eventos = dict(MACRO_EVENTS)
    except Exception as e:
        print(f"[padroes] calendário macro indisponível: {e}", file=sys.stderr)
        eventos = {}

    series_fatores = {}
    for rotulo, simbolo, modo in FATORES:
        try:
            s = _historico(simbolo, anos)
            if s is not None:
                series_fatores[rotulo] = (s, modo)
        except Exception as e:
            print(f"[padroes] fator {simbolo} indisponível: {e}", file=sys.stderr)

    return montar_relatorio(ticker, ret, eventos, series_fatores)


if __name__ == "__main__":
    import json as _json
    args = _json.loads(sys.stdin.read() or "{}")
    print(json_seguro.dumps(analisar(args.get("ticker", "NVDA"),
                                     int(args.get("anos") or ANOS_PADRAO))))
