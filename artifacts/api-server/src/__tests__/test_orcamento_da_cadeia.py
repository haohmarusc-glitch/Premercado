"""
A cadeia de fallback anda POR DENTRO de um create() — e precisa de prazo.

Produção 18/08/2026, Análise com IA:

    [provider] anthropic failed: Request timed out or interrupted.
    [provider] trying deepseek...
    analise_rapida_ia: estourou o orçamento de tempo   (stdoutParcial: 0)

O processo foi morto pelo Node aos 150s, sem análise e sem erro legível.

## O erro de contagem

O orçamento do analise_rapida_ia era escrito assim:

    teto_fundamento + 2 x _LLM_TIMEOUT_S <= _ORCAMENTO_TOTAL_S
             25     +      2 x 55        =  135

E havia um teste fixando essa conta. Ele passava. Mas a conta descrevia um
mundo em que UMA chamada é UMA tentativa, e nunca foi esse: `create()`
percorre a cadeia inteira por dentro, sem devolver o controle. Com seis
provedores configurados, uma única chamada pode custar 6 x 55s = 330s contra
135s de orçamento.

Contar tentativas de fora era o erro. Quem tem o prazo é o chamador, e é ele
que precisa passá-lo para dentro -- por isso `definir_orcamento`.

## O default importa tanto quanto o limite

Sem prazo definido, a cadeia percorre tudo, como sempre fez. O agente diário
roda em janela de 10 minutos e prefere gastar tempo a voltar sem resposta;
impor limite a ele "de brinde" seria trocar um bug por outro, do outro lado.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_orcamento_da_cadeia.py -v
"""
import time

import pytest

from agent import provider as prov


@pytest.fixture
def cadeia(monkeypatch):
    """Três provedores, sem rede e sem chaves — mesmo dublê de
    test_pular_provedor.py."""
    monkeypatch.setattr(prov, "_provider_order", lambda: ["anthropic", "gemini", "openrouter"])
    monkeypatch.setattr(prov, "_has_key", lambda _p: True)
    return prov.FallbackClient()


def _fazer_falhar(cadeia, monkeypatch, tentados: list):
    """Todo provedor levanta. Registra a ordem em que foram tentados."""
    class _ClienteQueFalha:
        def __init__(self, nome):
            self.models = {"full": f"modelo-{nome}"}
            self._nome = nome

        def create(self, **_kw):
            tentados.append(self._nome)
            raise RuntimeError(f"{self._nome} indisponível")

    monkeypatch.setattr(cadeia, "_get_client", lambda nome: _ClienteQueFalha(nome))


def _chamar(cadeia):
    return cadeia.create(model="modelo-full", max_tokens=10, system="s",
                         tools=[], messages=[{"role": "user", "content": "oi"}])


# ── sem prazo: comportamento de sempre ──────────────────────────────────────

def test_sem_prazo_a_cadeia_percorre_tudo(cadeia, monkeypatch):
    """O default protege o agente diário, que roda em janela de 10 min e
    prefere gastar tempo a voltar sem resposta."""
    tentados: list[str] = []
    _fazer_falhar(cadeia, monkeypatch, tentados)

    with pytest.raises(RuntimeError, match="All providers exhausted"):
        _chamar(cadeia)

    assert tentados == ["anthropic", "gemini", "openrouter"]


# ── com prazo ───────────────────────────────────────────────────────────────

def test_prazo_vencido_para_a_cadeia_no_primeiro_fallback(cadeia, monkeypatch):
    """O caso da produção: o primeiro provedor consome o orçamento e não há
    tempo para o segundo. Antes, a cadeia tentava assim mesmo e o processo era
    morto de fora, perdendo até o erro."""
    tentados: list[str] = []
    _fazer_falhar(cadeia, monkeypatch, tentados)
    cadeia.definir_orcamento(time.monotonic() - 1, 55)   # prazo já vencido

    with pytest.raises(RuntimeError, match="Orçamento de tempo esgotado"):
        _chamar(cadeia)

    # O PRIMEIRO foi tentado -- o chamador já tinha orçado essa tentativa.
    # Barrá-la seria recusar trabalho que ele decidiu que cabia.
    assert tentados == ["anthropic"]


