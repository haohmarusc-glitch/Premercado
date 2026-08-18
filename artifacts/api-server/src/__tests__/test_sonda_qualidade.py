"""
A sonda julga certo? Fixado contra os textos REAIS da produção.

A sonda de qualidade (agent/sonda_qualidade.py) chama o LLM e por isso não roda
no CI. Mas o julgamento dela é código puro, e código puro que decide "passou ou
não" precisa de teste -- senão a sonda vira carimbo: aprova tudo e ninguém
percebe, que é pior que não ter sonda nenhuma.

Os textos abaixo são recortes literais das análises de 18/08/2026, o dia em que
o mesmo payload produziu uma resposta certa e uma errada. Eles são o gabarito:
a sonda tem que reprovar o gemini e aprovar o anthropic NESTES textos.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_sonda_qualidade.py -v
"""
import pytest

from agent import sonda_qualidade as sonda


def _caso(nome: str) -> dict:
    return next(c for c in sonda.CASOS if c["nome"] == nome)


# Recortes literais da produção 18/08/2026.
GEMINI = (
    "O modelo de fluxo de caixa descontado (DCF) indica um valor justo de "
    "US$240,10. Este cálculo, baseado em um preço de partida de US$225,01, "
    "sugere um potencial de alta de 6,7% a partir daquele patamar. Comparado "
    "ao preço atual de US$180,00, o DCF aponta um espaço ainda maior para "
    "valorização."
)

ANTHROPIC = (
    "Cabe uma ressalva importante: o preço usado no valuation (US$ 225,01) "
    "difere do preço de US$ 180,00 usado no bloco de níveis. Essa diferença "
    "afeta a leitura de \"quanto upside resta\", já que o DCF e o consenso de "
    "analistas foram calculados a partir de referências de preço distintas."
)


# ── o gabarito ──────────────────────────────────────────────────────────────

def test_reprova_o_texto_que_somou_a_divergencia_ao_upside():
    """O erro concreto: ele CITOU os dois preços -- então passou no 'exige' --
    e mesmo assim concluiu errado. Uma sonda que só cobrasse a citação daria
    aprovado. É por isso que existe o 'proíbe'."""
    falhas = sonda._checar(GEMINI, _caso("divergencia_de_preco"))
    assert falhas, "a sonda aprovou o texto que virou tese de compra sobre dado ruim"
    assert any("upside" in f for f in falhas)


def test_aprova_o_texto_que_tratou_a_divergencia_como_problema():
    assert sonda._checar(ANTHROPIC, _caso("divergencia_de_preco")) == []


# ── a sonda não pode ser frouxa nem histérica ───────────────────────────────

def test_texto_que_ignora_a_divergencia_reprova():
    """Escolher um preço em silêncio é o modo de falhar mais discreto: o texto
    fica coerente consigo mesmo e errado em relação ao retrato."""
    mudo = "O DCF indica valor justo de US$ 240,10, um upside de 6,7% sobre o preço atual."
    falhas = sonda._checar(mudo, _caso("divergencia_de_preco"))
    assert any("diverg" in f for f in falhas)


def test_nao_reprova_por_falar_de_dolar():
    """`R$` é proibido, mas 'US$' contém '$' -- regex descuidada reprovaria
    todo texto correto."""
    assert sonda._checar("O papel está em US$ 180,00.", _caso("moeda_em_dolar")) == []


def test_reprova_real_de_verdade():
    assert sonda._checar("O papel está em R$ 950,00.", _caso("moeda_em_dolar"))


def test_ausencia_de_dado_nao_pode_virar_leitura_tecnica():
    inventado = "O RSI de 62 mostra o papel em zona de sobrecompra."
    falhas = sonda._checar(inventado, _caso("campo_ausente_nao_vira_conclusao"))
    assert falhas


def test_dizer_que_o_dado_faltou_passa():
    honesto = "Não há indicadores técnicos disponíveis nesta consulta."
    assert sonda._checar(honesto, _caso("campo_ausente_nao_vira_conclusao")) == []


# ── higiene ─────────────────────────────────────────────────────────────────

def test_todo_caso_tem_porque():
    """Sonda que reprova sem explicar manda o operador adivinhar se o problema
    é o modelo, o prompt ou o próprio caso."""
    for caso in sonda.CASOS:
        assert caso["porque"].strip(), f"{caso['nome']} sem justificativa"
        assert caso["exige"] or caso["proibe"], f"{caso['nome']} não checa nada"


def test_a_sonda_nao_roda_no_pytest():
    """Ela custa dinheiro e depende de rede. Se algum dia virar test_*.py ou
    ganhar uma função `test_`, o CI passa a gastar por execução."""
    import pathlib
    assert pathlib.Path(sonda.__file__).name == "sonda_qualidade.py"
    assert not [n for n in dir(sonda) if n.startswith("test_")]
