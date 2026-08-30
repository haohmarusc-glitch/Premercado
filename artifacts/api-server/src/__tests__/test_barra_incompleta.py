"""
A barra do dia corrente não pode virar NaN no preço atual.

Produção 18/08/2026, dois painéis no mesmo dia, mesma raiz:

  TÉCNICA             price=NaN. Como NaN não é JSON válido, a resposta
                      INTEIRA morria no JSON.parse do Node -- 500 na tela,
                      levando junto o RSI, o MACD e o VWAP, que estavam certos.
  REAÇÃO A EARNINGS   current_price nulo, e com ele R1/R2/S1/S2, que derivam
                      do preço. A tela desenhou com "—" em todos os níveis.

A causa é a mesma: o yfinance inclui a barra do DIA CORRENTE antes de haver
fechamento. Ela chega com Close vazio e vira a ÚLTIMA linha, então todo
`close.iloc[-1]` pega NaN.

## Por que na fonte

Havia mais de vinte `Close.iloc[-1]` espalhados pelos scripts do agente, e
apenas dois filtravam (get_chart e get_technicals). Corrigir um a um é como se
chega ao terceiro incidente: o próximo script escrito não sabe da regra.

get_daily_history virou fachada e limpa QUALQUER caminho -- yfinance, cache,
cache vencido, fonte externa. Filtrar nos cinco `return` de dentro dependeria
de quem adicionar o sexto lembrar de repetir.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_barra_incompleta.py -v
"""
import numpy as np
import pandas as pd
import pytest

from agent import market_data_provider as mdp


def _serie(closes: list) -> pd.DataFrame:
    idx = pd.date_range("2026-08-10", periods=len(closes), freq="B")
    return pd.DataFrame(
        {"Open": 1.0, "High": 1.0, "Low": 1.0, "Close": closes, "Volume": 1_000},
        index=idx,
    )


# ── o filtro ────────────────────────────────────────────────────────────────

def test_descarta_a_barra_sem_fechamento():
    """O caso exato: última linha é o dia corrente, ainda sem Close."""
    out = mdp._sem_barra_incompleta(_serie([10.0, 11.0, 12.0, np.nan]))
    assert len(out) == 3
    assert out["Close"].iloc[-1] == 12.0   # e o iloc[-1] passa a ser um número


def test_serie_limpa_passa_intacta():
    df = _serie([10.0, 11.0, 12.0])
    assert len(mdp._sem_barra_incompleta(df)) == 3


def test_buraco_no_meio_tambem_sai():
    """Feriado mal preenchido ou falha da fonte no meio da janela: a linha sem
    fechamento não serve para média nem para desvio."""
    out = mdp._sem_barra_incompleta(_serie([10.0, np.nan, 12.0, 13.0]))
    assert len(out) == 3
    assert not out["Close"].isna().any()


def test_volume_faltando_NAO_descarta_o_pregao():
    """Só Close. Linha com Volume vazio ainda tem fechamento utilizável, e
    descartá-la jogaria fora pregão bom -- encurtando a janela de cálculo por
    um campo que ninguém usou."""
    df = _serie([10.0, 11.0, 12.0])
    df.loc[df.index[1], "Volume"] = np.nan
    assert len(mdp._sem_barra_incompleta(df)) == 3


def test_serie_toda_vazia_volta_como_veio():
    """Devolver df VAZIO aqui esconderia a diferença entre "a fonte não
    respondeu" e "respondeu só com barras incompletas". Decidir o que fazer
    sem dado é do chamador, que já trata ok=False e len<N."""
    df = _serie([np.nan, np.nan])
    assert len(mdp._sem_barra_incompleta(df)) == 2


def test_entradas_degeneradas_nao_explodem():
    """Filtro de higiene nunca pode derrubar a busca inteira."""
    assert mdp._sem_barra_incompleta(None) is None
    vazio = pd.DataFrame()
    assert mdp._sem_barra_incompleta(vazio) is vazio
    # df sem a coluna Close (formato inesperado de alguma fonte)
    sem_close = pd.DataFrame({"Preco": [1.0, 2.0]})
    assert len(mdp._sem_barra_incompleta(sem_close)) == 2


# ── a fachada ───────────────────────────────────────────────────────────────

def test_a_limpeza_vale_para_qualquer_caminho(monkeypatch):
    """yfinance, cache, cache vencido e fonte externa passam todos pela
    fachada. Este teste finge o caminho de sucesso; o que ele fixa é que a
    limpeza acontece DEPOIS, valendo para os cinco return de dentro."""
    sujo = _serie([10.0, 11.0, np.nan])
    monkeypatch.setattr(
        mdp, "_get_daily_history_bruto",
        lambda t, p="6mo", **k: mdp.HistoryResult(df=sujo, source="yfinance"),
    )

    res = mdp.get_daily_history("NVDA", "6mo")

    assert res.ok
    assert len(res.df) == 2
    assert res.df["Close"].iloc[-1] == 11.0
    assert res.source == "yfinance"          # metadados preservados


def test_a_fachada_preserva_is_stale_e_warnings(monkeypatch):
    """Perder is_stale aqui seria trocar um NaN por uma mentira silenciosa: a
    tela deixaria de saber que o dado é de cópia vencida."""
    monkeypatch.setattr(
        mdp, "_get_daily_history_bruto",
        lambda t, p="6mo", **k: mdp.HistoryResult(
            df=_serie([10.0, np.nan]), source="cache_stale",
            is_stale=True, warnings=["servindo cache VENCIDO"],
        ),
    )

    res = mdp.get_daily_history("NVDA", "6mo")

    assert res.is_stale is True
    assert res.warnings == ["servindo cache VENCIDO"]
    assert res.source == "cache_stale"


