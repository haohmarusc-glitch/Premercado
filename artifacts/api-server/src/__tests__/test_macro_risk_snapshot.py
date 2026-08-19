"""
A coleta não pode transformar falha em zero.

O macro_risk.py distingue "medi e está calmo" de "não consegui medir", e essa
distinção só sobrevive se a camada que busca os dados respeitá-la. Um `except`
que devolvesse 0.0 aqui reintroduziria o bug na camada de baixo: cegueira
chegando ao Kelly como se fosse leitura de mercado calmo.

Estes testes rodam SEM rede -- cada fonte é dublada. O que eles fixam é o
contrato entre coleta e módulo, não o valor de nenhum indicador.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_macro_risk_snapshot.py -v
"""
import pytest

from agent import macro_risk as mr
from agent import macro_risk_snapshot as snap


# ── nenhum teste daqui toca a rede ──────────────────────────────────────────
#
# Fixture AUTOUSE, e por um motivo aprendido do jeito ruim. Em 18/08/2026 a
# fonte nova do WTI entrou sem dublê. Este ambiente não alcança o yfinance,
# então ela falhava e caía no FRED dublado -- os testes passavam. O CI TEM
# rede: lá ela funcionou de verdade, o FRED nunca foi tentado, e
# `test_uma_fonte_fora_nao_leva_as_outras` quebrou.
#
# O bloqueio é nas PRIMITIVAS de rede, não nos nomes das funções deste módulo.
# Duas razões: os testes abaixo exercitam os fetchers de verdade (com dublê de
# camada mais baixa), e uma lista de nomes de função envelhece -- quem
# adicionar a sétima fonte não teria como saber que precisa vir aqui. Bloquear
# a primitiva pega qualquer fonte futura sem manutenção.

REDE_BLOQUEADA = "rede bloqueada nos testes -- duble a camada que este teste exercita"


@pytest.fixture(autouse=True)
def sem_rede(monkeypatch):
    try:
        from agent import market_alerts, market_data_provider, tools
    except ImportError:  # pragma: no cover
        import market_alerts, market_data_provider, tools  # type: ignore
    import yfinance as yf

    def bloqueia(quem):
        def _b(*_a, **_k):
            raise RuntimeError(f"{quem}: {REDE_BLOQUEADA}")
        return _b

    monkeypatch.setattr(snap.SESSION, "get", bloqueia("SESSION.get"))
    monkeypatch.setattr(market_data_provider, "get_daily_history", bloqueia("get_daily_history"))
    monkeypatch.setattr(market_alerts, "get_global_market_snapshot", bloqueia("snapshot global"))
    monkeypatch.setattr(tools, "get_geopolitical_news", bloqueia("get_geopolitical_news"))
    monkeypatch.setattr(yf, "Ticker", bloqueia("yf.Ticker"))


# ── uma fonte fora não derruba as outras ────────────────────────────────────

def _sem_nada(monkeypatch):
    """Todas as fontes fora. Com o bloqueio por primitiva isto já é o estado
    padrão -- só o FOMC (que lê MACRO_EVENTS, não a rede) precisa ser fixado."""
    monkeypatch.setattr(snap, "_perto_do_fomc", lambda *_a, **_k: False)


def test_tudo_fora_nao_levanta_e_nao_vira_zero(monkeypatch):
    """O caso que importa: coleta totalmente cega tem que produzir um retrato
    HONESTO, não um retrato de mercado calmo."""
    _sem_nada(monkeypatch)
    dados, diag = snap.coletar()

    assert dados["sk_hynix"] is None
    assert dados["kospi"] is None
    assert dados["sox"] is None
    assert dados["manchetes"] is None
    assert "yield_30y_hoje" not in dados        # ausente, não 0.0
    assert len(diag["erros"]) >= 5


def test_o_retrato_cego_nao_tem_score(monkeypatch):
    _sem_nada(monkeypatch)
    saida = snap.montar()

    assert saida["aggregate_score"] is None
    assert saida["cobertura_pct"] < mr.COBERTURA_MINIMA_PCT
    assert saida["fontesDegradadas"]


