"""
Em nome de quem o subprocesso age.

Vazamento real (26/08/2026), reportado da tela. O agente autentica na API
interna com a OPERATOR_API_KEY, e `requireAuth` resolvia essa chave SEMPRE
para a conta dona:

  Veredito        `get_exit_plan_items`/`get_scenario_status` devolviam o
                  plano e o cenário do DONO para qualquer conta
  Chat            "qual meu plano de saída?" respondia com o do dono
  Reavaliar Plano `update_exit_plan_item`/`create_exit_plan_item` podiam
                  ESCREVER no plano do dono a mando de outra conta

A escrita é a pior das três: ler dado alheio é vazamento, alterar é dano.

Este arquivo cobre o elo Python da corrente. Os outros dois (env do spawn e
o middleware) estão em `identidade-do-subprocesso.test.ts`.
"""
import pytest

from agent.tools import _internal_headers


@pytest.fixture(autouse=True)
def _ambiente_limpo(monkeypatch):
    monkeypatch.delenv("OPERATOR_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_ACTING_USER_ID", raising=False)


def test_representando_um_usuario_manda_a_identidade(monkeypatch):
    monkeypatch.setenv("OPERATOR_API_KEY", "chave")
    monkeypatch.setenv("AGENT_ACTING_USER_ID", "7")
    h = _internal_headers()
    assert h["Authorization"] == "Bearer chave"
    assert h["X-Acting-User-Id"] == "7"


def test_sem_representar_ninguem_cai_na_conta_dona(monkeypatch):
    """Run agendada não tem "usuário da requisição" -- e carteira.py e os
    scripts do operador também não. Sem o header a API resolve o dono, que é
    o comportamento correto para quem não representa ninguém."""
    monkeypatch.setenv("OPERATOR_API_KEY", "chave")
    h = _internal_headers()
    assert h["Authorization"] == "Bearer chave"
    assert "X-Acting-User-Id" not in h


def test_sem_chave_nao_manda_identidade(monkeypatch):
    """Dizer quem se é sem provar não serve de nada -- e a API ignoraria."""
    monkeypatch.setenv("AGENT_ACTING_USER_ID", "7")
    assert _internal_headers() == {}


@pytest.mark.parametrize("valor", [
    "0",            # não existe usuário 0
    "-1",           # sinal
    "1.5",          # decimal
    "1 OR 1=1",     # injeção
    "7; DROP TABLE users",
    "",
    "   ",
    "abc",
    "0x7",
    "７",           # dígito de largura plena: `isdigit()` aceita, `int()` também
])
def test_id_torto_nao_vira_header(monkeypatch, valor):
    """Melhor cair na conta dona do que mandar lixo: a API valida de novo do
    outro lado, mas um header malformado que atravessa é uma pergunta a menos
    respondida aqui."""
    monkeypatch.setenv("OPERATOR_API_KEY", "chave")
    monkeypatch.setenv("AGENT_ACTING_USER_ID", valor)
    assert "X-Acting-User-Id" not in _internal_headers()


def test_o_id_valido_atravessa_com_espaco_em_volta(monkeypatch):
    """O env var vem de `String(userId)` no Node, mas espaço em volta é o tipo
    de coisa que aparece quando alguém edita à mão."""
    monkeypatch.setenv("OPERATOR_API_KEY", "chave")
    monkeypatch.setenv("AGENT_ACTING_USER_ID", "  42  ")
    assert _internal_headers()["X-Acting-User-Id"] == "42"
