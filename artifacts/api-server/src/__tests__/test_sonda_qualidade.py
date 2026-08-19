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
    falhas, _ = sonda._checar(GEMINI, _caso("divergencia_de_preco"))
    assert falhas, "a sonda aprovou o texto que virou tese de compra sobre dado ruim"
    assert any("upside" in f for f in falhas)


def test_aprova_o_texto_que_tratou_a_divergencia_como_problema():
    assert sonda._checar(ANTHROPIC, _caso("divergencia_de_preco"))[0] == []


# ── a sonda não pode ser frouxa nem histérica ───────────────────────────────

def test_texto_que_ignora_a_divergencia_reprova():
    """Escolher um preço em silêncio é o modo de falhar mais discreto: o texto
    fica coerente consigo mesmo e errado em relação ao retrato."""
    mudo = "O DCF indica valor justo de US$ 240,10, um upside de 6,7% sobre o preço atual."
    falhas, _ = sonda._checar(mudo, _caso("divergencia_de_preco"))
    assert any("diverg" in f for f in falhas)


def test_nao_reprova_por_falar_de_dolar():
    """`R$` é proibido, mas 'US$' contém '$' -- regex descuidada reprovaria
    todo texto correto."""
    assert sonda._checar("O papel está em US$ 180,00.", _caso("moeda_em_dolar"))[0] == []


def test_reprova_real_de_verdade():
    assert sonda._checar("O papel está em R$ 950,00.", _caso("moeda_em_dolar"))[0]


def test_ausencia_de_dado_nao_pode_virar_leitura_tecnica():
    inventado = "O RSI de 62 mostra o papel em zona de sobrecompra."
    falhas, _ = sonda._checar(inventado, _caso("campo_ausente_nao_vira_conclusao"))
    assert falhas


def test_dizer_que_o_dado_faltou_passa():
    honesto = "Não há indicadores técnicos disponíveis nesta consulta."
    assert sonda._checar(honesto, _caso("campo_ausente_nao_vira_conclusao"))[0] == []


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


# ── negação não pode virar reprovação ───────────────────────────────────────
#
# Produção 18/08/2026: o gemini reprovou em `campo_ausente` por conter
# "sobrecompr". Só que a MESMA palavra aparece na frase certa -- e o anthropic,
# que passou, escreveu exatamente essa frase num run anterior. A sonda estava
# medindo presença de palavra, não presença de erro.

_TEXTO_CERTO = (
    "Nenhum indicador técnico (RSI, MACD, médias móveis, VWAP, RVOL) foi "
    "calculado para este ticker, o que impede qualquer leitura de momentum ou "
    "sobrecompra/sobrevenda no curto prazo."
)

_TEXTO_ERRADO = "O papel está em sobrecompra clara, com o RSI de 78."


def test_negar_a_leitura_tecnica_nao_reprova():
    falhas, ignorados = sonda._checar(_TEXTO_CERTO, _caso("campo_ausente_nao_vira_conclusao"))
    assert falhas == [], f"reprovou texto correto: {falhas}"
    assert ignorados, "descartou em silêncio -- é assim que uma sonda começa a aprovar tudo"


def test_afirmar_a_leitura_tecnica_reprova():
    """O outro lado: a heurística de negação não pode virar porta dos fundos."""
    falhas, _ = sonda._checar(_TEXTO_ERRADO, _caso("campo_ausente_nao_vira_conclusao"))
    assert falhas


def test_a_falha_carrega_a_frase_inteira():
    """Sem a frase, quem lê o ✗ não sabe se o modelo errou ou se a regex é
    burra -- e um veredito que não se deixa auditar não decide nada."""
    falhas, _ = sonda._checar(_TEXTO_ERRADO, _caso("campo_ausente_nao_vira_conclusao"))
    # Em TODAS as falhas, não só na primeira: este texto viola exige e proíbe
    # ao mesmo tempo, e a ordem entre os dois é detalhe de implementação.
    assert any("RSI de 78" in f for f in falhas)


