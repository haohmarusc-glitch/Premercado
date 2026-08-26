"""
A tabela de faixas do Fear & Greed, e a fronteira.

Incidente real (Veredito de 26/08/2026 13:30). A tela mostrou, ao mesmo tempo:

    prosa:   "Fear & Greed 54,9 (neutro em leitura de 13:28:50 de 26/ago)"
    painel:  "Fear & Greed 55.2 · ganância"

A primeira leitura foi que os dois usavam vocabulários de faixa diferentes.
NÃO era: as duas tabelas eram idênticas, e os dois rótulos estão certos --
54,9 <= 55 é neutro, 55,2 > 55 é ganância. O que aconteceu foi 0,3 ponto de
deriva intradia atravessar a fronteira dos 55 e trocar a palavra.

Duas coisas saem daí, e este arquivo fixa as duas:

  1. A tabela passa a existir UMA vez. `tools.py` e `get_macro.py` tinham
     cópias idênticas -- defeito latente, não ativo, mas é exatamente a forma
     do defeito da MM50 que apareceu no mesmo dia.
  2. A distância até a borda viaja junto. O rótulo continua sendo o rótulo; a
     tela é que pode dizer quando ele está por um fio, em vez de afirmar
     "ganância" em 55,2 com a mesma firmeza que afirmaria em 68.
"""
import pytest

from agent.sentimento import (FAIXAS, MARGEM_DA_FRONTEIRA, classificar,
                              distancia_da_fronteira, faixa)


def _codigo_de(nome: str) -> str:
    """O fonte SEM comentários nem docstrings.

    As duas asserções abaixo olham o texto do arquivo, e a primeira versão
    delas caiu no comentário que CITA o código removido -- alarme falso pelo
    mesmo mecanismo que este repo passou o dia caçando nos validadores: casar
    o token em vez da afirmação. Comentário que explica um defeito antigo não
    é o defeito.
    """
    import ast
    import os
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    arvore = ast.parse(open(os.path.join(raiz, "agent", nome),
                            encoding="utf-8").read())
    # `ast.unparse` devolve só o código: comentários já não existem na árvore,
    # e as docstrings saem como constantes que a busca não confunde.
    for no in ast.walk(arvore):
        if isinstance(no, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                           ast.ClassDef)) and ast.get_docstring(no):
            no.body = no.body[1:] or [ast.Pass()]
    return ast.unparse(arvore)


@pytest.mark.parametrize("score,rotulo", [
    (0.0, "medo extremo"), (25.0, "medo extremo"),
    (25.1, "medo"), (45.0, "medo"),
    (45.1, "neutro"), (54.9, "neutro"), (55.0, "neutro"),
    (55.1, "ganância"), (75.0, "ganância"),
    (75.1, "ganância extrema"), (100.0, "ganância extrema"),
])
def test_o_teto_de_cada_faixa_e_inclusivo(score, rotulo):
    assert classificar(score) == rotulo


def test_o_par_do_incidente():
    """Os dois rótulos estão certos. É a fronteira que é frágil, não a conta."""
    assert classificar(54.9) == "neutro"
    assert classificar(55.2) == "ganância"
    assert faixa(54.9)["naFronteira"] and faixa(55.2)["naFronteira"], \
        "as duas leituras estão perto da borda -- é isso que a tela precisa dizer"


@pytest.mark.parametrize("valor", [None, "55", float("nan"), True, {}, []])
def test_leitura_degenerada_nao_vira_rotulo(valor):
    """`True` entra na lista de propósito: `isinstance(True, int)` é True em
    Python, e um bool viraria 1.0 e sairia como "medo extremo"."""
    assert classificar(valor) == "desconhecido"
    assert distancia_da_fronteira(valor) is None
    assert faixa(valor)["naFronteira"] is False


@pytest.mark.parametrize("score,dist", [
    (54.9, 0.1), (55.2, 0.2), (55.0, 0.0),
    (68.0, 7.0),          # meio da faixa de ganância
    (50.0, 5.0),          # meio da faixa neutra
    (0.0, 25.0), (100.0, 25.0),
])
def test_distancia_ate_a_borda_mais_proxima(score, dist):
    assert distancia_da_fronteira(score) == pytest.approx(dist)


def test_meio_de_faixa_nao_e_marcado():
    """Se a marca aparecesse em metade das leituras ela perderia o sentido."""
    assert faixa(68.0)["naFronteira"] is False
    assert faixa(50.0)["naFronteira"] is False


