"""
Provedor que não respondeu não pode sumir do resumo do erro.

Incidente de 19/08/2026, na Análise com IA:

    All providers exhausted. Last error: [kimi 429 conta suspensa]
    -- condenados nesta run: openrouter: 404 | openai: 429 | kimi: 429

Lê-se como "tentei tudo que tenho". Não era: a cadeia desta tela tem CINCO
provedores e o resumo cita três. Anthropic e gemini -- os dois primeiros, e os
dois que teriam respondido -- não aparecem nem como condenados.

Há DOIS jeitos de um provedor sumir assim, e o erro não distinguia nenhum:

  1. Nunca entrou na cadeia. `self._order = [p for p in _provider_order() if
     _has_key(p)]` -- sem chave, o provedor é filtrado sem log nem campo.
  2. Entrou, tentou, falhou e não foi condenado. `_mortos` só recebe falha
     PERMANENTE (modelo inexistente, conta sem saldo); timeout, erro de
     conexão e 500/529 caem para o próximo provedor em silêncio.

Neste incidente foi o caso 2 -- o dump do ambiente mostrou as cinco chaves
presentes. Mas os dois casos produziam a MESMA mensagem enganosa, e por isso
os dois estão cobertos aqui.

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


# ── tentou, falhou, não foi condenado ───────────────────────────────────────
#
# A segunda metade do mesmo incidente. Com as cinco chaves presentes, o erro
# ainda dizia só "condenados nesta run: openrouter | openai | kimi" -- porque
# anthropic e gemini falharam com erro NÃO-permanente e o resumo não os citava.

def test_falha_nao_permanente_aparece_no_resumo(monkeypatch):
    _com_chaves(monkeypatch, "anthropic", "openai")
    c = prov.FallbackClient()
    c._falhas = {"anthropic": "Connection timed out", "openai": "insufficient_quota"}
    c._mortos = {"openai": "insufficient_quota"}
    nota = c._nota_falhas_sem_condenacao()
    assert "anthropic" in nota
    assert "Connection timed out" in nota


def test_condenado_nao_e_repetido_na_outra_lista(monkeypatch):
    """As duas listas apontam para lugares diferentes; duplicar embaralha."""
    _com_chaves(monkeypatch, "anthropic", "openai")
    c = prov.FallbackClient()
    c._falhas = {"anthropic": "timeout", "openai": "insufficient_quota"}
    c._mortos = {"openai": "insufficient_quota"}
    assert "openai" not in c._nota_falhas_sem_condenacao()


def test_sem_nota_quando_todos_falharam_condenados(monkeypatch):
    _com_chaves(monkeypatch, "openai")
    c = prov.FallbackClient()
    c._falhas = {"openai": "insufficient_quota"}
    c._mortos = {"openai": "insufficient_quota"}
    assert c._nota_falhas_sem_condenacao() == ""


def test_sem_nota_quando_ninguem_falhou(monkeypatch):
    _com_chaves(monkeypatch, "openai")
    assert prov.FallbackClient()._nota_falhas_sem_condenacao() == ""