def test_prazo_folgado_nao_atrapalha(cadeia, monkeypatch):
    tentados: list[str] = []
    _fazer_falhar(cadeia, monkeypatch, tentados)
    cadeia.definir_orcamento(time.monotonic() + 3600, 55)

    with pytest.raises(RuntimeError, match="All providers exhausted"):
        _chamar(cadeia)

    assert tentados == ["anthropic", "gemini", "openrouter"]


def test_o_prazo_conta_o_CUSTO_da_tentativa_nao_so_o_relogio(cadeia, monkeypatch):
    """Ainda restam 30s, mas uma tentativa custa até 55s: começar seria
    garantir o estouro. O corte tem que ser 'cabe outra?', não 'já passou?'."""
    tentados: list[str] = []
    _fazer_falhar(cadeia, monkeypatch, tentados)
    cadeia.definir_orcamento(time.monotonic() + 30, 55)

    with pytest.raises(RuntimeError, match="Orçamento de tempo esgotado"):
        _chamar(cadeia)

    assert tentados == ["anthropic"]


def test_a_mensagem_diz_quanto_faltava_e_quanto_custa(cadeia, monkeypatch, capsys):
    """Erro de orçamento sem os números manda o operador adivinhar se o
    problema é teto curto ou provedor lento."""
    _fazer_falhar(cadeia, monkeypatch, [])
    cadeia.definir_orcamento(time.monotonic() + 10, 55)

    with pytest.raises(RuntimeError) as e:
        _chamar(cadeia)

    assert "55s" in str(e.value)          # custo de uma tentativa
    assert "gemini" in str(e.value)       # quem NÃO foi tentado
    assert "sem orçamento para tentar gemini" in capsys.readouterr().err


def test_custo_negativo_vira_zero(cadeia):
    """Chamador passando lixo não pode virar prazo que nunca cabe."""
    cadeia.definir_orcamento(time.monotonic() + 10, -5)
    assert cadeia._custo_tentativa_s == 0.0
    assert cadeia._cabe_outra_tentativa() is True


# ── a ligação com o script ──────────────────────────────────────────────────

def test_a_analise_rapida_define_o_orcamento():
    """Ter o mecanismo e não usá-lo deixaria tudo como estava."""
    import pathlib
    from agent import analise_rapida_ia as mod

    fonte = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    assert any("definir_orcamento(" in l for l in codigo)
    # e antes do laço que chama create(), senão o prazo chega tarde
    i_def = next(i for i, l in enumerate(codigo) if "definir_orcamento(" in l)
    i_create = next(i for i, l in enumerate(codigo) if "client.create(" in l)
    assert i_def < i_create


# ── o teto vale para TODOS os provedores ────────────────────────────────────
#
# Produção 18/08/2026, com o orçamento do #322 já ativo:
#
#   coleta terminou em 2.4s (teto 25s); orçamento total 135s
#   [provider] anthropic failed: Request timed out or interrupted.
#   [provider] trying deepseek...
#   deepseek/deepseek-v4-pro respondeu em 142.2s (0 chars)
#
# 142 segundos com API_TIMEOUT_SECONDS=55. O teto ia só para o cliente
# Anthropic; o cliente OpenAI-compatível (deepseek, gemini, openrouter, openai,
# kimi) era construído sem `timeout=`, e o default do SDK é 600s.
#
# O estrago não era só a espera. `definir_orcamento` decide se cabe outra
# tentativa usando o custo ESTIMADO de uma chamada -- e com o teto valendo para
# um provedor só, essa estimativa era mentira para os outros cinco. A proteção
# de orçamento autorizava chamadas que não tinha como limitar.

