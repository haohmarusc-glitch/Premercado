"""Testes de run_checkers.py -- o lote que substituiu três spawns de Python.

O risco central de batelar não é o caminho feliz: é que um orçamento único pro
lote transformaria três falhas parciais independentes numa falha total. Quase
todo teste aqui existe pra provar que isso NÃO acontece.
"""
import io
import json
import sys

import pytest

from agent import run_checkers as rc
from agent.bounded_parallel import MIN_BUDGET_S


# ── Import pesado dentro da janela medida ─────────────────────────────────────


def test_modulos_de_check_sao_importados_no_topo():
    """Import sob demanda dentro de cada check reintroduz o bug do orçamento.

    budget_from_deadline() calcula a fatia a partir do relógio no momento da
    chamada, então tudo que é caro precisa ter acontecido ANTES. Com os imports
    lazy, o pandas+numpy+yfinance subia depois da fatia já calculada e o tempo
    dele não entrava em orçamento nenhum.

    Produção 04/08: `[run_checkers] spike: 121.9s (fatia de 40.0s)` -- o
    bounded_parallel_map respeitou os 40s ("orçamento esgotado com 6
    pendentes"); os ~80s restantes eram o import escondido dentro da medição.
    """
    for modulo in (
        "agent.market_alerts",
        "agent.get_intraday_spikes",
        "agent.get_bounce_alerts",
        "agent.get_squeeze_alerts",
    ):
        assert modulo in sys.modules, f"{modulo} precisa entrar antes do primeiro budget"


def test_funcoes_por_ticker_vem_dos_scripts_standalone():
    """Reaproveitadas, não reimplementadas -- senão viram uma segunda cópia da
    lógica, livre pra sair de sincronia com o script original."""
    from agent.get_bounce_alerts import _bounce_for
    from agent.get_intraday_spikes import _spikes_for
    from agent.get_squeeze_alerts import _progress_for

    assert rc._spikes_for is _spikes_for
    assert rc._bounce_for is _bounce_for
    assert rc._progress_for is _progress_for


# ── Divisão do orçamento ──────────────────────────────────────────────────────


def test_squeeze_recebe_o_dobro_dos_outros():
    """check_squeeze_setup faz várias chamadas de rede por ticker -- era por isso
    que o timeout dele já era 120s contra 60s dos irmãos."""
    pendentes = ["spike", "bounce", "squeeze"]
    spike = rc._fatia(200.0, "spike", pendentes)
    squeeze = rc._fatia(200.0, "squeeze", pendentes)
    assert spike == pytest.approx(50.0)
    assert squeeze == pytest.approx(100.0)


def test_sobra_de_quem_terminou_cedo_vai_pros_seguintes():
    """A fatia é recalculada a cada passo justamente pra isso: se o spike gastou
    5s de uma fatia de 50s, o tempo ocioso não pode simplesmente evaporar."""
    # Fatia do bounce com os três pendentes...
    com_todos = rc._fatia(200.0, "bounce", ["spike", "bounce", "squeeze"])
    # ...e depois que o spike saiu tendo gastado quase nada (restante quase igual).
    sem_spike = rc._fatia(195.0, "bounce", ["bounce", "squeeze"])
    assert sem_spike > com_todos


def test_fatia_nunca_desce_abaixo_do_piso():
    """Startup comeu o orçamento: ainda vale tentar uma janela curta -- alguns
    tickers respondem nela -- em vez de devolver lista vazia na hora."""
    assert rc._fatia(1.0, "spike", ["spike", "bounce", "squeeze"]) == MIN_BUDGET_S
    assert rc._fatia(-50.0, "squeeze", ["squeeze"]) == MIN_BUDGET_S


def test_ultimo_check_pega_todo_o_restante():
    assert rc._fatia(60.0, "squeeze", ["squeeze"]) == pytest.approx(60.0)


# ── Isolamento de falhas ──────────────────────────────────────────────────────


@pytest.fixture
def rodar(monkeypatch):
    """Executa main() com stdin/saída controlados, devolvendo o payload parseado."""
    def _rodar(entrada: dict, runners: dict):
        monkeypatch.setattr(rc, "RUNNERS", runners)
        monkeypatch.setattr(rc, "_resultados", {})
        monkeypatch.setattr(rc, "_falhas", {})
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(entrada)))
        capturado = {}

        def _fake_exit(payload, code=0):
            capturado["payload"] = payload
            capturado["code"] = code
            raise SystemExit(code)

        monkeypatch.setattr(rc, "exit_now", _fake_exit)
        with pytest.raises(SystemExit):
            rc.main()
        return json.loads(capturado["payload"])
    return _rodar