def test_uma_fonte_fora_nao_leva_as_outras(monkeypatch):
    """O FRED cai e o resto continua. Sem o isolamento por bloco, uma exceção
    no primeiro fetch abortaria a coleta inteira e o dia inteiro viraria cego
    por causa de uma fonte."""
    def explode(*_a, **_k):
        raise RuntimeError("FRED fora")
    monkeypatch.setattr(snap, "_fred_duas_ultimas", explode)
    monkeypatch.setattr(snap, "_dois_ultimos_fechamentos", explode)
    monkeypatch.setattr(snap, "_variacao_do_dia", lambda t: -14.65)
    monkeypatch.setattr(snap, "_kospi_do_snapshot_global", lambda: (-8.0, ""))
    monkeypatch.setattr(snap, "_serie_sox", lambda **_k: ([100.0] * 46, ""))
    monkeypatch.setattr(snap, "_manchetes_china", lambda: ([], ""))
    monkeypatch.setattr(snap, "_earnings_da_carteira", lambda: (None, None, ""))
    monkeypatch.setattr(snap, "_perto_do_fomc", lambda *_a, **_k: False)

    saida = snap.montar()

    assert saida["ASIA_MEMORY_CONTAGION"]["active"] is True
    assert saida["OVEREXTENDED_SECTOR"]["status"] == mr.OK
    assert "RATE_SHOCK" in saida["fontesDegradadas"]
    assert set(saida["coleta"]["erros"]) >= {"DGS30", "DGS10", "DCOILWTICO"}


# ── o Kospi suspeito ────────────────────────────────────────────────────────

def test_kospi_suspeito_nao_e_usado(monkeypatch):
    """`suspect` no snapshot global significa que o número pode ser comparação
    atravessando sessões. Usá-lo como se fosse variação de um dia é o erro que
    aquele rótulo existe para evitar."""
    monkeypatch.setattr(
        __import__("agent.market_alerts", fromlist=["x"]),
        "get_global_market_snapshot",
        lambda: {"items": [{"ticker": "^KS11", "changePct": -31.0,
                            "suspect": True, "suspectReason": "barras a 9 dias"}]},
    )
    pct, motivo = snap._kospi_do_snapshot_global()
    assert pct is None
    assert "9 dias" in motivo


def test_kospi_limpo_passa(monkeypatch):
    monkeypatch.setattr(
        __import__("agent.market_alerts", fromlist=["x"]),
        "get_global_market_snapshot",
        lambda: {"items": [{"ticker": "^KS11", "changePct": -8.0, "suspect": False}]},
    )
    pct, motivo = snap._kospi_do_snapshot_global()
    assert pct == -8.0
    assert motivo == ""


def test_perder_o_kospi_nao_apaga_o_sinal_da_asia(monkeypatch):
    """Por isso o sinal não depende só do índice: SK Hynix e Samsung vêm de
    outra fonte, e o check dispara por ação OU índice. Relevante porque o limite
    de implausibilidade do snapshot é 8,0% e o Kospi fechou a -8,0% em
    28/07/2026 -- uma queda real um pouco maior chegaria rotulada."""
    from datetime import date as _d
    monkeypatch.setattr(snap, "_fred_duas_ultimas",
                        lambda s: (0.0, 0.0, [_d.today().isoformat(), ""]))
    monkeypatch.setattr(snap, "_dois_ultimos_fechamentos",
                        lambda t: (_ for _ in ()).throw(RuntimeError("fora")))
    monkeypatch.setattr(snap, "_variacao_do_dia", lambda t: -14.65)
    monkeypatch.setattr(snap, "_kospi_do_snapshot_global", lambda: (None, "suspeito"))
    monkeypatch.setattr(snap, "_serie_sox", lambda **_k: ([100.0] * 46, ""))
    monkeypatch.setattr(snap, "_manchetes_china", lambda: ([], ""))
    monkeypatch.setattr(snap, "_earnings_da_carteira", lambda: (None, None, ""))

    saida = snap.montar()
    assert saida["ASIA_MEMORY_CONTAGION"]["active"] is True


# ── FRED ────────────────────────────────────────────────────────────────────

class _Resposta:
    def __init__(self, obs): self._obs = obs
    def raise_for_status(self): pass
    def json(self): return {"observations": self._obs}