def test_resultado_sem_df_passa_sem_erro(monkeypatch):
    monkeypatch.setattr(
        mdp, "_get_daily_history_bruto",
        lambda t, p="6mo", **k: mdp.HistoryResult(df=None, source="none"),
    )
    res = mdp.get_daily_history("NVDA", "6mo")
    assert res.ok is False
    assert res.df is None


# ── quem chama o yfinance DIRETO ────────────────────────────────────────────
#
# A fachada do get_daily_history só protege quem passa por ela. Vários scripts
# chamam `yf.Ticker(...).history()` direto -- e foi por isso que o conserto na
# fonte não alcançou a Reação a Earnings: ela busca o próprio histórico.
#
# Estes testes fixam o caso concreto que quebrou. A varredura dos demais
# chamadores diretos é decisão à parte: ali a semântica difere por script
# (diário vs intradiário, janela de aquecimento), então não é mecânica como
# foi a do json_seguro.

def test_o_helper_e_publico():
    """O nome sem underscore é contrato: scripts fora do provider precisam
    poder aplicá-lo. Voltar a torná-lo privado quebraria esses chamadores."""
    assert hasattr(mdp, "sem_barra_incompleta")


def test_earnings_reaction_limpa_o_proprio_historico():
    """Ela chama t.history() direto, sem passar pelo provider. Produção
    18/08/2026, SNDK: "base: —" com R1/R2/S1/S2 vazios, enquanto gap,
    fechamento e volume vinham certos -- o preço era o único NaN, e os quatro
    níveis derivam dele."""
    import pathlib
    fonte = (pathlib.Path(mdp.__file__).parent / "earnings_reaction_analysis.py").read_text(encoding="utf-8")

    # Linhas de CÓDIGO, sem comentário. A primeira versão deste teste comparou
    # posições no texto cru e falhou achando `hist["Close"].iloc[-1]` dentro do
    # comentário que explica o conserto -- o comentário vem antes da linha que
    # ele descreve. Teste que lê fonte precisa saber a diferença.
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]

    def primeira(trecho: str) -> int:
        for i, linha in enumerate(codigo):
            if trecho in linha:
                return i
        return -1

    i_filtro = primeira("sem_barra_incompleta(hist)")
    i_preco = primeira('hist["Close"].iloc[-1]')

    assert i_filtro >= 0, "o filtro não é aplicado ao histórico deste script"
    assert i_preco >= 0, "não achei o uso do preço -- teste desatualizado?"
    # Filtrar DEPOIS de ler o preço não serviria de nada.
    assert i_filtro < i_preco


# ── a varredura dos chamadores diretos ──────────────────────────────────────
#
# Onze arquivos chamam yf.Ticker().history() ou yf.download() sem passar pelo
# provider. Revisados um a um, e o resultado NÃO foi "aplicar em todos" -- em
# dois deles o filtro INTRODUZIRIA erro. É o motivo de esta varredura ter sido
# feita à mão, e não como substituição mecânica.

def _fonte(nome: str) -> str:
    import pathlib
    return (pathlib.Path(mdp.__file__).parent / nome).read_text(encoding="utf-8")


def test_market_alerts_filtra_no_helper_central():
    """_hist() alimenta ~8 checagens que fazem `df["Close"].iloc[-1]` (fade,
    gap, momentum, rompimento). Filtrar no helper conserta as oito de uma vez,
    e é por isso que o cache guarda a série já limpa."""
    fonte = _fonte("market_alerts.py")
    assert "sem_barra_incompleta(df)" in fonte


def test_confluence_filtra_juros_e_petroleo():
    """Os dois vetos macro leem iloc[-1] de série crua de 5d/1mo. Com NaN, o
    veto sumia em silêncio -- o pior modo de falha para um freio."""
    fonte = _fonte("confluence_engine.py")
    assert fonte.count("sem_barra_incompleta(") >= 2


def test_agent_filtra_o_candle_diario():
    fonte = _fonte("llm_runtime.py")
    assert "sem_barra_incompleta(hist)" in fonte


# ── e as duas que NÃO podem ser filtradas ───────────────────────────────────

def test_get_performance_NAO_filtra_a_barra_de_hoje():
    """A decisão mais importante da varredura, e a contraintuitiva.

    get_performance lê `iloc[-2]` como "fechamento anterior" -- e isso só está
    certo PORQUE a barra de hoje é a última. Filtrando, iloc[-2] passaria a ser
    o fechamento de ANTEONTEM, e eu teria introduzido um erro de preço tentando
    consertar outro.

    Este teste existe para alguém que vier "completar a varredura" parar e ler
    antes de aplicar o filtro aqui."""
    fonte = _fonte("get_performance.py")
    assert "sem_barra_incompleta" not in fonte
    assert 'hist["Close"].iloc[-2]' in fonte   # o uso que depende da barra de hoje


def test_get_quotes_NAO_filtra_pelo_mesmo_motivo():
    fonte = _fonte("get_quotes.py")
    assert "sem_barra_incompleta" not in fonte
    assert 'hist["Close"].iloc[-2]' in fonte
