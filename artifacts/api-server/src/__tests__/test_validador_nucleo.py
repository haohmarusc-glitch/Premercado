"""
Núcleo dos validadores + a bateria adversarial das auditorias de 26/08/2026.

Quatro auditorias independentes leram `reacao_earnings_validator` e
`analise_rapida_validator` e levantaram alegações de defeito. Cada uma foi
RODADA contra o código antes de qualquer correção: 39 se reproduziram, 1 não
("## Quadro Geral" com capitalização diferente — o auditor não notou que a
prosa já chega em minúsculas). Este arquivo guarda as 39, cada uma no par que
importa:

    DEVE PASSAR  o texto que obedece o SYSTEM e era apontado assim mesmo
    DEVE CAIR    o erro de verdade, que não pode sumir junto com o alarme falso

O par é o ponto. Nas rodadas de 26/08 uma correção de regex reabriu outro
falso positivo duas vezes seguidas, porque só existia a metade "deve cair". Um
validador que aprende apenas a não gritar vira decoração.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_validador_nucleo.py -v
"""
import math

import pytest

from agent.validador_nucleo import (afirmacao_negada, booleano, caminho,
                                    cita_numero, dic, erros, frases, grafias,
                                    linha_de_log, num_finito,
                                    sem_blocos_de_codigo, texto_do_numero,
                                    texto_utilizavel)


# ── fronteira numérica: o defeito que voltou três vezes ─────────────────────
#
# Primeira versão: substring pura -- "1" casava dentro de "1,38", "21", "2026".
# Segunda versão: fronteira só à ESQUERDA -- "180" ainda casava dentro de
# "180,75", e a checagem de divergência concluía que o preço tinha sido
# declarado quando o texto trazia outro número.

@pytest.mark.parametrize("texto,valor,inteiro_ok,esperado", [
    ("US$ 180 e US$ 190",   180.0,  True,  True),
    ("US$ 180,75 apenas",   180.0,  True,  False),
    ("US$ 180.75 apenas",   180.0,  True,  False),
    ("chegou a 1180",       180.0,  True,  False),
    ("reagiu 1,38%",        1.38,   False, True),
    ("reagiu 11,38%",       1.38,   False, False),
    ("reagiu 1,385%",       1.38,   False, False),
    ("reagiu -2,15%",       1.38,   False, False),
    ("média de 21%",        1.38,   False, False),
    ("ocorreu em 2026-08-19", 1.38, False, False),
    ("US$ 1.000,50",        1000.5, True,  True),
    ("US$ 1,000.50",        1000.5, True,  True),
])
def test_fronteira_numerica_nos_dois_lados(texto, valor, inteiro_ok, esperado):
    assert cita_numero(texto, valor, inteiro_ok=inteiro_ok) is esperado


def test_preco_aceita_o_inteiro_e_percentual_nao():
    """As duas checagens NÃO podem compartilhar a mesma régua: "US$ 180" é
    escrita legítima de 180,00, mas arredondar 1,38% para "1" transforma a
    checagem em coringa."""
    assert cita_numero("US$ 180 nos níveis", 180.0, inteiro_ok=True) is True
    assert cita_numero("US$ 180 nos níveis", 180.0, inteiro_ok=False) is False


def test_grafia_de_valor_impossivel_nao_estoura():
    assert grafias(float("nan"), True) == []
    assert grafias(None, True) == []


# ── negação: a primitiva que faltava em quase toda checagem ─────────────────

@pytest.mark.parametrize("frase,alvo", [
    ("a correlação de AVGO não é um padrão estatisticamente relevante",
     r"estatisticamente\s+relevante"),
    ("SMCI e AVGO não são praticamente o mesmo trade", r"mesmo\s+trade"),
    ("AVGO não está descontado", "descontado"),
    ("AVGO deixou de estar descontado", "descontado"),
    ("AVGO está longe de esticado ou descontado", "descontado"),
    ("nem sempre sobe", "sempre"),
    ("o dado não garante reversão", "garante"),
    ("isso não é um sinal confiável", r"sinal\s+confi[áa]vel"),
])
def test_negacao_colada_e_reconhecida(frase, alvo):
    assert afirmacao_negada(frase, alvo) is True


@pytest.mark.parametrize("frase,alvo", [
    # A vírgula corta a cadeia: nega um rótulo e AFIRMA o outro.
    ("AVGO não está neutro, está descontado", "descontado"),
    ("AVGO está esticado, não descontado", "esticado"),
    # Janela ilimitada viraria mordaça — este é o caso que ela deixaria passar.
    ("não recomendo olhar o gráfico, mas é hora de comprar",
     r"hora\s+de\s+comprar"),
    ("AVGO está atualmente descontado", "descontado"),
])
def test_negacao_distante_ou_apos_pontuacao_nao_conta(frase, alvo):
    assert afirmacao_negada(frase, alvo) is False


