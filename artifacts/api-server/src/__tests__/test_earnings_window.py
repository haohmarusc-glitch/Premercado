"""
Modo earnings: detecção da janela e vol implícita do straddle ATM.

Auditoria 17/08/2026. A vol do Painel de Cenários é o desvio-padrão dos
retornos diários de 12 meses -- simétrica, sem drift, e diluindo o dia do
balanço entre 251 dias comuns. Com um earnings dentro da janela isso não é
aproximação, é o número errado (PDD: modelo ±4,7%/sem contra 10,3% realizados,
centro -8,2%).

Este arquivo cobre o lado Python: "há balanço até a data-alvo?" e "quanto as
opções estão cobrando pra atravessá-lo?". A regra do SELO que compara as três
vols mora no TS (@workspace/scenario-math) e é testada em
premarket/src/__tests__/earnings-window-selo.test.ts, com os números medidos
de PDD e XPEV.

As funções aqui são puras de propósito -- o straddle e a escolha de vencimento
não tocam a rede, então o teste exercita a matemática de verdade em vez de
exercitar um mock.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_earnings_window.py -v
"""
from datetime import date

import pandas as pd
import pytest

from agent import earnings_window as ew


# ── escolha do vencimento ───────────────────────────────────────────────────

VENCIMENTOS = ["2026-08-21", "2026-08-28", "2026-09-04", "2026-09-18"]


def test_pega_o_primeiro_vencimento_que_cobre_o_balanco():
    # Balanço 26/08: o de 21/08 vence ANTES do evento e não o precifica.
    assert ew.escolher_vencimento(VENCIMENTOS, "2026-08-26") == "2026-08-28"


def test_vencimento_no_proprio_dia_do_balanco_serve():
    # >= e não >: o straddle que vence no dia ainda carrega o evento.
    assert ew.escolher_vencimento(VENCIMENTOS, "2026-08-28") == "2026-08-28"


def test_prefere_o_mais_curto_entre_os_que_cobrem():
    """Vencimento longo embute vol de calendário além do evento -- o mais
    curto que cobre é o proxy mais limpo do move do balanço."""
    assert ew.escolher_vencimento(VENCIMENTOS, "2026-08-22") == "2026-08-28"


def test_sem_vencimento_apos_o_balanco_devolve_none():
    assert ew.escolher_vencimento(VENCIMENTOS, "2026-12-01") is None


def test_lista_vazia_ou_data_invalida_nao_explode():
    assert ew.escolher_vencimento([], "2026-08-26") is None
    assert ew.escolher_vencimento(VENCIMENTOS, "não é data") is None
    assert ew.escolher_vencimento(VENCIMENTOS, None) is None


def test_vencimento_ilegivel_e_ignorado_sem_derrubar_os_outros():
    assert ew.escolher_vencimento(["lixo", "2026-08-28"], "2026-08-26") == "2026-08-28"


# ── straddle ATM ────────────────────────────────────────────────────────────

def _lado(strikes, bid, ask, last=None):
    return pd.DataFrame({
        "strike": strikes,
        "bid": bid,
        "ask": ask,
        "lastPrice": last if last is not None else [0.0] * len(strikes),
    })


def test_straddle_no_dinheiro_vira_percentual_do_spot():
    # Spot 100, strike 100: call meio 4,00 + put meio 3,50 = 7,50 -> 7,5%.
    calls = _lado([95, 100, 105], [8.0, 3.8, 1.0], [8.4, 4.2, 1.2])
    puts = _lado([95, 100, 105], [1.0, 3.3, 7.8], [1.2, 3.7, 8.2])
    pct, motivo = ew.move_implicito_do_chain(calls, puts, 100.0)
    assert motivo is None
    assert pct == pytest.approx(7.5)


def test_escolhe_o_strike_mais_proximo_quando_nao_ha_strike_exato():
    # Spot 102: 100 está mais perto que 105.
    calls = _lado([100, 105], [3.8, 1.0], [4.2, 1.2])
    puts = _lado([100, 105], [3.3, 7.8], [3.7, 8.2])
    pct, _ = ew.move_implicito_do_chain(calls, puts, 102.0)
    assert pct == pytest.approx(7.35, abs=0.01)  # 7,50 / 102


def test_so_usa_strike_presente_nos_dois_lados():
    """Straddle é call E put no MESMO strike -- casar strikes diferentes
    somaria um spread diagonal e o chamaria de straddle."""
    calls = _lado([100, 105], [3.8, 1.0], [4.2, 1.2])
    puts = _lado([105, 110], [7.8, 12.0], [8.2, 12.4])   # 100 não existe nos puts
    pct, _ = ew.move_implicito_do_chain(calls, puts, 100.0)
    # Cai no 105, o único comum: 1,10 + 8,00 = 9,10 sobre 100.
    assert pct == pytest.approx(9.1)


def test_lastprice_só_entra_quando_o_book_esta_vazio():
    """Fora do pregão o yfinance zera bid/ask. lastPrice é de um negócio
    possivelmente antigo, mas implícita defasada informa mais que nenhuma."""
    calls = _lado([100], [0.0], [0.0], last=[4.0])
    puts = _lado([100], [0.0], [0.0], last=[3.5])
    pct, motivo = ew.move_implicito_do_chain(calls, puts, 100.0)
    assert motivo is None
    assert pct == pytest.approx(7.5)


def test_strike_longe_demais_do_dinheiro_recusa_em_vez_de_estimar():
    """Cadeia rala: chamar de ATM um strike a 20% do dinheiro devolveria um
    número que PARECE implícita e não é. Melhor cair no fallback manual."""
    calls = _lado([80], [22.0], [22.4])
    puts = _lado([80], [0.5], [0.7])
    pct, motivo = ew.move_implicito_do_chain(calls, puts, 100.0)
    assert pct is None
    assert "longe demais" in motivo


