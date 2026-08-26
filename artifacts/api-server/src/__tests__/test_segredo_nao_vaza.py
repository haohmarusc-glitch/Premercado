"""A credencial não atravessa a fronteira do servidor.

Incidente real (ARM, 26/08/2026). A tela de Análise Rápida publicou, no aviso
de bloco faltante da camada fundamental:

    402 Client Error: Payment Required for url:
    https://financialmodelingprep.com/stable/discounted-cash-flow?symbol=ARM&apikey=<A CHAVE>

E não parou na tela: o mesmo texto vai em Salvar relatório, Baixar .md e
Enviar por e-mail.

O detalhe que dói: `mask_sensitive_data` já pegava esse formato desde 02/08,
escrita para o MESMO vazamento (um 403 da FMP mandando a chave pro log de
news_sources.py). Ela só nunca foi chamada no caminho novo.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.security import (  # noqa: E402
    TAMANHO_MINIMO_DO_SEGREDO,
    mask_sensitive_data,
)

CHAVE = "maDU8XrTxUWzC64GFO7RQzViffqZNaOL"

URL_DO_INCIDENTE = (
    "402 Client Error: Payment Required for url: "
    "https://financialmodelingprep.com/stable/discounted-cash-flow"
    f"?symbol=ARM&apikey={CHAVE}"
)


def test_a_url_do_incidente_sai_mascarada():
    saida = mask_sensitive_data(URL_DO_INCIDENTE)
    assert CHAVE not in saida
    # O resto da mensagem tem que sobreviver: "402" e o nome do host são o
    # que faz o operador saber SE vale abrir o log.
    assert "402" in saida and "financialmodelingprep.com" in saida


@pytest.mark.parametrize("param", ["apikey", "api_key", "token",
                                   "access_token", "auth", "key"])
def test_todos_os_nomes_de_parametro_usados_pelos_provedores(param):
    """FMP usa `apikey`, Finnhub `token`, FRED `api_key`. Um formato de fora
    da lista é uma chave publicada."""
    assert CHAVE not in mask_sensitive_data(f"erro em https://x.com/a?s=1&{param}={CHAVE}")


def test_chave_fora_de_url_tambem_some(monkeypatch):
    """A segunda rede: casar pelo VALOR do ambiente pega o formato que
    ninguém previu -- um f-string num log, a chave no corpo de um POST."""
    monkeypatch.setenv("FMP_API_KEY", CHAVE)
    assert CHAVE not in mask_sensitive_data(f"falhou com a chave {CHAVE} crua")


def test_flag_curto_no_ambiente_nao_vira_mascara(monkeypatch):
    """`DEBUG_TOKEN=1` não pode transformar todo "1" do texto em máscara --
    seria uma mensagem ilegível protegendo nada."""
    monkeypatch.setenv("DEBUG_TOKEN", "1")
    texto = "RSI 14 caiu para 43.79 e o volume foi 0.9x da média de 21 pregões"
    assert mask_sensitive_data(texto) == texto


def test_o_piso_de_tamanho_e_o_que_separa_segredo_de_flag(monkeypatch):
    curto = "x" * (TAMANHO_MINIMO_DO_SEGREDO - 1)
    longo = "y" * TAMANHO_MINIMO_DO_SEGREDO
    monkeypatch.setenv("A_KEY", curto)
    monkeypatch.setenv("B_KEY", longo)
    assert curto in mask_sensitive_data(f"vi {curto} aqui")
    assert longo not in mask_sensitive_data(f"vi {longo} aqui")


def test_segredo_prefixo_de_outro_nao_deixa_cauda(monkeypatch):
    """Mascarar o menor primeiro deixaria a cauda do maior na tela -- que é
    justamente o pedaço que identifica a chave."""
    base = "AAAAAAAAAAAAAAAA"
    monkeypatch.setenv("P_KEY", base)
    monkeypatch.setenv("Q_KEY", base + "ZZZZZZZZ")
    saida = mask_sensitive_data(f"erro: {base}ZZZZZZZZ")
    assert "ZZZZZZZZ" not in saida


def test_texto_normal_de_analise_passa_intacto():
    """O falso positivo aqui é uma análise ilegível."""
    texto = ("O preço de US$ 251,06 está 3,64% abaixo da MM20 (US$ 260,55) "
             "e o RSI-14 está em 43,79.")
    assert mask_sensitive_data(texto) == texto


# ── os dois caminhos que publicavam sem mascarar ────────────────────────────

def test_motivo_curto_da_analise_rapida_mascara():
    from agent.analise_rapida_ia import _motivo_curto

    class _HTTPError(Exception):
        pass

    assert CHAVE not in _motivo_curto(_HTTPError(URL_DO_INCIDENTE))


def test_motivo_curto_mascara_ANTES_de_truncar():
    """A chave fica no FIM da URL. Truncar primeiro deixaria um PEDAÇO da
    credencial na tela -- vazamento mais difícil de notar, não proteção."""
    from agent.analise_rapida_ia import _motivo_curto

    class _HTTPError(Exception):
        pass

    # URL curta de propósito: a chave cabe dentro dos 120 caracteres, então
    # só a ordem certa das operações protege.
    curta = f"402 Client Error for url: https://x.io/a?apikey={CHAVE}"
    assert len(curta) < 120, "o teste só vale se a chave couber no corte"
    saida = _motivo_curto(_HTTPError(curta))
    assert CHAVE not in saida
    assert CHAVE[:8] not in saida, "sobrou o prefixo da chave"


def test_falha_de_leitura_mascara():
    """O dicionário vai pro payload do modelo, e o que entra no payload pode
    sair no texto."""
    from agent.tools import _falha_de_leitura

    class _HTTPError(Exception):
        pass

    saida = _falha_de_leitura("o Plano de Saída", _HTTPError(URL_DO_INCIDENTE))
    assert CHAVE not in saida["error"]
