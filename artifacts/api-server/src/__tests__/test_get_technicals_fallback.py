"""
Guarda da integração do get_technicals.py com a cadeia de fallback.

## Por que este teste lê o arquivo em vez de importar o módulo

Historicamente, porque não dava para importar: `get_technicals.py`
redirecionava o fd 1 para o fd 2 NO IMPORT, e trazê-lo para dentro do pytest
sequestrava o stdout da suíte inteira.

Isso mudou -- o redirecionamento agora é guardado por `if __name__ ==
"__main__"`, e os dois testes no fim deste arquivo verificam os dois lados
(importar não mexe no stdout de quem importa; rodar por `-m` continua com o
pipe limpo).

A leitura do texto FICA, por outro motivo: o que se garante aqui é o CALL
SITE -- que este módulo, que pede série ajustada, continue cortando a fonte
externa. É uma propriedade de uma linha, e uma verificação no texto é honesta
para ela. O comportamento da cadeia com `permitir_externa=False` está coberto
de verdade em test_provider_fallback.py, sobre o provider.
"""
import pathlib

import pytest

_SRC_DIR = pathlib.Path(__file__).resolve().parent.parent

_FONTE = (
    pathlib.Path(__file__).resolve().parent.parent / "agent" / "get_technicals.py"
).read_text(encoding="utf-8")


def test_usa_a_cadeia_de_fallback():
    assert "market_data_provider.get_daily_history(" in _FONTE


def test_corta_a_fonte_externa():
    """A série é ajustada; a fonte externa é "as traded". Um split dentro dos
    6 meses viraria degrau de preço e RSI/médias sairiam com um salto que
    nunca existiu — pior que ficar sem indicador, porque o número errado tem
    cara de número certo."""
    assert "permitir_externa=False" in _FONTE


def test_continua_pedindo_serie_ajustada():
    """auto_adjust=True faz parte da chave do hist_cache (o market_alerts usa
    False); trocar isso serviria série bruta para quem espera ajustada."""
    assert "auto_adjust=True" in _FONTE


# ── RSI e base de volume ────────────────────────────────────────────────────
#
# Mesma limitação de import da docstring do módulo: dá para garantir QUAL
# conta está escrita, não rodar a conta. A equivalência numérica entre Wilder
# aqui e em get_trend está coberta em test_technicals_rsi_rvol.py, sobre a
# cópia de tools.py que é importável.

def test_rsi_e_de_wilder_nao_de_cutler():
    """Este script serve /api/technicals — o painel "Técnica" da tela. Com
    `rolling(14).mean()` (Cutler) aqui e Wilder em get_trend, os painéis
    "Tendência" e "Técnica" mostravam RSIs diferentes para o mesmo ticker no
    mesmo instante (NBIS 17/08/2026: 64,6 contra 67,2)."""
    assert "ewm(alpha=1 / 14, min_periods=14)" in _FONTE
    assert "clip(lower=0).rolling(14).mean()" not in _FONTE


def test_base_de_volume_e_mediana():
    """Média de 20 pregões é distorcida por um único dia de earnings (2-3x o
    volume normal), deprimindo rvol/volumeRatio por um mês."""
    assert "volume.rolling(20).median()" in _FONTE
    assert "volume.rolling(20).mean()" not in _FONTE


def test_nao_baixa_mais_o_historico_diario_direto_do_yfinance():
    """Se voltar um download direto da série DIÁRIA, a cadeia foi contornada e
    o módulo perde o cache vencido numa queda do Yahoo."""
    assert "yf.Ticker(ticker).history(period=period" not in _FONTE


def test_intradiario_continua_direto_no_yfinance():
    """A chamada de 5 minutos fica fora da cadeia de propósito: ela só serve
    série DIÁRIA (o hist_cache nem guarda intradiário, e a fonte externa não
    tem esse dado no plano gratuito). Quem consome já trata a ausência."""
    assert 'yf.Ticker(ticker).history(period="1d", interval="5m")' in _FONTE


def test_mantem_a_guarda_de_dados_insuficientes():
    assert 'error": "Dados insuficientes"' in _FONTE


@pytest.mark.parametrize("trecho", [
    "from agent import market_data_provider",
    "import market_data_provider",
])
def test_import_duplo_para_rodar_standalone_e_como_pacote(trecho):
    """O script é spawnado por caminho (sys.path[0] = src/agent) e também
    precisa resolver como membro do pacote — padrão dual do repo."""
    assert trecho in _FONTE


def test_importar_o_modulo_nao_sequestra_o_stdout_de_quem_importou():
    """`os.dup2(2, 1)` vale para o PROCESSO, não para o módulo, e é
    irreversível.

    Este arquivo redireciona o fd 1 para o stderr antes dos imports pesados,
    porque yfinance e pandas imprimem durante o próprio import e sujariam o
    pipe que o Node lê. Isso é certo quando ele É o script.

    Solto no módulo, porém, a mesma linha rodava também quando alguém o
    IMPORTAVA -- e aí o importador perdia o stdout em silêncio, sem nada no
    log. Foi o que impediu o analise_rapida_ia de coletar os painéis no
    próprio processo: ele imprime o JSON final em stdout, e importar
    get_technicals apagava essa saída.

    Agora o redirecionamento é guardado por `if __name__ == "__main__"`, que
    cobre igual o `-m agent.get_technicals` do spawn (rodando por -m, o
    módulo é o __main__)."""
    import os
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-c",
         "from agent import get_technicals\n"
         "print('MARCA_NO_STDOUT')\n"
         "assert callable(get_technicals.technicals)\n"],
        cwd=str(_SRC_DIR), env={**os.environ, "PYTHONPATH": str(_SRC_DIR)},
        capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, f"import falhou:\n{r.stderr[-1500:]}"
    assert "MARCA_NO_STDOUT" in r.stdout, (
        "o import levou o stdout do processo junto -- quem importar este "
        f"módulo perde a própria saída. stdout={r.stdout!r}")


def test_o_contrato_do_script_continua_de_pe():
    """A proteção não pode ter sido só removida: rodando por `-m`, o stdout
    tem de continuar limpo, com o JSON e mais nada. É o pipe que o Node lê."""
    import json
    import os
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "agent.get_technicals"],
        input='{"tickers": ["NVDA"]}',
        cwd=str(_SRC_DIR), env={**os.environ, "PYTHONPATH": str(_SRC_DIR)},
        capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, r.stderr[-1500:]
    # Sem rede o ticker vem com {"error": ...}; o que importa aqui é o pipe
    # estar limpo o bastante para o json.loads do Node passar.
    corpo = json.loads(r.stdout)
    assert "items" in corpo, f"stdout sujo: {r.stdout[:300]!r}"