def test_o_timeout_vai_para_os_dois_clientes():
    """A assimetria sobreviveu porque ninguém comparava os dois lados: cada um
    era construído no seu ramo do if, e ler um ramo por vez não revela o que
    falta no outro."""
    import pathlib
    from agent import provider as prov

    fonte = pathlib.Path(prov.__file__).read_text(encoding="utf-8")
    trecho = fonte.split("if self.provider_name == \"anthropic\":", 1)[1][:1200]

    anthropic_tem = "timeout=" in trecho.split("else:", 1)[0]
    openai_tem = "timeout=" in trecho.split("else:", 1)[1]

    assert anthropic_tem, "cliente Anthropic sem timeout"
    assert openai_tem, "cliente OpenAI-compatível sem timeout -- default do SDK é 600s"


def test_o_custo_estimado_do_orcamento_bate_com_o_teto_real():
    """A invariante que liga as duas coisas: `definir_orcamento` recebe o custo
    de uma tentativa, e esse custo só é honesto se o SDK de fato interromper
    naquele tempo. Um lado sem o outro é orçamento sobre estimativa fictícia."""
    from agent import analise_rapida_ia as mod

    # O script passa _LLM_TIMEOUT_S como custo, e é ele que vira
    # API_TIMEOUT_SECONDS -- que agora chega aos dois clientes.
    import os
    assert float(os.environ["API_TIMEOUT_SECONDS"]) == pytest.approx(mod._LLM_TIMEOUT_S)


# ── de quem é o tempo ───────────────────────────────────────────────────────
#
# Produção 18/08/2026, forçando a entrada pelo deepseek:
#
#   [provider] deepseek failed: Request timed out.
#   [provider] switched to anthropic
#   anthropic/claude-sonnet-5 respondeu em 91.5s
#
# O anthropic NÃO levou 91,5s -- levou ~40s, o mesmo do run sem fallback. Os
# outros ~52s foram o deepseek sendo cortado pelo teto de 55s.
#
# A causa é estrutural: quem chama só consegue cronometrar o `create()`, e o
# `create()` percorre a cadeia por dentro. O nome impresso vem de
# `client.provider_name`, que depois da troca já é o provedor NOVO -- então o
# tempo de todo mundo cai no colo de quem respondeu, que é justamente o único
# que não tem culpa. Log assim manda investigar o inocente.

def _fazer_cadeia(cadeia, monkeypatch, roteiro: dict):
    """roteiro: nome -> ('ok'|'falha', segundos_de_espera)."""
    class _Cliente:
        def __init__(self, nome):
            self.models = {"full": f"modelo-{nome}"}
            self._nome = nome

        def create(self, **_kw):
            acao, espera = roteiro[self._nome]
            time.sleep(espera)
            if acao == "falha":
                raise RuntimeError(f"{self._nome} estourou o teto")
            return f"resposta de {self._nome}"

    monkeypatch.setattr(cadeia, "_get_client", lambda nome: _Cliente(nome))


def test_o_tempo_registrado_e_do_vencedor_nao_da_cadeia(cadeia, monkeypatch):
    """O caso da produção em miniatura: o primeiro provedor queima tempo e
    falha, o segundo responde rápido. O número gravado tem que ser o do
    segundo."""
    _fazer_cadeia(cadeia, monkeypatch, {
        "anthropic": ("falha", 0.30),
        "gemini": ("ok", 0.02),
    })

    inicio = time.monotonic()
    assert _chamar(cadeia) == "resposta de gemini"
    cadeia_s = time.monotonic() - inicio

    vencedor_s = cadeia.ultimo_tempo_provedor_s
    assert vencedor_s is not None
    # O gemini respondeu em ~0,02s dentro de uma cadeia de ~0,32s. Gravar o
    # tempo da cadeia aqui é exatamente o bug -- seria 15x o custo real dele.
    assert vencedor_s < 0.15
    assert cadeia_s - vencedor_s > 0.2


def test_sem_fallback_os_dois_numeros_praticamente_coincidem(cadeia, monkeypatch):
    """Quando o primeiro responde não há nada a separar, e o log não deve
    inventar uma distinção que não existe (ver o guarda de 0,5s no script)."""
    _fazer_cadeia(cadeia, monkeypatch, {"anthropic": ("ok", 0.05)})

    inicio = time.monotonic()
    _chamar(cadeia)
    cadeia_s = time.monotonic() - inicio

    assert abs(cadeia_s - cadeia.ultimo_tempo_provedor_s) < 0.05


