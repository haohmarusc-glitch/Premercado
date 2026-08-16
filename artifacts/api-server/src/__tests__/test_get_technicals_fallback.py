"""
Guarda da integração do get_technicals.py com a cadeia de fallback.

## Por que este teste lê o arquivo em vez de importar o módulo

`get_technicals.py` redireciona o fd 1 para o fd 2 NO IMPORT, antes de
qualquer biblioteca carregar — é a proteção que garante um pipe limpo para o
Node (ver a docstring do módulo). Importá-lo dentro do pytest sequestraria o
stdout da suíte inteira, e mexer nessa ordem para acomodar teste desfaria a
correção de um bug real.

O comportamento da cadeia com `permitir_externa=False` está coberto de
verdade em test_provider_fallback.py, sobre o provider. O que falta garantir
aqui é só o CALL SITE: que este módulo, que pede série ajustada, continue
cortando a fonte externa. É uma propriedade de uma linha, e uma verificação
no texto do arquivo é honesta para ela — o que não daria para fazer assim
seria testar cálculo de indicador.
"""
import pathlib

import pytest

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
