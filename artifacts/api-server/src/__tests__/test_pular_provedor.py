"""
FallbackClient.pular_provedor_atual — avançar a cadeia sem CONDENAR.

Problema que motivou (auditoria 17/08/2026): quando um provedor devolvia um
"toco" (resposta de uma linha em vez da análise), a rota falhava com "resposta
curta demais" e o usuário clicava de novo — caindo no MESMO provedor. Toco não
é falha permanente, então nada o tirava da cadeia, e o botão "Análise com IA"
simplesmente não funcionava enquanto aquele provedor estivesse ruim.

A distinção que o teste fixa:

  _condenar ............ falha PERMANENTE (modelo inexistente, conta sem
                         saldo). Entra em _mortos, não volta nesta run.
  pular_provedor_atual . qualidade ruim PONTUAL. Avança o ponteiro e pronto --
                         o provedor continua elegível, inclusive para o
                         próximo pedido do mesmo processo.

Confundir os dois é caro nos dois sentidos: condenar por toco descarta um
provedor bom pelo resto do processo; não avançar deixa o usuário preso.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_pular_provedor.py -v
"""
import pytest

from agent import provider as prov


@pytest.fixture
def cadeia(monkeypatch):
    """FallbackClient com três provedores, sem tocar em rede nem em chaves."""
    monkeypatch.setattr(prov, "_provider_order", lambda: ["anthropic", "gemini", "openrouter"])
    monkeypatch.setattr(prov, "_has_key", lambda _p: True)
    return prov.FallbackClient()


def test_avanca_para_o_proximo_e_devolve_true(cadeia, capsys):
    assert cadeia.provider_name == "anthropic"
    assert cadeia.pular_provedor_atual("resposta curta demais (12 chars)") is True
    assert cadeia.provider_name == "gemini"

    saida = capsys.readouterr().err
    assert "[provider] pulando anthropic" in saida
    assert "resposta curta demais (12 chars)" in saida
    assert "-> gemini" in saida


def test_nao_condena_o_provedor_pulado(cadeia):
    """O ponto central: o provedor continua elegível. Se entrasse em _mortos,
    um toco isolado o tiraria da cadeia pelo resto do processo."""
    cadeia.pular_provedor_atual("toco")
    assert cadeia._mortos == {}


def test_pula_provedor_ja_condenado(cadeia):
    """Quem já falhou de forma permanente não deve receber a vez."""
    cadeia._condenar("gemini", "modelo inexistente")
    assert cadeia.pular_provedor_atual("toco") is True
    assert cadeia.provider_name == "openrouter"


def test_devolve_false_no_fim_da_cadeia(cadeia, capsys):
    """Sem próximo, quem chamou precisa desistir com erro legível em vez de
    repetir o mesmo provedor para sempre."""
    assert cadeia.pular_provedor_atual("toco") is True   # -> gemini
    assert cadeia.pular_provedor_atual("toco") is True   # -> openrouter
    assert cadeia.pular_provedor_atual("toco") is False  # acabou
    # .err e não .out: desde 18/08/2026 todo diagnóstico do provider vai para
    # stderr. Iam para stdout, que em analise_rapida_ia.py é do JSON final --
    # o efeito era o diagnóstico sumir E poluir o pipe do resultado.
    assert "sem próximo provedor disponível" in capsys.readouterr().err


def test_false_quando_todos_os_seguintes_estao_condenados(cadeia):
    cadeia._condenar("gemini", "sem saldo")
    cadeia._condenar("openrouter", "404")
    assert cadeia.pular_provedor_atual("toco") is False
    assert cadeia.provider_name == "anthropic"  # ponteiro não se move à toa


def test_provider_name_segue_o_ponteiro(cadeia):
    """models/provider_name leem de _current_idx — depois de pular, o próximo
    create() tem que sair no provedor novo, não no antigo."""
    cadeia.pular_provedor_atual("toco")
    assert cadeia._order[cadeia._current_idx] == cadeia.provider_name == "gemini"