def test_um_check_que_explode_nao_leva_os_outros(rodar):
    """O ponto todo do batelamento: antes eram três processos, e a morte de um
    era irrelevante pros outros. Num processo só isso precisa continuar valendo."""
    saida = rodar(
        {"tickers": ["NVDA"], "checks": ["spike", "bounce", "squeeze"]},
        {
            "spike": lambda tk, b: [{"ticker": "NVDA", "title": "pico"}],
            "bounce": lambda tk, b: (_ for _ in ()).throw(RuntimeError("yfinance caiu")),
            "squeeze": lambda tk, b: [{"ticker": "NVDA", "tier": "near"}],
        },
    )
    assert saida["resultados"]["spike"] == [{"ticker": "NVDA", "title": "pico"}]
    assert saida["resultados"]["squeeze"] == [{"ticker": "NVDA", "tier": "near"}]
    assert "bounce" not in saida["resultados"]
    # E a falha é NOMEADA, com o tipo do erro -- não some nem vira lista vazia.
    assert "RuntimeError" in saida["falhas"]["bounce"]
    assert "yfinance caiu" in saida["falhas"]["bounce"]


def test_todos_falhando_ainda_produz_json_valido(rodar):
    """O lado Node parseia stdout antes de olhar o exit code -- não pode receber
    saída vazia nem meio JSON."""
    saida = rodar(
        {"tickers": ["NVDA"], "checks": ["spike"]},
        {"spike": lambda tk, b: (_ for _ in ()).throw(ValueError("x"))},
    )
    assert saida["resultados"] == {}
    assert "spike" in saida["falhas"]


def test_roda_so_os_checks_pedidos(rodar):
    chamados = []

    def _marca(nome):
        return lambda tk, b: (chamados.append(nome), [])[1]

    saida = rodar(
        {"tickers": ["NVDA"], "checks": ["squeeze"]},
        {"spike": _marca("spike"), "bounce": _marca("bounce"), "squeeze": _marca("squeeze")},
    )
    assert chamados == ["squeeze"]
    assert saida["falhas"] == {}


def test_check_desconhecido_e_ignorado_sem_derrubar_o_lote(rodar):
    """Um typo no lado Node não pode custar o ciclo inteiro."""
    saida = rodar(
        {"tickers": ["NVDA"], "checks": ["spike", "inexistente"]},
        {"spike": lambda tk, b: []},
    )
    assert saida["resultados"] == {"spike": []}
    assert saida["falhas"] == {}


def test_ordem_e_do_mais_barato_pro_mais_caro(rodar):
    """Se o tempo acabar, quem fica sem fatia tem que ser o caro -- e o caro é o
    que já tem cache de 30min do lado do Python."""
    chamados = []

    def _marca(nome):
        return lambda tk, b: (chamados.append(nome), [])[1]

    # Pedidos fora de ordem de propósito.
    rodar(
        {"tickers": ["NVDA"], "checks": ["squeeze", "spike", "bounce"]},
        {"spike": _marca("spike"), "bounce": _marca("bounce"), "squeeze": _marca("squeeze")},
    )
    assert chamados == ["spike", "bounce", "squeeze"]


def test_stdin_invalido_cai_nos_tickers_default(rodar, monkeypatch):
    vistos = []
    monkeypatch.setattr("sys.stdin", io.StringIO("isso não é json"))
    monkeypatch.setattr(rc, "RUNNERS", {"spike": lambda tk, b: (vistos.extend(tk), [])[1]})
    monkeypatch.setattr(rc, "_resultados", {})
    monkeypatch.setattr(rc, "_falhas", {})
    monkeypatch.setattr(rc, "exit_now", lambda p, code=0: (_ for _ in ()).throw(SystemExit(0)))
    with pytest.raises(SystemExit):
        rc.main()
    from agent import config
    assert vistos == config.TICKERS


# ── SIGTERM entrega o parcial ─────────────────────────────────────────────────


def test_sigterm_entrega_o_que_ja_foi_calculado(monkeypatch):
    """Sem isso, o SIGTERM do timeout do Node jogaria fora tudo que já rodou --
    que é exatamente o custo que o batelamento não pode introduzir."""
    monkeypatch.setattr(rc, "_resultados", {"spike": [{"ticker": "NVDA"}]})
    monkeypatch.setattr(rc, "_falhas", {})
    capturado = {}
    monkeypatch.setattr(rc, "exit_now", lambda p, code=0: capturado.setdefault("payload", p))

    rc._ao_receber_sigterm(15, None)

    saida = json.loads(capturado["payload"])
    assert saida["resultados"]["spike"] == [{"ticker": "NVDA"}]