def test_margem_cobre_a_deriva_observada():
    """A deriva medida entre leituras do mesmo dia foi de 0,3 a 0,7 ponto
    (54,9/55,2 e 57,6/57,3). A margem tem que cobrir isso -- abaixo disso a
    marca não pegaria o caso que a motivou."""
    assert MARGEM_DA_FRONTEIRA >= 0.7


def test_a_tabela_existe_uma_vez_so():
    """`tools.py` e `get_macro.py` tinham cópias idênticas de `classify` até
    26/08/2026. Idênticas HOJE -- a MM50 mostrou no mesmo dia o que acontece
    quando duas cópias da mesma conta divergem sem ninguém notar.

    Assertiva sobre o FONTE, não sobre identidade de objeto: a suíte tem
    testes que inserem `src/agent/` no sys.path, e a partir daí `sentimento` e
    `agent.sentimento` viram dois módulos distintos com funções distintas (ver
    a nota do conftest). `is` passaria ou falharia conforme a ORDEM dos
    testes -- o pior tipo de asserção. O que importa aqui é que nenhum dos
    dois arquivos reimplemente a escada de faixas."""
    for nome in ("tools.py", "get_macro.py"):
        codigo = _codigo_de(nome)
        for teto, rotulo in FAIXAS[:-1]:
            escada = f"<= {teto:.0f}" in codigo or f"<={teto:.0f}" in codigo
            assert not (escada and rotulo in codigo), (
                f"{nome} parece reimplementar a faixa \"{rotulo}\" -- a tabela "
                f"mora em agent/sentimento.py")


def test_as_duas_fontes_concordam_em_todo_o_intervalo():
    """Complemento do teste acima: mesmo que alguém reintroduza uma cópia por
    outro caminho, as duas saídas publicadas têm que bater ponto a ponto."""
    from agent import get_macro, tools
    for s in [i / 2 for i in range(0, 201)]:
        assert get_macro._faixa(s) == tools._faixa_sentimento(s) == faixa(s)


def test_as_faixas_cobrem_o_intervalo_inteiro_sem_buraco():
    tetos = [t for t, _ in FAIXAS]
    assert tetos == sorted(tetos), "faixas fora de ordem furariam a busca linear"
    assert tetos[-1] == 100.0
    assert all(classificar(s) != "desconhecido" for s in range(0, 101))


# ── a terceira cópia da escada, e o defeito que morava nela ────────────────
#
# Achada ao trazer as duas primeiras para `sentimento.py`: `tools.py` montava
# a `interpretation` com uma escada PRÓPRIA de limiares --
#
#     "Pânico ..." if score and score <= 25 else ... else "Euforia ..."
#
# `score and` é FALSO em 0.0, então a cadeia inteira despencava e o score 0 --
# o pânico máximo que o índice sabe expressar -- saía como "Euforia, risco
# máximo de reversão". O sentido exatamente invertido, no extremo em que o
# rótulo mais importa.
#
# Ninguém viu porque 0 nunca apareceu em produção. Continua sendo o valor mais
# provável de aparecer num crash, que é justamente quando se lê esta tela.

from agent.sentimento import INTERPRETACOES, interpretar  # noqa: E402


def test_o_score_zero_nao_e_euforia():
    assert "Pânico" in interpretar(0.0)
    assert "Pânico" in interpretar(0)
    assert "Euforia" not in interpretar(0.0)


@pytest.mark.parametrize("score,trecho", [
    (0.0, "Pânico"), (25.0, "Pânico"),
    (25.1, "Medo"), (45.0, "Medo"),
    (45.1, "neutro"), (55.0, "neutro"),
    (55.1, "ganancioso"), (75.0, "ganancioso"),
    (75.1, "Euforia"), (100.0, "Euforia"),
    (None, "Sem leitura"),
])
def test_interpretacao_segue_o_rotulo(score, trecho):
    assert trecho in interpretar(score)


def test_toda_faixa_tem_interpretacao():
    """Faixa nova sem linha aqui levantaria KeyError DENTRO da coleta, no meio
    de montar o payload do macro."""
    for _, rotulo in FAIXAS:
        assert rotulo in INTERPRETACOES
    assert "desconhecido" in INTERPRETACOES


def test_interpretacao_nao_tem_escada_propria():
    """O ponto do conserto: ela é derivada do RÓTULO. Uma segunda escada de
    limiares é como o zero virou euforia."""
    assert "score and score <=" not in _codigo_de("tools.py")
