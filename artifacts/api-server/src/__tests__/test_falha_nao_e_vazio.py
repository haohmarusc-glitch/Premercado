"""
Falha de leitura não é ausência de dado.

Incidente real (26/08/2026 15:29). O chat respondeu:

    "Seu Plano de Saída está vazio. Você não tem metas ou janelas de venda
     cadastradas para nenhuma de suas posições."

`get_exit_plan_items` devolvia `[{"error": "..."}]` quando a leitura falhava
-- uma LISTA NÃO VAZIA cujo único item não tem ticker. Duas leituras possíveis
para o modelo: reportar a falha, ou concluir "nenhum item real, logo vazio".

A segunda é a que custa dinheiro. Dizer a alguém que o stop-loss dele não
existe, quando existe, é pior que não responder: ele deixa de agir numa
posição que tinha gatilho definido.

`get_scenario_status` tinha a mesma doença em forma pior, porque suas duas
respostas sem dado são quase iguais e só uma é verdade:

    {"configured": False, "note": "Usuário ainda não configurou..."}   verdade
    {"error": "..."}                                                   falha

Confundir as duas produz exatamente a frase que apareceu no veredito de ARM:
"Cenário não foi configurado pelo usuário".
"""
import pytest

from agent.tools import _falha_de_leitura


def test_a_falha_se_anuncia_em_tres_camadas():
    r = _falha_de_leitura("o Plano de Saída", TimeoutError("10s"))
    # 1. bandeira estrutural, para o CÓDIGO ramificar
    assert r["leitura_falhou"] is True
    # 2. o erro real, para o log
    assert "TimeoutError" in r["error"] and "10s" in r["error"]
    # 3. a instrução, para o MODELO -- não há validador entre a ferramenta e
    #    a resposta do chat, então o aviso viaja no payload.
    assert "NÃO significa que está vazio" in r["aviso"]
    assert "NUNCA que não há itens" in r["aviso"]


def test_o_aviso_nomeia_o_que_falhou():
    """"A leitura falhou" sem dizer de quê manda o operador procurar no
    escuro."""
    assert "Plano de Saída" in _falha_de_leitura("o Plano de Saída", ValueError("x"))["aviso"]
    assert "Painel de Cenários" in _falha_de_leitura("o Painel de Cenários", ValueError("x"))["aviso"]


def test_o_aviso_cobre_os_dois_enganos_possiveis():
    """Vazio E não-configurado. O segundo é o engano específico do cenário,
    que tem uma resposta legítima parecidíssima com a falha."""
    aviso = _falha_de_leitura("o Painel de Cenários", ValueError("x"))["aviso"]
    assert "vazio" in aviso and "não configurou nada" in aviso


@pytest.mark.parametrize("erro", [
    TimeoutError("timeout"), ConnectionError("recusou"),
    ValueError(""), RuntimeError("500 Server Error"),
])
def test_qualquer_excecao_vira_payload_legivel(erro):
    r = _falha_de_leitura("o Plano de Saída", erro)
    assert r["leitura_falhou"] is True
    assert type(erro).__name__ in r["error"]


def test_as_ferramentas_que_podem_parecer_vazias_usam_o_helper():
    """As três que devolvem forma confundível com ausência. As de ESCRITA
    (`create_alert`, `update_exit_plan_item`) já dizem `created: False` /
    `updated: False`, que não se confunde com nada -- ficam de fora."""
    import ast
    import os
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    arvore = ast.parse(open(os.path.join(raiz, "agent", "tools.py"),
                            encoding="utf-8").read())
    for nome in ("get_exit_plan_items", "list_alerts", "get_scenario_status"):
        fn = next(n for n in ast.walk(arvore)
                  if isinstance(n, ast.FunctionDef) and n.name == nome)
        corpo = ast.unparse(fn)
        assert "_falha_de_leitura" in corpo, f"{nome} ainda devolve erro cru"
        assert "{'error': str(e)}" not in corpo, f"{nome} tem retorno confundível"