def test_cadeia_vazia_ou_spot_ausente_devolve_motivo_legivel():
    vazio = _lado([], [], [])
    assert ew.move_implicito_do_chain(vazio, vazio, 100.0)[1] == "cadeia de opções vazia"
    calls = _lado([100], [3.8], [4.2])
    assert ew.move_implicito_do_chain(calls, calls, 0.0)[1] == "sem preço spot"
    assert ew.move_implicito_do_chain(None, None, 100.0)[1] == "cadeia de opções vazia"


def test_sem_preco_utilizavel_no_strike_atm():
    calls = _lado([100], [0.0], [0.0], last=[0.0])
    puts = _lado([100], [3.3], [3.7])
    pct, motivo = ew.move_implicito_do_chain(calls, puts, 100.0)
    assert pct is None
    assert "sem preço utilizável" in motivo


# ── próximo balanço ─────────────────────────────────────────────────────────

def _datas(*iso: str) -> pd.DataFrame:
    return pd.DataFrame({"EPS Estimate": [None] * len(iso)},
                        index=pd.DatetimeIndex([pd.Timestamp(d) for d in iso]))


def test_pega_o_proximo_balanco_a_partir_de_hoje():
    df = _datas("2026-05-27", "2026-08-24", "2026-11-20")
    assert ew.proximo_earnings(df, date(2026, 8, 17)) == "2026-08-24"


def test_balanco_de_hoje_ainda_conta_como_a_frente():
    """BMO reage no próprio pregão, AMC no seguinte -- em quase todo o dia o
    evento ainda está à frente do preço. Excluí-lo o esconderia justamente no
    dia em que mais importa."""
    df = _datas("2026-08-24")
    assert ew.proximo_earnings(df, date(2026, 8, 24)) == "2026-08-24"


def test_so_passado_devolve_none():
    df = _datas("2026-05-27", "2026-02-20")
    assert ew.proximo_earnings(df, date(2026, 8, 17)) is None


def test_sem_datas_devolve_none():
    assert ew.proximo_earnings(None, date(2026, 8, 17)) is None
    assert ew.proximo_earnings(pd.DataFrame(), date(2026, 8, 17)) is None


def test_indice_com_timezone_funciona():
    """get_earnings_dates devolve índice com tz; comparar tz-aware com date
    crua levantaria TypeError e derrubaria o ticker inteiro."""
    idx = pd.DatetimeIndex([pd.Timestamp("2026-08-24 16:00", tz="America/New_York")])
    df = pd.DataFrame({"EPS Estimate": [None]}, index=idx)
    assert ew.proximo_earnings(df, date(2026, 8, 17)) == "2026-08-24"


# ── fallback manual da implícita ────────────────────────────────────────────

def test_implicito_manual_vem_com_carimbo_de_coleta():
    """Número manual sem a data ao lado é indistinguível de número vivo --
    exatamente o erro que a auditoria pegou no radar."""
    out = ew._implicito_manual("PDD")
    assert out["pct"] == pytest.approx(7.38)   # OptionSlam, move_impl_sem
    assert out["fonte"] == "manual"
    assert out["coletadoEm"]                    # obrigatório
    assert out["fonteNome"] == "OptionSlam"


def test_implicito_manual_de_ticker_sem_coleta_e_none():
    assert ew._implicito_manual("NVDA") is None


def test_ticker_com_coleta_mas_sem_move_implicito_e_none():
    # AOSL está no arquivo com move_impl_sem: null -- presença da linha não é
    # presença do número.
    assert ew._implicito_manual("AOSL") is None


# ── orquestração ────────────────────────────────────────────────────────────

def test_balanco_fora_da_janela_nao_e_erro(monkeypatch):
    """Ticker sem balanço até a data-alvo é o caso COMUM: o painel está no seu
    terreno e o card simplesmente não aparece."""
    monkeypatch.setattr(ew, "yf", type("F", (), {"Ticker": lambda self, t: object()})())
    monkeypatch.setattr(ew._earnings_dates, "buscar",
                        lambda t, fetch, **k: (_datas("2026-11-20"), "cache", None))

    out = ew.analisar("PDD", "2026-10-07", hoje=date(2026, 8, 17))
    assert out["naJanela"] is False
    assert out["proximoEarnings"] == "2026-11-20"
    assert "error" not in out


def test_falha_nas_datas_vira_erro_legivel(monkeypatch):
    monkeypatch.setattr(ew, "yf", type("F", (), {"Ticker": lambda self, t: object()})())
    monkeypatch.setattr(ew._earnings_dates, "buscar",
                        lambda t, fetch, **k: (None, "erro", "429 Too Many Requests"))

    out = ew.analisar("PDD", "2026-10-07", hoje=date(2026, 8, 17))
    assert "429" in out["error"]


def test_cache_vencido_das_datas_e_anunciado(monkeypatch):
    """Uma data reagendada muda a resposta de 'está na janela?' de sim para
    não -- servir a cópia velha sem marca seria o pior dos dois mundos."""
    monkeypatch.setattr(ew, "yf", type("F", (), {"Ticker": lambda self, t: object()})())
    monkeypatch.setattr(ew._earnings_dates, "buscar",
                        lambda t, fetch, **k: (_datas("2026-11-20"), "cache_vencido", "timeout"))

    out = ew.analisar("PDD", "2026-10-07", hoje=date(2026, 8, 17))
    assert out["fonteDatas"] == "cache_vencido"


def test_data_alvo_invalida_nao_passa_silenciosamente():
    out = ew.analisar("PDD", "07/10/2026", hoje=date(2026, 8, 17))
    assert "data-alvo inválida" in out["error"]