def test_a_linha_de_falha_diz_quanto_o_provedor_custou(cadeia, monkeypatch, capsys):
    """"deepseek failed" sem número não distingue recusa imediata (chave ruim,
    milissegundos) de teto estourado (55s) -- e as duas pedem investigações
    opostas."""
    _fazer_cadeia(cadeia, monkeypatch, {
        "anthropic": ("falha", 0.10),
        "gemini": ("ok", 0.01),
    })
    _chamar(cadeia)

    assert "anthropic failed after 0.1s" in capsys.readouterr().err


def test_o_relogio_zera_a_cada_chamada(cadeia, monkeypatch):
    """Valor grudado da chamada anterior seria pior que valor nenhum: o log
    pareceria medido e estaria descrevendo outro pedido."""
    _fazer_cadeia(cadeia, monkeypatch, {"anthropic": ("ok", 0.20)})
    _chamar(cadeia)
    primeiro = cadeia.ultimo_tempo_provedor_s

    _fazer_cadeia(cadeia, monkeypatch, {"anthropic": ("ok", 0.01)})
    _chamar(cadeia)

    assert cadeia.ultimo_tempo_provedor_s < primeiro / 2


def test_o_script_imprime_os_dois_relogios():
    """Ter o número e não usá-lo deixaria o log mentindo igual."""
    import pathlib
    from agent import analise_rapida_ia as mod

    fonte = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    assert any("ultimo_tempo_provedor_s" in l for l in codigo)
    assert any("cadeia inteira" in l for l in codigo)


# ── provedor que não converge NESTA tarefa ──────────────────────────────────
#
# O deepseek gasta o max_tokens inteiro raciocinando e nunca chega à resposta.
# Medido quatro vezes em 18-19/08/2026, duas versões de modelo e duas do prompt:
#
#   v4-pro    teto 12.000   142,2s   0 chars   (17.806 chars de raciocínio)
#   v4-flash  teto  6.000    54,2s   0 chars   (esgotou os 6.000 tokens)
#   v4-flash  teto  6.000    52,7s   0 chars   (com o prompt 27% menor)
#
# Num prompt trivial ele responde em 1s -- não é indisponibilidade, é esta
# tarefa. E como ele ocupava um slot do orçamento de 135s para entregar nada, o
# custo não era só o dele: era o tempo que sobrava para o provedor seguinte.

def test_a_analise_exclui_quem_nao_converge(monkeypatch):
    """Fora da tela, DENTRO da cadeia global: o v4-flash é forte em
    tool-calling, formato do agente diário, e esse uso nunca falhou. Excluí-lo
    lá puniria um caminho que funciona por causa de outro que não."""
    import importlib, os
    monkeypatch.delenv("AGENT_PROVIDER_ORDER", raising=False)
    from agent import analise_rapida_ia as mod
    importlib.reload(mod)

    ordem = os.environ["AGENT_PROVIDER_ORDER"].split(",")
    assert "deepseek" not in ordem
    assert "deepseek" in prov._DEFAULT_ORDER      # segue na cadeia global
    assert ordem[0] == "anthropic" and ordem[1] == "gemini"


def test_override_explicito_vence(monkeypatch):
    """É assim que se testa um provedor excluído sem editar código -- foi o
    comando que produziu a medição acima."""
    import importlib, os
    monkeypatch.setenv("AGENT_PROVIDER_ORDER", "deepseek")
    from agent import analise_rapida_ia as mod
    importlib.reload(mod)
    assert os.environ["AGENT_PROVIDER_ORDER"] == "deepseek"


def test_a_exclusao_deriva_da_ordem_unica(monkeypatch):
    """Uma terceira cópia da sequência divergiria das outras duas (provider.py
    e agent-budget.ts) na primeira mudança -- padrão do playbook §10, que já
    mordeu neste repo na #327."""
    import pathlib
    from agent import analise_rapida_ia as mod
    fonte = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    assert any("_ORDEM_PADRAO" in l for l in codigo)
    # e nenhuma lista literal de provedores
    assert not any('"anthropic", "gemini"' in l for l in codigo)