# ── payload torto vira achado, não exceção ──────────────────────────────────

@pytest.mark.parametrize("valor,esperado", [
    (True, None), (False, None),          # bool viraria 1.0 e entraria em max()
    (float("nan"), None), (float("inf"), None), (float("-inf"), None),
    (None, None), ("N/A", None), ("", None), ({}, None), ([], None),
    (3, 3.0), (0, 0.0), (-1.5, -1.5),
    ("0.462", 0.462), ("1.234,56", 1234.56), ("1,234.56", 1234.56),
    ("US$ 225,01", 225.01), ("12%", 12.0),
])
def test_num_finito_so_devolve_numero_confiavel(valor, esperado):
    r = num_finito(valor)
    assert r == esperado or (r is None and esperado is None)


def test_nan_nao_pode_passar_por_numero():
    """`max([nan]) < 0.70` é False, então a checagem de co-movimento
    simplesmente não apontava — falha silenciosa, a pior espécie."""
    assert num_finito(float("nan")) is None
    assert not math.isnan(num_finito(0.5))


@pytest.mark.parametrize("valor,esperado", [
    ("false", False), ("False", False), ("true", True), ("sim", True),
    (False, False), (True, True), (None, False), (0, False), (1, True),
])
def test_booleano_nao_cai_no_bool_de_string(valor, esperado):
    """`bool("false")` é True em Python, e era assim que uma correlação com
    `corr_sobrevive: "false"` no JSON passava por sobrevivente."""
    assert booleano(valor) is esperado


def test_caminho_atravessa_niveis_tortos_sem_estourar():
    assert caminho({"a": {"b": {"c": 1}}}, "a", "b") == {"c": 1}
    assert caminho({"a": "string"}, "a", "b") == {}
    assert caminho(None, "a", "b") == {}
    assert caminho(["lista"], "a") == {}
    assert dic("nao é dict") == {}


def test_texto_do_numero_nao_quebra_com_string():
    """`f"{pc:.3f}"` com `pc = "0.462"` levantava ValueError DENTRO do
    validador, no meio de reportar um achado."""
    assert texto_do_numero("0.462") == "0.462"
    assert texto_do_numero(0.4623) == "0.462"
    assert texto_do_numero(None) == "None"


# ── resposta não utilizável: o buraco mais perigoso ─────────────────────────

@pytest.mark.parametrize("resposta", [None, "", "   ", "\n\n", 42, [],
                                      "```\n{'a': 1}\n```", "`só um span`"])
def test_resposta_degenerada_nao_e_utilizavel(resposta):
    """Lista vazia de achados é lida por quem chama como "nada destoa" — era
    assim que falha de geração virava texto aprovado."""
    ok, motivo = texto_utilizavel(resposta)
    assert ok is False and motivo


def test_prosa_de_verdade_passa():
    assert texto_utilizavel("O papel abriu em alta e devolveu no fim.")[0] is True


def test_piso_de_tamanho_e_opt_in():
    """Recusar-se a validar um texto curto é recusar-se a achar os erros que
    ele tem, e "curto demais" depende do que foi PEDIDO — conhecimento do
    gerador, não do validador de prosa."""
    curto = "Análise curta."
    assert texto_utilizavel(curto)[0] is True
    assert texto_utilizavel(curto, minimo=200)[0] is False


# ── higiene de bloco de código e frases ─────────────────────────────────────

def test_bloco_e_span_saem_antes_do_lint():
    """Número e palavra-chave dentro de código são dado CITADO — casar ali
    produziria apontamento contra o JSON que o próprio sistema imprimiu."""
    limpo = sem_blocos_de_codigo("antes ```sempre = True``` meio `R$ 10` fim")
    assert "sempre" not in limpo and "R$" not in limpo
    assert "antes" in limpo and "fim" in limpo


def test_frases_ignora_vazias():
    assert frases("Uma. Duas.\n\n\nTrês.") == ["Uma.", "Duas.", "Três."]
    assert frases("") == [] and frases(None) == []


# ── o log tem que existir mesmo quando não há achado ────────────────────────

def test_log_limpo_e_distinguivel_de_nao_rodou():
    """Silêncio e "não rodou" eram indistinguíveis, e essa ambiguidade já
    custou duas rodadas de diagnóstico."""
    assert "limpo" in linha_de_log("analise", [])
    linha = linha_de_log("analise", [
        {"nivel": "ERRO", "codigo": "X", "mensagem": "m"},
        {"nivel": "AVISO", "codigo": "Y", "mensagem": "m"}])
    assert "1 erro(s)" in linha and "1 aviso(s)" in linha and "X" in linha


def test_erros_separa_de_avisos():
    achados = [{"nivel": "ERRO", "codigo": "A"}, {"nivel": "AVISO", "codigo": "B"}]
    assert [a["codigo"] for a in erros(achados)] == ["A"]