def test_fred_pula_os_pontos_vazios(monkeypatch):
    """A série vem com '.' em feriado. Pedir limit=2 devolveria dois pontos
    vazios numa emenda de feriado, e o delta sairia de datas erradas."""
    monkeypatch.setenv("FRED_API_KEY", "x")
    monkeypatch.setattr(snap.SESSION, "get", lambda *a, **k: _Resposta([
        {"date": "2026-08-18", "value": "."},
        {"date": "2026-08-17", "value": "5.31"},
        {"date": "2026-08-14", "value": "5.19"},
    ]))
    hoje, ant, datas = snap._fred_duas_ultimas("DGS30")
    assert (hoje, ant) == (5.31, 5.19)
    assert datas == ["2026-08-17", "2026-08-14"]


def test_fred_sem_chave_levanta(monkeypatch):
    """Levantar, não devolver 0.0: quem chama transforma isso em sem_dado."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FRED_API_KEY"):
        snap._fred_duas_ultimas("DGS30")


def test_fred_com_uma_observacao_so_levanta(monkeypatch):
    """Um ponto não faz variação. Devolver o mesmo valor duas vezes daria
    delta zero -- 'sem choque de juros' construído sobre dado faltando."""
    monkeypatch.setenv("FRED_API_KEY", "x")
    monkeypatch.setattr(snap.SESSION, "get", lambda *a, **k: _Resposta([
        {"date": "2026-08-18", "value": "5.31"},
    ]))
    with pytest.raises(RuntimeError, match="2 observações"):
        snap._fred_duas_ultimas("DGS30")


# ── FOMC ────────────────────────────────────────────────────────────────────

def test_fomc_usa_o_calendario_que_ja_existe():
    """Segunda lista de datas do Fed seria fonte divergente (playbook §10)."""
    from datetime import date
    assert snap._perto_do_fomc(date(2026, 7, 29)) is True    # data oficial
    assert snap._perto_do_fomc(date(2026, 7, 28)) is True    # véspera
    assert snap._perto_do_fomc(date(2026, 7, 20)) is False


# ── contrato de saída ───────────────────────────────────────────────────────

def test_o_stdout_e_so_json(monkeypatch, capsys):
    """O Node faz JSON.parse do stdout. Diagnóstico vazando para lá derruba a
    resposta inteira -- mesma regra dos outros scripts servidos por rota."""
    _sem_nada(monkeypatch)
    import json
    saida = snap.montar()
    from agent import json_seguro
    texto = json_seguro.dumps(saida)
    assert json.loads(texto)["cobertura_pct"] is not None
    # o _log da montagem foi para stderr, não para stdout
    assert capsys.readouterr().out == ""


def test_serializa_por_json_seguro():
    """NaN vindo de uma divisão em qualquer fetch derrubaria a rota inteira."""
    import pathlib
    fonte = pathlib.Path(snap.__file__).read_text(encoding="utf-8")
    assert "json_seguro.dumps" in fonte
    assert "json.dumps(" not in fonte


# ── busca do balanço recente ────────────────────────────────────────────────

import pandas as pd


def _df_earnings(dias_atras: int, surpresa, coluna="Surprise(%)"):
    from datetime import date, timedelta
    quando = pd.Timestamp(date.today() - timedelta(days=dias_atras))
    return pd.DataFrame({coluna: [surpresa]}, index=[quando])


class _TickerFalso:
    def __init__(self, df): self.earnings_dates = df


def test_balanco_na_janela_devolve_a_surpresa(monkeypatch):
    monkeypatch.undo()          # este teste exercita a função real, com yf dublado
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", lambda t: _TickerFalso(_df_earnings(1, 5.0)))
    valor, motivo = snap._surpresa_recente("NVDA")
    assert valor == 5.0
    assert motivo == ""


def test_balanco_velho_e_ignorado_sem_virar_erro(monkeypatch):
    """Fora da janela não é falha de coleta -- é ausência de evento. Reportar
    como erro encheria `coleta.erros` todo dia com ruído."""
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", lambda t: _TickerFalso(_df_earnings(30, 5.0)))
    valor, motivo = snap._surpresa_recente("NVDA")
    assert valor is None
    assert motivo == ""


def test_balanco_agendado_ainda_sem_numero(monkeypatch):
    """earnings_dates traz datas FUTURAS com surpresa NaN. Tratar NaN como 0
    faria um balanço ainda não divulgado virar "não bateu"."""
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", lambda t: _TickerFalso(_df_earnings(0, float("nan"))))
    valor, motivo = snap._surpresa_recente("NVDA")
    assert valor is None
    assert "sem número reportado" in motivo


def test_coluna_renomeada_diz_o_que_achou(monkeypatch):
    """O yfinance renomeia colunas entre versões. Um KeyError daria um motivo
    inútil; nomear as colunas encontradas manda o conserto direto."""
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", lambda t: _TickerFalso(
        _df_earnings(1, 5.0, coluna="Outra Coisa")))
    valor, motivo = snap._surpresa_recente("NVDA")
    assert valor is None
    assert "Outra Coisa" in motivo


def test_o_primeiro_ticker_com_balanco_vence(monkeypatch):
    """Um evento por vez de propósito: o sinal descreve "fulano bateu e caiu", e
    misturar dois balanços numa média produziria um número que não aconteceu
    com ninguém."""
    chamados = []

    def falso(t, hoje=None):
        chamados.append(t)
        return (7.0, "") if t == "MRVL" else (None, "")

    monkeypatch.setattr(snap, "_surpresa_recente", falso)
    monkeypatch.setattr(snap, "_variacao_do_dia", lambda t: -9.0)
    try:
        from agent import config
    except ImportError:
        import config  # type: ignore
    monkeypatch.setattr(config, "TICKERS", ["NVDA", "MRVL", "ARM"], raising=False)

    eps, reacao, _ = snap._earnings_da_carteira()
    assert (eps, reacao) == (7.0, -9.0)
    assert "ARM" not in chamados          # parou no MRVL


# ── frescura do dado ────────────────────────────────────────────────────────
#
# Produção 18/08/2026, primeira coleta completa:
#
#     "datasFred": {"yield_30y": ["2026-08-17", "2026-08-14"],
#                   "wti":       ["2026-08-11", "2026-08-10"]}
#
# Os yields vieram de ontem. O WTI, de SETE dias antes -- o sinal de choque
# geopolítico comparava 11 contra 10 de agosto e apresentava isso como o
# movimento do dia. Um salto do petróleo hoje só apareceria daqui a uma semana.

from datetime import date, timedelta


def _resp_fred(dias_atras: int, hoje=5.31, ant=5.25):
    d = date.today() - timedelta(days=dias_atras)
    return _Resposta([
        {"date": d.isoformat(), "value": str(hoje)},
        {"date": (d - timedelta(days=3)).isoformat(), "value": str(ant)},
    ])


def test_observacao_velha_demais_e_descartada(monkeypatch):
    """Usar dado de semana passada como "hoje" produz um delta com cara de
    variação do dia -- pior que não ter, porque parece medição."""
    monkeypatch.setenv("FRED_API_KEY", "x")
    monkeypatch.setattr(snap.SESSION, "get", lambda *a, **k: _resp_fred(7))
    monkeypatch.setattr(snap, "_dois_ultimos_fechamentos", lambda t: (_ for _ in ()).throw(RuntimeError("fora")))
    monkeypatch.setattr(snap, "_variacao_do_dia", lambda t: None)
    monkeypatch.setattr(snap, "_kospi_do_snapshot_global", lambda: (None, ""))
    monkeypatch.setattr(snap, "_serie_sox", lambda **_k: (None, ""))
    monkeypatch.setattr(snap, "_manchetes_china", lambda: (None, ""))
    monkeypatch.setattr(snap, "_earnings_da_carteira", lambda: (None, None, ""))

    dados, diag = snap.coletar()

    assert "yield_30y_hoje" not in dados
    assert "7 dias" in diag["erros"]["DGS30"]


def test_observacao_de_ontem_passa(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "x")
    monkeypatch.setattr(snap.SESSION, "get", lambda *a, **k: _resp_fred(1))
    monkeypatch.setattr(snap, "_dois_ultimos_fechamentos", lambda t: (_ for _ in ()).throw(RuntimeError("fora")))
    monkeypatch.setattr(snap, "_variacao_do_dia", lambda t: None)
    monkeypatch.setattr(snap, "_kospi_do_snapshot_global", lambda: (None, ""))
    monkeypatch.setattr(snap, "_serie_sox", lambda **_k: (None, ""))
    monkeypatch.setattr(snap, "_manchetes_china", lambda: (None, ""))
    monkeypatch.setattr(snap, "_earnings_da_carteira", lambda: (None, None, ""))

    dados, diag = snap.coletar()

    assert dados["yield_30y_hoje"] == 5.31
    assert "DGS30" not in diag["erros"]


def test_o_petroleo_vem_do_futuro_e_nao_do_FRED(monkeypatch):
    """CL=F fecha todo pregão e é o preço que o mercado está olhando agora. O
    FRED é a fonte oficial, mas para ESTE sinal a frescura importa mais que a
    procedência."""
    monkeypatch.setenv("FRED_API_KEY", "x")
    monkeypatch.setattr(snap, "_dois_ultimos_fechamentos",
                        lambda t: (84.77, 82.0, date.today().isoformat()))
    monkeypatch.setattr(snap.SESSION, "get", lambda *a, **k: _resp_fred(7, hoje=70.0, ant=69.0))
    monkeypatch.setattr(snap, "_variacao_do_dia", lambda t: None)
    monkeypatch.setattr(snap, "_kospi_do_snapshot_global", lambda: (None, ""))
    monkeypatch.setattr(snap, "_serie_sox", lambda **_k: (None, ""))
    monkeypatch.setattr(snap, "_manchetes_china", lambda: (None, ""))
    monkeypatch.setattr(snap, "_earnings_da_carteira", lambda: (None, None, ""))

    dados, _ = snap.coletar()

    assert dados["wti_hoje"] == 84.77       # do futuro, não os 70.0 do FRED
    assert "DCOILWTICO" not in dados


def test_o_FRED_ainda_serve_de_reserva(monkeypatch):
    """Se o futuro falhar, o dado oficial recente ainda é melhor que nada."""
    monkeypatch.setenv("FRED_API_KEY", "x")
    monkeypatch.setattr(snap, "_dois_ultimos_fechamentos",
                        lambda t: (_ for _ in ()).throw(RuntimeError("yfinance fora")))
    monkeypatch.setattr(snap.SESSION, "get", lambda *a, **k: _resp_fred(1, hoje=84.0, ant=79.5))
    monkeypatch.setattr(snap, "_variacao_do_dia", lambda t: None)
    monkeypatch.setattr(snap, "_kospi_do_snapshot_global", lambda: (None, ""))
    monkeypatch.setattr(snap, "_serie_sox", lambda **_k: (None, ""))
    monkeypatch.setattr(snap, "_manchetes_china", lambda: (None, ""))
    monkeypatch.setattr(snap, "_earnings_da_carteira", lambda: (None, None, ""))

    dados, _ = snap.coletar()
    assert dados["wti_hoje"] == 84.0


# ── ruído de float32 não vai para o banco ───────────────────────────────────

def test_preco_do_provider_sai_limpo(monkeypatch):
    """Produção 19/08/2026: o WTI chegou como 84.43000030517578. O yfinance
    devolve float32 e a conversão para float64 expõe o ruído.

    Na tela isso não aparece (o formatador arredonda), mas o valor CRU é
    gravado no `raw` do snapshot -- que existe para revisar thresholds meses
    depois, e é aí que a sujeira atrapalha."""
    import pandas as pd
    import numpy as np

    idx = pd.date_range("2026-08-17", periods=2, freq="B")
    df = pd.DataFrame({"Close": np.array([82.0, 84.43], dtype="float32")}, index=idx)

    try:
        from agent import market_data_provider as mdp
    except ImportError:
        import market_data_provider as mdp  # type: ignore

    monkeypatch.setattr(mdp, "get_daily_history",
                        lambda t, p="6mo", **k: mdp.HistoryResult(df=df, source="teste"))

    ultimo, anterior, _ = snap._dois_ultimos_fechamentos("CL=F")
    assert ultimo == 84.43
    assert anterior == 82.0
    # e o valor cru era mesmo sujo -- se um dia o provider passar a devolver
    # float64, este teste vira redundante em vez de falhar
    assert float(df["Close"].iloc[-1]) != 84.43


def test_arredondar_nao_apaga_precisao_util():
    """4 casas e não 2: preço de índice ou câmbio pode precisar de mais que
    centavo, e truncar ali inventaria movimento que não houve."""
    assert snap.CASAS_DO_PRECO >= 4


# ── sessão em curso não é dia fechado ───────────────────────────────────────
#
# Produção 19/08/2026, 00:25 UTC = 09:25 em Seul, 25 min após a abertura da KRX.
# Duas coletas do MESMO dado, minutos apart:
#
#     linha de comando   sk_hynix +1,03   samsung -2,19   kospi +2,42
#     rota (botão)       sk_hynix -9,33   samsung -7,45   kospi descartado
#
# A primeira leu o pregão de ontem; a segunda, a barra em andamento de hoje.
# `sem_barra_incompleta` não pega: ela descarta Close vazio, e barra intradiária
# tem Close -- provisório.
#
# Ler sessão em curso como dia fechado faz o número mudar a manhã inteira, e o
# sinal disparar conforme a hora em que alguém abre a tela. E contradiz a
# premissa do próprio sinal: ele vale como leading indicator PORQUE a Coreia já
# fechou.

from datetime import datetime as _dt


def _hoje_kst(hora_utc: float):
    """agora_utc tal que em Seul (UTC+9) seja `hora_utc` local."""
    base = _dt(2026, 8, 19, 0, 0)
    return base + timedelta(hours=hora_utc - 9)


def test_barra_de_hoje_com_bolsa_aberta_e_recusada():
    agora = _dt(2026, 8, 19, 0, 25)          # 09:25 em Seul
    assert snap._sessao_ainda_aberta("000660.KS", date(2026, 8, 19), agora) is True


def test_depois_do_fechamento_a_barra_de_hoje_vale():
    """O cron das 07:50 BRT roda às 19:50 em Seul -- sessão encerrada há horas.
    Recusar a barra aí jogaria fora justamente o dado mais fresco e útil."""
    agora = _dt(2026, 8, 19, 10, 50)         # 19:50 em Seul
    assert snap._sessao_ainda_aberta("000660.KS", date(2026, 8, 19), agora) is False


def test_barra_de_ontem_sempre_vale():
    agora = _dt(2026, 8, 19, 0, 25)
    assert snap._sessao_ainda_aberta("000660.KS", date(2026, 8, 18), agora) is False


def test_praca_desconhecida_mantem_o_comportamento_antigo():
    """Sufixo fora da tabela não é tratado, e isso está dito no código em vez de
    virar suposição silenciosa."""
    agora = _dt(2026, 8, 19, 0, 25)
    assert snap._sessao_ainda_aberta("NVDA", date(2026, 8, 19), agora) is False


def test_recua_um_pregao_em_vez_de_apagar_o_sinal(monkeypatch):
    """Devolver None com a bolsa aberta apagaria o sinal a manhã inteira na
    Ásia. A última sessão FECHADA está logo atrás -- é ela que o sinal quer."""
    import pandas as pd
    idx = pd.to_datetime(["2026-08-15", "2026-08-18", "2026-08-19"])
    # ontem: 100 -> 101 (+1%). hoje, em curso: 101 -> 91 (-9,9%)
    df = pd.DataFrame({"Close": [100.0, 101.0, 91.0]}, index=idx)

    try:
        from agent import market_data_provider as mdp
    except ImportError:
        import market_data_provider as mdp  # type: ignore
    monkeypatch.setattr(mdp, "get_daily_history",
                        lambda t, p="6mo", **k: mdp.HistoryResult(df=df, source="teste"))
    monkeypatch.setattr(snap, "_sessao_ainda_aberta", lambda *a, **k: True)

    assert snap._variacao_do_dia("000660.KS") == 1.0     # o pregão fechado


def test_com_a_bolsa_fechada_usa_a_ultima_barra(monkeypatch):
    import pandas as pd
    idx = pd.to_datetime(["2026-08-15", "2026-08-18", "2026-08-19"])
    df = pd.DataFrame({"Close": [100.0, 101.0, 91.0]}, index=idx)

    try:
        from agent import market_data_provider as mdp
    except ImportError:
        import market_data_provider as mdp  # type: ignore
    monkeypatch.setattr(mdp, "get_daily_history",
                        lambda t, p="6mo", **k: mdp.HistoryResult(df=df, source="teste"))
    monkeypatch.setattr(snap, "_sessao_ainda_aberta", lambda *a, **k: False)

    assert snap._variacao_do_dia("000660.KS") == -9.9


# ── erro repetido não enche o banco ─────────────────────────────────────────

def test_o_mesmo_motivo_nao_e_repetido_por_ticker():
    """Quando a fonte cai, ela cai para TODOS os tickers com a mesma mensagem.
    Em 19/08/2026 isso produziu doze cópias de um erro de curl de 130 chars
    dentro de `coleta.erros` -- que é persistido no `raw` e fica lá para
    sempre."""
    doze = [f"{t}: CONNECT tunnel failed, response 403" for t in "ABCDEFGHIJKL"]
    saida = snap._resumir(doze)
    assert saida.count("CONNECT tunnel") == 1
    assert "+11 ticker(s)" in saida


def test_motivos_diferentes_sobrevivem():
    """Repetição escondendo um motivo DIFERENTE no meio seria pior que a
    repetição."""
    saida = snap._resumir([
        "NVDA: CONNECT tunnel failed",
        "MU: CONNECT tunnel failed",
        "ARM: sem coluna de surpresa em ['Outra']",
    ])
    assert "CONNECT tunnel" in saida
    assert "sem coluna de surpresa" in saida


def test_sem_motivo_nenhum_devolve_vazio():
    """Nenhum balanço na janela não é erro -- string vazia mantém a chave fora
    de coleta.erros."""
    assert snap._resumir([]) == ""


# ── o Kospi entra pela mesma régua das ações ────────────────────────────────
#
# Produção 19/08/2026, 00:58 UTC = 09:58 em Seul, com a KRX aberta há uma hora:
#
#     sk_hynix  +1,03   samsung  -2,19    <- pregão fechado (conserto anterior)
#     kospi     -7,27                     <- sessão em curso
#
# O sinal disparou com DUAS bases de tempo no mesmo cartão. Isso é pior que a
# inconsistência que o antecedeu, porque parece coerente: três números lado a
# lado, dois de ontem e um de agora, sem nada indicando a diferença.
#
# Escapou do primeiro conserto porque o Kospi vem por OUTRO caminho -- o
# snapshot global, não o _variacao_do_dia.

def test_indice_coreano_tambem_e_reconhecido():
    """A tabela de praças era só por sufixo, e índice não tem sufixo."""
    agora = _dt(2026, 8, 19, 0, 58)          # 09:58 em Seul
    assert snap._sessao_ainda_aberta("^KS11", date(2026, 8, 19), agora) is True
    assert snap._sessao_ainda_aberta("^KS11", date(2026, 8, 18), agora) is False


def test_kospi_de_sessao_em_curso_e_descartado(monkeypatch):
    import agent.market_alerts as ma
    monkeypatch.setattr(ma, "get_global_market_snapshot", lambda: {"items": [
        {"ticker": "^KS11", "changePct": -7.27, "suspect": False,
         "asOf": date.today().isoformat()},
    ]})
    monkeypatch.setattr(snap, "_sessao_ainda_aberta", lambda *a, **k: True)

    pct, motivo = snap._kospi_do_snapshot_global()
    assert pct is None
    assert "em curso" in motivo


def test_kospi_de_sessao_encerrada_passa(monkeypatch):
    """É o caso do cron (19:50 em Seul). Descartar aqui jogaria fora justamente
    o dado que o retrato diário existe para capturar."""
    import agent.market_alerts as ma
    monkeypatch.setattr(ma, "get_global_market_snapshot", lambda: {"items": [
        {"ticker": "^KS11", "changePct": -7.27, "suspect": False,
         "asOf": date.today().isoformat()},
    ]})
    monkeypatch.setattr(snap, "_sessao_ainda_aberta", lambda *a, **k: False)

    pct, motivo = snap._kospi_do_snapshot_global()
    assert pct == -7.27
    assert motivo == ""


def test_sem_asOf_nao_derruba_o_kospi(monkeypatch):
    """`asOf` é campo do snapshot global; se ele sumir numa mudança lá, o certo
    é seguir com o número (comportamento antigo) e não apagar o sinal."""
    import agent.market_alerts as ma
    monkeypatch.setattr(ma, "get_global_market_snapshot", lambda: {"items": [
        {"ticker": "^KS11", "changePct": -7.27, "suspect": False},
    ]})
    pct, _ = snap._kospi_do_snapshot_global()
    assert pct == -7.27