def test_negacao_de_uma_frase_nao_isenta_a_outra():
    """Duas frases, uma honesta e uma inventada. Se a busca fosse pelo texto
    inteiro em vez de por frase, a primeira absolveria a segunda."""
    misto = _TEXTO_CERTO + " " + _TEXTO_ERRADO
    falhas, _ = sonda._checar(misto, _caso("campo_ausente_nao_vira_conclusao"))
    assert falhas, "a negação de uma frase blindou a afirmação da outra"


# ── o relógio do orçamento ──────────────────────────────────────────────────

def test_a_sonda_zera_o_relogio_entre_casos():
    """`_INICIO` é constante de módulo, fixada no import. Em produção cada
    análise é um processo novo; na sonda são vários casos no mesmo processo.
    Medido: a 'coleta' do 3º caso apareceu como 75,2s dos 135s (a real foi 2s),
    e um 4º caso teria abortado com uma mensagem falsa."""
    import pathlib
    fonte = pathlib.Path(sonda.__file__).read_text(encoding="utf-8")
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    i_zera = next((i for i, l in enumerate(codigo) if "_INICIO = time.monotonic()" in l), -1)
    i_chama = next((i for i, l in enumerate(codigo) if "mod.analisar(" in l), -1)
    assert i_zera >= 0, "o relógio do orçamento não é zerado entre casos"
    assert i_zera < i_chama, "zerar depois da chamada não adianta"


# ── a sonda não pode depender de número vivo ────────────────────────────────
#
# O caso `divergencia_de_preco` cobrava o literal "225" -- o preço de valuation
# que eu tinha fabricado no payload. Só que `analisar()` SOBRESCREVE o
# `_fundamento` recebido pelo que busca ao vivo, então aquele número nunca
# chegava ao modelo.
#
# Ele passou por coincidência enquanto o preço real rondava 225, e reprovou
# quando o mercado andou (220,11 em 19/08/2026). A sonda estava medindo a
# cotação do dia, não a obediência do modelo -- e me levou a "consertar" o
# prompt duas vezes atrás de uma regressão que não existia.

def test_nenhum_caso_cobra_numero_de_mercado():
    """Número vivo no `exige` transforma a sonda em oráculo de cotação: ela
    reprova quando o mercado anda, e o operador vai investigar o modelo."""
    import re
    for caso in sonda.CASOS:
        for padrao, descricao in caso["exige"] + caso["proibe"]:
            # 180 é o preço que o próprio caso injeta via `snapshot` -- esse o
            # teste controla. Qualquer outro número de 3+ dígitos vem do mercado.
            vivos = [n for n in re.findall(r"\\d{3,}", padrao) if n != "180"]
            assert not vivos, (
                f"{caso['nome']}: padrão {padrao!r} cobra o número {vivos} "
                f"({descricao}) -- ele muda com o mercado"
            )


def test_o_caso_da_divergencia_nao_finge_injetar_valuation():
    """`analisar()` sobrescreve `_fundamento` com o que busca ao vivo. Um
    valuation montado no caso é descartado, e deixá-lo ali mentiria sobre o que
    o teste exercita."""
    caso = _caso("divergencia_de_preco")
    assert "_fundamento" not in caso["dados"]
    # a divergência acontece por construção: 180 fabricado contra o preço real
    assert caso["dados"]["snapshot"]["price"] == 180.0


def test_a_analise_realmente_sobrescreve_o_fundamento():
    """A premissa dos dois testes acima. Se um dia `analisar()` passar a
    respeitar o `_fundamento` recebido, eles ficam conservadores demais em vez
    de errados -- mas vale saber."""
    import pathlib
    from agent import analise_rapida_ia as mod
    fonte = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    assert 'dados = {**dados, "_fundamento": fundamento}' in fonte
