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


def test_o_volumeratio_inclui_a_barra_de_hoje():
    """O que ninguém tinha escrito, e por isso foi descrito errado duas vezes.

    O único filtro da série é `hist["Close"].notna()`, e ele só derruba a barra
    do dia FORA do pregão (o yfinance devolve Close vazio aí). Com a sessão
    aberta a barra de hoje tem Close, fica na série, e entra na média de 5 --
    então `volumeRatio` CAI ao longo da manhã e sobe conforme o dia enche.

    Isso foi descrito errado no comentário do validador ("volume de hoje contra
    a média de 20 dias INTEIROS") e depois errado de outra forma na correção
    dele ("5 pregões FECHADOS"). Comentário não tem teste; este tem.

    Visto em produção (MU, 29/09/2026, 21 minutos de pregão): volumeRatio 0,78,
    que é a assinatura aritmética de um dia quase vazio dentro de cinco -- e a
    análise com IA leu isso corretamente, contra o que o comentário dizia.
    """
    assert 'hist[hist["Close"].notna()]' in _FONTE
    assert "volume.iloc[-5:].mean()" in _FONTE
    # Nenhum corte que tire a barra de hoje da série de volume. Se alguém
    # quiser volumeRatio só de pregões fechados, é mudança de COMPORTAMENTO e
    # tem de vir com o rótulo da tela junto -- não pode entrar em silêncio.
    for corte in ("volume.iloc[:-1]", "volume[:-1]", "hist.iloc[:-1]"):
        assert corte not in _FONTE, f"{corte} muda o significado de volumeRatio"


def test_a_aritmetica_do_volumeratio_no_comeco_do_pregao():
    """Quatro dias cheios e um quase vazio dão 4/5 = 0,80, não 1,00.

    Fixa o número que o comentário cita, para a explicação e a conta não
    poderem divergir.
    """
    import statistics

    mediana20 = 20_000_000
    cheios = [mediana20] * 4
    hoje_parcial = mediana20 * 0.02          # ~21 minutos de pregão
    media5 = statistics.fmean(cheios + [hoje_parcial])
    assert round(media5 / mediana20, 2) == 0.80

    # E no fim do dia converge para 1,0, que é o que se espera do indicador.
    media5_fim = statistics.fmean(cheios + [mediana20])
    assert round(media5_fim / mediana20, 2) == 1.00


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
