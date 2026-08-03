"""
Testes de agent.py::_agent_loop — cobre o bug de produção em que blocos
tool_use presentes na resposta ficavam órfãos no histórico sempre que o
stop_reason normalizado não era literalmente "tool_use" (ex.: Anthropic
retornando "max_tokens"/"pause_turn" com tool_use já completo antes do
corte). Isso deixava a mensagem seguinte sem tool_result correspondente,
e a chamada seguinte à API quebrava com 400 invalid_request_error
("tool_use ids were found without tool_result blocks"). Bug visto em
produção com claude-sonnet-5 em 17/07.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_agent_loop.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import json as _json
import time

from agent import agent as agent_module
from agent.provider import NormalizedResponse, TextBlock, ToolUseBlock


class _FakeClient:
    """Devolve uma sequência fixa de respostas, uma por chamada a .create()."""

    provider_name = "anthropic"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.tools_per_call = []

    def create(self, **kwargs):
        # messages é mutado in-place pelo loop (append) -- precisa copiar a
        # lista aqui, senão todas as entradas de self.calls acabam apontando
        # pro mesmo objeto (o estado final), mascarando o snapshot de cada
        # chamada.
        self.calls.append(list(kwargs["messages"]))
        self.tools_per_call.append(kwargs.get("tools"))
        return self._responses.pop(0)


def test_tool_use_block_resolved_even_when_stop_reason_not_tool_use(monkeypatch):
    """Reproduz o bug: resp.content tem um ToolUseBlock mas o stop_reason já
    veio normalizado como "end_turn" (caso real: a Anthropic mandou
    "max_tokens" com um tool_use completo antes do corte). O loop precisa
    gerar o tool_result mesmo assim, senão a próxima mensagem enviada à API
    fica com um tool_use órfão."""
    monkeypatch.setattr(agent_module, "run_tool", lambda name, args: '{"ok": true}')

    responses = [
        NormalizedResponse(
            content=[ToolUseBlock(id="toolu_1", name="get_stock_data", input={})],
            stop_reason="end_turn",  # bug real: stop_reason != "tool_use" mas há tool_use
        ),
        NormalizedResponse(
            content=[TextBlock(text="Relatório final completo " * 10)],
            stop_reason="end_turn",
        ),
    ]
    client = _FakeClient(responses)

    result = agent_module._agent_loop(
        client=client,
        model="claude-sonnet-5",
        system="system prompt",
        tools=[],
        messages=[{"role": "user", "content": "start"}],
        max_turns=5,
        max_tokens=1024,
    )

    assert "Relatório final completo" in result
    # A 2a chamada à API precisa ter recebido um tool_result pro tool_use da
    # 1a resposta, senão a Anthropic rejeita a mensagem com 400.
    second_call_messages = client.calls[1]
    assistant_msg = second_call_messages[-2]
    tool_result_msg = second_call_messages[-1]
    assert assistant_msg["role"] == "assistant"
    tool_use_ids = {b["id"] for b in assistant_msg["content"] if b["type"] == "tool_use"}
    assert tool_use_ids == {"toolu_1"}
    assert tool_result_msg["role"] == "user"
    result_ids = {b["tool_use_id"] for b in tool_result_msg["content"] if b["type"] == "tool_result"}
    assert result_ids == tool_use_ids


def test_normal_tool_use_turn_still_works(monkeypatch):
    """Garante que o caminho comum (stop_reason == "tool_use") não regrediu."""
    monkeypatch.setattr(agent_module, "run_tool", lambda name, args: '{"ok": true}')

    responses = [
        NormalizedResponse(
            content=[ToolUseBlock(id="toolu_a", name="get_news", input={})],
            stop_reason="tool_use",
        ),
        NormalizedResponse(
            content=[TextBlock(text="Relatório final completo " * 10)],
            stop_reason="end_turn",
        ),
    ]
    client = _FakeClient(responses)

    result = agent_module._agent_loop(
        client=client,
        model="claude-sonnet-5",
        system="system prompt",
        tools=[],
        messages=[{"role": "user", "content": "start"}],
        max_turns=5,
        max_tokens=1024,
    )

    assert "Relatório final completo" in result
    assert len(client.calls) == 2


def test_multi_tool_call_turn_runs_in_parallel_and_preserves_result_mapping(monkeypatch):
    """As ferramentas de um turno agora rodam em paralelo (ThreadPoolExecutor)
    pra evitar o timeout de processo em runs com muitos ativos (cada tool call
    de rede levava vários segundos, e eram executadas em série). Este teste
    garante que, mesmo com tempos de resposta diferentes por ferramenta (a
    mais lenta termina por último), cada tool_result acaba pareado com o
    tool_use_id correto -- e que roda de fato em paralelo (tempo total ~=
    max(delays), não soma dos delays)."""
    delays = {"toolu_slow": 0.15, "toolu_fast": 0.01, "toolu_mid": 0.05}

    def fake_run_tool(name, args):
        time.sleep(delays[args["id"]])
        return _json.dumps({"id": args["id"]})

    monkeypatch.setattr(agent_module, "run_tool", fake_run_tool)

    blocks = [
        ToolUseBlock(id="toolu_slow", name="get_options_data", input={"id": "toolu_slow"}),
        ToolUseBlock(id="toolu_fast", name="get_options_data", input={"id": "toolu_fast"}),
        ToolUseBlock(id="toolu_mid", name="get_options_data", input={"id": "toolu_mid"}),
    ]
    responses = [
        NormalizedResponse(content=blocks, stop_reason="tool_use"),
        NormalizedResponse(content=[TextBlock(text="Relatório final completo " * 10)], stop_reason="end_turn"),
    ]
    client = _FakeClient(responses)

    start = time.monotonic()
    result = agent_module._agent_loop(
        client=client,
        model="claude-sonnet-5",
        system="system prompt",
        tools=[],
        messages=[{"role": "user", "content": "start"}],
        max_turns=5,
        max_tokens=1024,
    )
    elapsed = time.monotonic() - start

    assert "Relatório final completo" in result
    # Em série seria >= 0.15+0.01+0.05 = 0.21s; em paralelo fica perto do
    # maior delay (0.15s). Margem generosa pra não flakar em CI lento.
    assert elapsed < 0.19

    second_call_messages = client.calls[1]
    tool_result_msg = second_call_messages[-1]
    by_id = {b["tool_use_id"]: _json.loads(b["content"])["id"] for b in tool_result_msg["content"]}
    assert by_id == {"toolu_slow": "toolu_slow", "toolu_fast": "toolu_fast", "toolu_mid": "toolu_mid"}
    # Ordem no histórico segue a ordem dos tool_use blocks, não a ordem de
    # conclusão (fast terminou primeiro, mas slow ainda deve vir primeiro).
    assert [b["tool_use_id"] for b in tool_result_msg["content"]] == ["toolu_slow", "toolu_fast", "toolu_mid"]


def test_deadline_forces_final_report_without_tools(monkeypatch):
    """Quando o deadline_ts (folga antes do SIGTERM do runner.ts) já passou,
    o loop deve parar de fazer turnos normais e forçar UMA chamada final com
    tools=[] pra garantir que a resposta seja só texto -- sem isso, a run
    corria o risco de ser morta pelo processo pai sem nunca imprimir
    REPORT:, perdendo o progresso e o dinheiro já gasto nas chamadas
    parciais (ver runner.ts, AGENT_SOFT_DEADLINE_MS)."""
    monkeypatch.setattr(agent_module, "run_tool", lambda name, args: '{"ok": true}')

    responses = [
        NormalizedResponse(content=[TextBlock(text="Relatório parcial por tempo esgotado " * 5)], stop_reason="end_turn"),
    ]
    client = _FakeClient(responses)

    result = agent_module._agent_loop(
        client=client,
        model="claude-sonnet-5",
        system="system prompt",
        tools=[{"name": "get_stock_data"}],
        messages=[{"role": "user", "content": "start"}],
        max_turns=20,
        max_tokens=1024,
        deadline_ts=time.time() - 1,  # já passou
    )

    assert "Relatório parcial por tempo esgotado" in result
    # Só uma chamada -- o deadline dispara já na primeira iteração do loop,
    # antes de qualquer turno normal.
    assert len(client.calls) == 1
    # Garantia estrutural do fix: tools=[] torna impossível a resposta vir
    # com tool_use pendente perto do fim (mesmo que `tools` normal do run
    # tivesse ferramentas disponíveis).
    assert client.tools_per_call == [[]]


def test_deadline_none_does_not_affect_normal_flow(monkeypatch):
    """deadline_ts=None (default) não deve mudar o comportamento normal do
    loop -- é o caso de run_premarket/run_chat_stream, que não passam esse
    parâmetro."""
    monkeypatch.setattr(agent_module, "run_tool", lambda name, args: '{"ok": true}')

    responses = [
        NormalizedResponse(content=[TextBlock(text="Relatório final completo " * 10)], stop_reason="end_turn"),
    ]
    client = _FakeClient(responses)

    result = agent_module._agent_loop(
        client=client,
        model="claude-sonnet-5",
        system="system prompt",
        tools=[],
        messages=[{"role": "user", "content": "start"}],
        max_turns=5,
        max_tokens=1024,
        deadline_ts=None,
    )

    assert "Relatório final completo" in result
    assert len(client.calls) == 1


# ── Orçamento das cobranças ───────────────────────────────────────────────────
#
# As duas cobranças do loop tratam falhas DIFERENTES:
#   1. o modelo encerrou sem registrar as save_observation;
#   2. o modelo registrou tudo mas devolveu texto curto demais pra ser o
#      relatório.
# Elas dividiam um contador único, então a primeira podia consumir todo o
# orçamento e deixar a segunda sem nenhuma tentativa.
#
# Visto em produção 03/08 (relatório diário, claude-sonnet-5): o modelo passou
# dois turnos seguidos sem salvar observação (gastou as duas cobranças), no
# turno 10 salvou as nove de uma vez e no turno 11 devolveu texto curto. Sem
# orçamento sobrando pra pedir o relatório, a run -- que já tinha coletado
# tudo, salvo tudo e custado US$ 0,60 -- foi descartada a UM pedido do fim.

_RELATORIO_OK = "# Relatório\n" + ("Análise detalhada do ativo. " * 60)


def _obs_call(n: int) -> NormalizedResponse:
    return NormalizedResponse(
        content=[
            ToolUseBlock(id=f"obs_{i}", name="save_observation", input={"ticker": f"T{i}"})
            for i in range(n)
        ],
        stop_reason="tool_use",
    )


def _texto(t: str) -> NormalizedResponse:
    return NormalizedResponse(content=[TextBlock(text=t)], stop_reason="end_turn")


def _obs_tickers(*tickers: str) -> NormalizedResponse:
    """Igual a _obs_call, mas com os tickers nomeados (pra checar identidade)."""
    return NormalizedResponse(
        content=[
            ToolUseBlock(id=f"obs_{tk}", name="save_observation", input={"ticker": tk})
            for tk in tickers
        ],
        stop_reason="tool_use",
    )


def _rodar(monkeypatch, responses, min_observations=3, required_tickers=None):
    # O loop só conta a observação quando o retorno traz saved=true -- é assim
    # que ele distingue "salvou" de "chamou e falhou".
    monkeypatch.setattr(
        agent_module, "run_tool",
        lambda name, args: '{"saved": true}' if name == "save_observation" else '{"ok": true}',
    )
    client = _FakeClient(responses)
    texto = agent_module._agent_loop(
        client=client,
        model="claude-sonnet-5",
        system="system",
        tools=[],
        messages=[{"role": "user", "content": "start"}],
        max_turns=12,
        max_tokens=1024,
        require_observations=True,
        min_observations=min_observations,
        required_tickers=required_tickers,
    )
    return texto, client


def test_cobrancas_de_observacao_nao_consomem_o_orcamento_do_relatorio(monkeypatch):
    """A sequência exata da produção de 03/08."""
    texto, _ = _rodar(monkeypatch, [
        _texto("Vou continuar."),          # sem observação -> cobrança de obs #1
        _texto("Certo, um momento."),      # sem observação -> cobrança de obs #2
        _obs_call(3),                      # finalmente salva tudo
        _texto("Pronto."),                 # curto -> PRECISA da cobrança de relatório
        _texto(_RELATORIO_OK),             # e aí entrega
    ])
    assert "Análise detalhada" in texto
    assert "Análise incompleta" not in texto


def test_relatorio_curto_ainda_e_descartado_quando_as_cobrancas_acabam(monkeypatch):
    """Separar os orçamentos não pode virar tentativa infinita."""
    texto, client = _rodar(monkeypatch, [
        _obs_call(3),
        _texto("Pronto."),   # curto -> cobrança de relatório #1
        _texto("Pronto."),   # curto -> cobrança de relatório #2
        _texto("Pronto."),   # curto -> orçamento esgotado
    ])
    assert "Análise incompleta" in texto
    assert "curta demais" in texto
    assert len(client.calls) == 4


def test_observacao_pendente_ainda_e_cobrada_duas_vezes(monkeypatch):
    texto, client = _rodar(monkeypatch, [
        _texto("Vou continuar."),
        _texto("Certo."),
        _texto("Certo."),   # terceira sem observação -> orçamento de obs esgotado
    ])
    assert "Análise incompleta" in texto
    assert "registrar as observações pendentes" in texto
    assert len(client.calls) == 3


def test_caminho_feliz_nao_gasta_cobranca_nenhuma(monkeypatch):
    texto, client = _rodar(monkeypatch, [
        _obs_call(3),
        _texto(_RELATORIO_OK),
    ])
    assert "Análise detalhada" in texto
    assert len(client.calls) == 2


def test_piso_do_relatorio_acompanha_o_preflight(monkeypatch):
    """O agente é o último ponto que ainda pode CONSERTAR pedindo de novo.

    Com a régua dele mais frouxa que a do preflight (800 chars), um texto no
    meio do caminho era aceito aqui, não gerava cobrança, e só então o
    preflight bloqueava o e-mail -- run inteira perdida sem nova tentativa.
    """
    assert agent_module._min_report_chars(1) >= agent_module.PREFLIGHT_MIN_CHARS
    assert agent_module._min_report_chars(7) >= agent_module.PREFLIGHT_MIN_CHARS

    quase = "x" * (agent_module.PREFLIGHT_MIN_CHARS - 1)
    texto, _ = _rodar(monkeypatch, [
        _obs_call(3),
        _texto(quase),          # passaria na régua antiga (280), morreria no preflight
        _texto(_RELATORIO_OK),  # cobrado, agora entrega de verdade
    ])
    assert "Análise detalhada" in texto


def test_lista_grande_de_ativos_eleva_o_piso_acima_do_preflight(monkeypatch):
    """Pro caso em que 40 x n passa dos 800: o piso continua sendo o maior."""
    esperado = agent_module.MIN_REPORT_CHARS_PER_TICKER * 40
    assert esperado > agent_module.PREFLIGHT_MIN_CHARS
    assert agent_module._min_report_chars(40) == esperado


def test_nao_cobra_relatorio_de_quem_ainda_nao_registrou_observacao(monkeypatch):
    """Ordem do fluxo: registrar tudo, depois escrever.

    Com observação faltando, pedir "escreva o relatório" contradiz a cobrança
    anterior. Enquanto os dois orçamentos eram um só isso não podia acontecer
    (o de observação esgotava primeiro); ao separá-los, virou um caminho
    possível que precisa continuar fechado.
    """
    texto, client = _rodar(monkeypatch, [
        _texto("Vou continuar."),  # cobrança de obs #1
        _texto("Certo."),          # cobrança de obs #2
        _texto("Certo."),          # obs esgotado -> desiste, NÃO cobra relatório
    ])
    assert "registrar as observações pendentes" in texto
    assert len(client.calls) == 3


# ── Piso por identidade, não por contagem ─────────────────────────────────────
#
# Visto em produção 03/08 (relatório diário): o Grupo A saiu com 7 ativos (6
# posições da carteira + HCC, um líder de contágio de fora dela), o modelo
# salvou as 7 observações, o piso exigia 8, e a cobrança disse apenas "chame
# save_observation para os ativos que faltam" -- sem nomear nenhum. O modelo não
# tinha como saber quais eram (a carteira aparece no prompt, mas ele já
# "achava" que tinha coberto tudo), gastou as duas cobranças em reconhecimentos
# vazios, e a run inteira -- coletada, salva e paga -- virou "Análise
# incompleta".
#
# A contagem também erra pro outro lado: 8 observações cobrindo 7 posições + 1
# ticker de fora satisfaziam o piso com uma posição da carteira sem registro.


def _ultimo_texto_do_usuario(client) -> str:
    """A última mensagem de usuário que o loop enfileirou (a cobrança)."""
    for msg in reversed(client.calls[-1]):
        if msg["role"] == "user" and isinstance(msg["content"], str):
            return msg["content"]
    return ""


def test_cobranca_nomeia_os_ativos_que_faltam(monkeypatch):
    """O ponto todo: a cobrança precisa ser um pedido mecânico, não um enigma."""
    texto, client = _rodar(
        monkeypatch,
        [
            _obs_tickers("NVDA", "SMCI"),
            _texto("Terminei a análise."),   # falta GOOGL -> cobrança nomeada
            _obs_tickers("GOOGL"),
            _texto(_RELATORIO_OK),
        ],
        required_tickers=["NVDA", "SMCI", "GOOGL"],
    )
    cobranca = _ultimo_texto_do_usuario(client)
    assert "GOOGL" in cobranca
    assert "NVDA" not in cobranca and "SMCI" not in cobranca
    assert "Análise detalhada" in texto
    assert "Análise incompleta" not in texto


def test_contagem_certa_com_ativo_errado_nao_satisfaz_o_piso(monkeypatch):
    """3 observações, mas uma é de um ticker de fora da lista exigida."""
    texto, client = _rodar(
        monkeypatch,
        [
            _obs_tickers("NVDA", "SMCI", "HCC"),  # HCC não está na lista exigida
            _texto("Terminei."),   # a contagem (3 de 3) passaria aqui
            _obs_tickers("GOOGL"),
            _texto(_RELATORIO_OK),
        ],
        required_tickers=["NVDA", "SMCI", "GOOGL"],
    )
    assert "GOOGL" in _ultimo_texto_do_usuario(client)
    assert "Análise detalhada" in texto


def test_ticker_faltante_aparece_na_mensagem_final(monkeypatch):
    """Cobranças esgotadas: quem lê o relatório precisa saber o que faltou."""
    texto, _ = _rodar(
        monkeypatch,
        [
            _obs_tickers("NVDA", "SMCI"),
            _texto("Terminei."),   # cobrança #1
            _texto("Certo."),      # cobrança #2
            _texto("Certo."),      # esgotado
        ],
        required_tickers=["NVDA", "SMCI", "GOOGL"],
    )
    assert "Análise incompleta" in texto
    assert "GOOGL" in texto


def test_lista_exigida_define_o_piso_mesmo_com_min_observations_divergente(monkeypatch):
    """required_tickers é a fonte da verdade -- caller e piso não podem divergir."""
    texto, _ = _rodar(
        monkeypatch,
        [
            _obs_tickers("NVDA", "SMCI"),
            _texto(_RELATORIO_OK),
        ],
        min_observations=99,               # valor errado de propósito
        required_tickers=["NVDA", "SMCI"],
    )
    assert "Análise detalhada" in texto
    assert "Aviso: apenas" not in texto


def test_sem_lista_exigida_o_piso_continua_sendo_a_contagem(monkeypatch):
    """Fluxos sem cesta fixa não têm identidade a conferir -- não podem quebrar."""
    texto, _ = _rodar(
        monkeypatch,
        [
            _obs_call(3),
            _texto(_RELATORIO_OK),
        ],
        min_observations=3,
    )
    assert "Análise detalhada" in texto
    assert "Aviso: apenas" not in texto


def test_ticker_em_caixa_baixa_conta_como_registrado(monkeypatch):
    """O modelo às vezes manda 'nvda'; sanitize_ticker normaliza depois, mas o
    loop compara antes -- a comparação precisa ser case-insensitive."""
    texto, _ = _rodar(
        monkeypatch,
        [
            _obs_tickers("nvda", "smci"),
            _texto(_RELATORIO_OK),
        ],
        required_tickers=["NVDA", "SMCI"],
    )
    assert "Análise detalhada" in texto
    assert "Aviso: apenas" not in texto


# ── Diagnóstico de truncamento ────────────────────────────────────────────────
#
# provider.py achata o motivo de parada em "tool_use"/"end_turn", então "o
# modelo terminou" e "eu cortei no meio por limite de tokens" chegavam ao loop
# indistinguíveis. Quando o corte pega o JSON de input de um tool_use, o bloco
# chega com input {} e a ferramenta estoura TypeError de argumento faltando --
# a três camadas de distância da causa.
#
# Visto em produção 03/08: turnos com 9 e 12 tool_use, max_tokens em 4096,
# get_technical_indicators e get_short_interest falhando por falta de `ticker`,
# run terminando com 0 de 8 observações. Nada nos logs ligava uma coisa à outra.


def _cortada(blocks, motivo="max_tokens"):
    return NormalizedResponse(content=blocks, stop_reason="tool_use", raw_stop_reason=motivo)


def test_avisa_quando_a_resposta_foi_cortada(monkeypatch, capsys):
    monkeypatch.setattr(agent_module, "run_tool", lambda name, args: '{"ok": true}')
    responses = [
        _cortada([
            ToolUseBlock(id="a", name="get_stock_data", input={"ticker": "NVDA"}),
            ToolUseBlock(id="b", name="get_short_interest", input={}),  # truncado
        ]),
        _texto(_RELATORIO_OK),
    ]
    agent_module._agent_loop(
        client=_FakeClient(responses), model="m", system="s", tools=[],
        messages=[{"role": "user", "content": "start"}], max_turns=5, max_tokens=4096,
    )
    err = capsys.readouterr().err
    assert "CORTADA por limite de tokens" in err
    assert "max_tokens=4096" in err
    # O ponto do aviso: nomear a chamada que provavelmente perdeu os argumentos.
    assert "get_short_interest" in err
    assert "get_stock_data" not in err.split("input vazio")[-1]


def test_reconhece_o_motivo_da_camada_openai(monkeypatch, capsys):
    """Anthropic diz "max_tokens"; a compat OpenAI diz "length"."""
    monkeypatch.setattr(agent_module, "run_tool", lambda name, args: '{"ok": true}')
    responses = [
        _cortada([ToolUseBlock(id="a", name="get_stock_data", input={})], motivo="length"),
        _texto(_RELATORIO_OK),
    ]
    agent_module._agent_loop(
        client=_FakeClient(responses), model="m", system="s", tools=[],
        messages=[{"role": "user", "content": "start"}], max_turns=5, max_tokens=100,
    )
    assert "CORTADA por limite de tokens" in capsys.readouterr().err


def test_turno_normal_nao_gera_aviso(monkeypatch, capsys):
    """O aviso precisa ser raro pra significar alguma coisa."""
    monkeypatch.setattr(agent_module, "run_tool", lambda name, args: '{"ok": true}')
    responses = [
        NormalizedResponse(
            content=[ToolUseBlock(id="a", name="get_stock_data", input={"ticker": "NVDA"})],
            stop_reason="tool_use", raw_stop_reason="tool_use",
        ),
        _texto(_RELATORIO_OK),
    ]
    agent_module._agent_loop(
        client=_FakeClient(responses), model="m", system="s", tools=[],
        messages=[{"role": "user", "content": "start"}], max_turns=5, max_tokens=4096,
    )
    assert "CORTADA" not in capsys.readouterr().err


def test_resposta_sem_raw_stop_reason_nao_quebra(monkeypatch, capsys):
    """Campo tem default: cliente antigo/falso que não o preenche segue válido."""
    monkeypatch.setattr(agent_module, "run_tool", lambda name, args: '{"ok": true}')
    responses = [
        NormalizedResponse(
            content=[ToolUseBlock(id="a", name="get_stock_data", input={"ticker": "N"})],
            stop_reason="tool_use",
        ),
        _texto(_RELATORIO_OK),
    ]
    agent_module._agent_loop(
        client=_FakeClient(responses), model="m", system="s", tools=[],
        messages=[{"role": "user", "content": "start"}], max_turns=5, max_tokens=4096,
    )
    assert "CORTADA" not in capsys.readouterr().err


def test_max_tokens_do_diario_comporta_o_fan_out():
    """4096 era o teto que cortava turnos de 9-12 chamadas em produção."""
    from agent import config
    assert config.MAX_TOKENS >= 8192
