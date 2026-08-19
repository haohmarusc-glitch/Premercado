"""
Provedor excluído da cadeia por falta de chave não pode sumir em silêncio.

O FallbackClient filtra a ordem por `_has_key`. A filtragem está certa -- não
há o que tentar sem credencial -- mas era INVISÍVEL, e o erro que chega à tela
ficava enganoso.

Incidente de 19/08/2026, na Análise com IA:

    All providers exhausted. Last error: [kimi 429 conta suspensa]
    -- condenados nesta run: openrouter: 404 | openai: 429 | kimi: 429

Lê-se como "tentei tudo que tenho". Anthropic e gemini -- os dois que teriam
respondido -- nem entraram na cadeia, e nada no erro dizia isso. O operador foi
investigar as três contas quebradas, que era o lugar errado.

Import de PACOTE (`from agent import ...`), nunca inserindo `src/agent` no
sys.path: existe um `agent.py` além do pacote `agent/`, e colocar o diretório
do pacote no path faz o nome `agent` resolver para o módulo solto.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_provider_sem_chave.py -v
"""
import pytest

from agent import provider as prov


TODAS_AS_CHAVES = [cfg["api_key_env"] for cfg in prov.PROVIDERS.values()]


@pytest.fixture(autouse=True)
def ambiente_limpo(monkeypatch):
    """Sem nenhuma chave e sem ordem forçada.

    Autouse porque o ambiente REAL de quem roda os testes pode ter chaves
    exportadas -- e aí o teste passaria ou falharia conforme a máquina, que é
    o tipo de teste que não fixa nada.
    """
    for env in TODAS_AS_CHAVES:
        monkeypatch.delenv(env, raising=False)
    monkeypatch.delenv("AGENT_PROVIDER_ORDER", raising=False)
    monkeypatch.delenv("AGENT_PROVIDER", raising=False)


def _com_chaves(monkeypatch, *provedores):
    for p in provedores:
        monkeypatch.setenv(prov.PROVIDERS[p]["api_key_env"], "chave-de-teste")


def test_so_entra_na_cadeia_quem_tem_chave(monkeypatch):
    _com_chaves(monkeypatch, "openai", "kimi")
    c = prov.FallbackClient()
    assert c._order == ["openai", "kimi"]


def test_quem_ficou_de_fora_e_registrado(monkeypatch):
    """O ponto do incidente: a exclusão precisa sobreviver ao construtor."""
    _com_chaves(monkeypatch, "openai", "kimi")
    c = prov.FallbackClient()
    assert "anthropic" in c._sem_chave
    assert "gemini" in c._sem_chave
    assert "openai" not in c._sem_chave


def test_a_nota_nomeia_o_provedor_e_a_variavel(monkeypatch):
    """Nomear só o provedor deixaria o operador adivinhando o nome da env."""
    _com_chaves(monkeypatch, "openai")
    nota = prov.FallbackClient()._nota_sem_chave()
    assert "anthropic" in nota
    assert "ANTHROPIC_API_KEY" in nota
    assert "nunca tentados" in nota


def test_sem_nota_quando_a_cadeia_esta_completa(monkeypatch):
    """Ruído em erro é o começo do erro ignorado."""
    _com_chaves(monkeypatch, *prov.PROVIDERS.keys())
    c = prov.FallbackClient()
    assert c._sem_chave == []
    assert c._nota_sem_chave() == ""


def test_a_nota_nao_cita_quem_tem_chave(monkeypatch):
    """Um provedor com chave que FALHOU pertence aos condenados, não aqui.

    Misturar os dois grupos apagaria justamente a distinção que este erro
    precisa carregar: "quebrou" e "nem foi tentado" pedem investigações
    diferentes.
    """
    _com_chaves(monkeypatch, "openai", "kimi")
    nota = prov.FallbackClient()._nota_sem_chave()
    assert "openai" not in nota
    assert "kimi" not in nota


def test_ordem_forcada_tambem_reporta_quem_falta(monkeypatch):
    """AGENT_PROVIDER_ORDER é o caminho que a Análise com IA usa.

    Ela reescreve a env antes de construir o cliente (para tirar o deepseek),
    então a checagem não pode valer só para a ordem default.
    """
    monkeypatch.setenv("AGENT_PROVIDER_ORDER", "anthropic,gemini,openai")
    _com_chaves(monkeypatch, "openai")
    c = prov.FallbackClient()
    assert c._order == ["openai"]
    assert c._sem_chave == ["anthropic", "gemini"]


def test_nenhuma_chave_continua_falhando_alto(monkeypatch):
    """Sem chave nenhuma não há degradação possível -- tem que levantar."""
    with pytest.raises(RuntimeError, match="No provider API keys found"):
        prov.FallbackClient()


def test_provedor_desconhecido_na_ordem_nao_quebra_a_nota(monkeypatch):
    """AGENT_PROVIDER_ORDER é texto livre vindo do ambiente.

    Um nome digitado errado não pode virar KeyError dentro da montagem da
    mensagem de erro -- falhar ao EXPLICAR a falha é o pior lugar para
    estourar.
    """
    monkeypatch.setenv("AGENT_PROVIDER_ORDER", "anthropic,provedor-que-nao-existe,openai")
    _com_chaves(monkeypatch, "openai")
    c = prov.FallbackClient()
    assert c._order == ["openai"]
    nota = c._nota_sem_chave()
    assert "anthropic" in nota
    assert "provedor-que-nao-existe" not in nota  # sem cfg, não há env para citar
