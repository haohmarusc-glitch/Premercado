"""
Loop agêntico do analisador de pré-mercado.
Suporta múltiplos provedores: anthropic, openai, gemini, openrouter, kimi.
Configurado via variável AGENT_PROVIDER (padrão: anthropic).
"""
import datetime
import json as _json
import os
import sys

from . import config
from . import memory
from . import tools as t
from .provider import get_client, ProviderClient
from .sector_contagion import SECTOR_GROUPS


def _sector_groups_text() -> str:
    """Lista de grupos setoriais para o prompt, derivada de SECTOR_GROUPS
    (fonte única — editar lá reflete aqui automaticamente)."""
    return "\n".join(
        f"   - {cfg['label']} ({', '.join(cfg['tickers'])})"
        for cfg in SECTOR_GROUPS.values()
    )


def _get_client() -> ProviderClient:
    return get_client()


def _cached_system(text: str) -> list:
    """Anthropic prompt caching — ignorado por outros provedores (passado como system string)."""
    return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]


def _system_blocks(stable_text: str, volatile_text: str = "") -> list:
    """
    Monta o system como blocos para otimizar o prompt caching da Anthropic.

    - `stable_text`: instruções/fluxo que NÃO mudam entre execuções → recebe
      cache_control (este prefixo é reaproveitado e custa ~10% nos cache hits).
    - `volatile_text`: data de hoje + memória dos dias anteriores, que mudam a
      cada run → vai num bloco SEPARADO e SEM cache, depois do estável, para
      não invalidar o cache do prefixo fixo.

    Para provedores não-Anthropic, _anthropic_messages_to_openai() já achata
    esta lista de blocos em uma única string de system.
    """
    blocks = [{"type": "text", "text": stable_text, "cache_control": {"type": "ephemeral"}}]
    if volatile_text:
        blocks.append({"type": "text", "text": volatile_text})
    return blocks


def _cached_tools(tools: list) -> list:
    """Cache hint para Anthropic — outros provedores ignoram o campo extra."""
    if not tools:
        return tools
    cached = list(tools)
    cached[-1] = {**cached[-1], "cache_control": {"type": "ephemeral"}}
    return cached


def _system_stable_full() -> str:
    """Parte ESTÁVEL do system prompt do modo completo (cacheável).
    Não inclui data nem memória — esses vão no bloco volátil."""
    return f"""Você é um analista de ações sênior fazendo a leitura pré-mercado do dia.
Ativos sob cobertura: {", ".join(config.TICKERS)}.

Seu fluxo completo:

**FASE 1 — Preparação (execute uma vez, no início)**
1. Chame list_alerts (sem filtro) para ver todos os alertas já cadastrados.
2. Chame get_fear_greed_index para capturar o sentimento macro do mercado.
3. Chame get_sector_performance para verificar se os setores da cesta estão em movimento
   antes de analisar ativos individuais (semis: SMH/SOXX; saúde: XLV/IBB; amplo: SPY/QQQ).
4. Chame get_earnings_calendar para identificar quais ativos têm resultados iminentes (≤ 14 dias).
5. Chame detect_sector_contagion para mapear contágio entre os grupos setoriais monitorados:
{_sector_groups_text()}
   Os tickers em "catch_up" são candidatos prioritários para análise aprofundada nesta sessão.
   Para captura intradiária: period='1d', interval='5m'.

**FASE 2 — Análise por ativo** (dois grupos; não misture a profundidade)

*Grupo A — análise COMPLETA*:
  • Tickers marcados como "líder" ou "catch_up" pelo detect_sector_contagion (FASE 1 passo 5)
  • Posições da carteira: {", ".join(config.PORTFOLIO_TICKERS)}

**Regra de economia (dias calmos):** se detect_sector_contagion NÃO apontar
nenhum líder/catch_up, restrinja a análise COMPLETA às posições da carteira
que atendam a pelo menos um destes critérios:
  • |variação| ≥ 2% no dia ou no pré-mercado (get_stock_data)
  • resultados em ≤ 14 dias (get_earnings_calendar da FASE 1)
As demais posições da carteira recebem análise REDUZIDA: apenas get_stock_data
+ get_news, e então save_observation baseada na cotação e nas manchetes
(sentimento neutro se nada relevante). NUNCA pule o save_observation de uma
posição da carteira — o que a regra corta são as categorias 3–7, não o registro.

Para o Grupo A, colete estas categorias de dados — NÃO finalize um ativo
antes de passar ao próximo; em vez disso, complete uma CATEGORIA para
TODOS os ativos do Grupo A antes de seguir para a próxima categoria:

1. Cotação e pré-mercado — get_stock_data
2. Manchetes — get_news
3. Indicadores técnicos — get_technical_indicators
4. Padrões de candlestick — detect_candle_patterns
5. Exposição short — get_short_interest
6. Consenso de analistas — get_analyst_ratings
7. Put/call ratio e IV — get_options_data
8. Se houver catalisador (resultados, guidance, contrato): search_edgar_filings + read_filing
9. Cruze candle × notícia: se detect_candle_patterns achou um padrão de
   reversão (Engolfo, Martelo/Enforcado, Estrela da Manhã/Noite etc.) na
   MESMA data ou 1 dia antes/depois de uma manchete relevante do get_news,
   destaque essa coincidência explicitamente no resumo do ativo — é um
   sinal mais forte que técnico ou notícia isolados. Padrão sem notícia
   correspondente (ou vice-versa) tem peso normal, sem destaque especial.
10. Compare cada ativo com a MEMÓRIA DOS DIAS ANTERIORES — o que mudou?
11. Chame save_observation para cada ativo, com resumo curto e sentimento.

OBRIGATÓRIO — agrupe tool calls por categoria, não por ativo:
Se o Grupo A tem N ativos, a categoria 1 (get_stock_data) deve ser UMA
resposta sua com N chamadas de ferramenta juntas — não N respostas
separadas. O mesmo vale para cada categoria seguinte.

Exemplo correto com Grupo A = [MU, NVDA, SMCI]:
  Turno X: você chama get_stock_data(MU) + get_stock_data(NVDA) +
           get_stock_data(SMCI) — as 3 JUNTAS na mesma resposta.
  Turno X+1: você chama get_news(MU) + get_news(NVDA) + get_news(SMCI)
           — de novo as 3 juntas, e assim por diante a cada categoria.
Padrão ERRADO a evitar: get_stock_data(MU), depois get_news(MU), depois
get_technical_indicators(MU) — terminando a MU inteira antes de tocar
em NVDA. Isso multiplica o número de turnos sem necessidade.

Outras regras de eficiência:
- Não repita uma ferramenta para o mesmo ticker se o dado já está no contexto.
- Pare assim que tiver informação suficiente para o relatório; não gaste turnos extras.

*Grupo B — cotação RÁPIDA* (só get_stock_data):
  • Todos os demais tickers em cobertura não incluídos no Grupo A
  Registre preço e variação no relatório; não chame outras ferramentas para eles.
  Agrupe: uma resposta com get_stock_data de todos os tickers do Grupo B juntos.

**FASE 2.5 — Radar de mercado** (após coletar notícias de TODOS os ativos)
14. Chame check_market_alerts passando todas as manchetes coletadas em headlines_by_ticker.

**FASE 3 — Gestão de alertas** (execute ao final)
Com base em tudo que coletou, gerencie os alertas de forma dinâmica:
- Criar novos alertas com create_alert quando identificar níveis técnicos relevantes
- Remover alertas com delete_alert quando o nível já foi superado ou contexto mudou
- Não crie mais de 3 alertas novos por execução

Princípios:
- Seja factual e cite os números.
- No relatório final, inclua seções por ativo em Markdown.
- Tickers com sufixo .SA são da B3 (Brasil), cotados em REAIS (R$) e sem
  pré-mercado — antes das 10h de Brasília, reporte o fechamento anterior e
  sinalize a moeda; não misture R$ com US$ em comparações diretas."""


def _system_volatile() -> str:
    """Parte VOLÁTIL: muda a cada execução, fica num bloco SEM cache."""
    today = datetime.date.today().strftime("%d/%m/%Y")
    return f"""Data de hoje: {today}.

=== MEMÓRIA DOS DIAS ANTERIORES ===
{memory.recent_context()}
=== FIM DA MEMÓRIA ==="""


def build_system_prompt() -> str:
    """Mantida para compatibilidade — concatena estável + volátil como string."""
    return _system_stable_full() + "\n\n" + _system_volatile()


def build_system_prompt_blocks() -> list:
    """System em blocos: fixo cacheado + volátil sem cache (otimiza cache da Anthropic)."""
    return _system_blocks(_system_stable_full(), _system_volatile())


def build_premarket_prompt() -> str:
    today = datetime.date.today().strftime("%d/%m/%Y")
    now = datetime.datetime.now().strftime("%H:%M")
    return f"""Você é um analista de ações fazendo uma VARREDURA RÁPIDA de pré-mercado intradiário às {now} de {today}.
Ativos sob cobertura: {", ".join(config.TICKERS)}.

Esta é uma varredura rápida — NÃO é o relatório diário completo. Seja conciso.

**Fluxo obrigatório (execute na ordem):**
1. get_fear_greed_index — sentimento macro atual
2. get_sector_performance — ETFs de setor (SMH, SOXX, XLV, SPY, QQQ)
3. detect_sector_contagion com period='1d', interval='5m' — contágio intradiário
4. Para cada ticker que apareceu como líder ou catch-up no contágio, chame get_stock_data
5. Para os 2–3 tickers com maior movimento, chame get_options_data

**NÃO USE:** search_edgar_filings, read_filing, save_observation, get_news,
get_technical_indicators, get_analyst_ratings, get_short_interest, get_earnings_calendar,
list_alerts, create_alert, delete_alert.

**Formato da saída — "## ⚡ Flash Pré-Mercado {now}":**
- Linha 1: Fear & Greed score e classificação
- Tabela compacta: SMH | SOXX | XLV | SPY (preço, variação %)
- Contágio detectado: líder → confirmando → catch-up (por grupo)
- Cotações dos tickers em movimento (só os relevantes)
- Put/call ratio e IV dos tickers com opções abertas
- ⚠️ Flag de risco se algo crítico (queda > 5%, IV > 80%, spike de volume)

Limite: no máximo 350 palavras. Seja direto e factual."""


def build_chat_prompt() -> str:
    today = datetime.date.today().strftime("%d/%m/%Y")
    now = datetime.datetime.now().strftime("%H:%M")
    return f"""Você é um analista de ações conversacional em {today} ({now} BRT).
Ativos monitorados: {", ".join(config.TICKERS)}.

Ferramentas disponíveis: get_stock_data, get_news, get_technical_indicators,
detect_candle_patterns, get_fear_greed_index, get_sector_performance, get_short_interest,
get_analyst_ratings, get_options_data.

Regras:
- Responda à pergunta do usuário de forma direta e concisa.
- Use ferramentas apenas quando necessário. Máximo 4 chamadas por resposta.
- NÃO use: save_observation, search_edgar_filings, read_filing, create_alert,
  delete_alert, list_alerts, check_market_alerts, detect_sector_contagion.
- Formate em Markdown. Seja factual; cite números.

=== CONTEXTO RECENTE DO AGENTE ===
{memory.recent_context()}
=== FIM DO CONTEXTO ==="""


def run_tool(name: str, args: dict) -> str:
    fn = t.DISPATCH.get(name)
    if not fn:
        return f"Ferramenta desconhecida: {name}"
    try:
        result = fn(**args)
        return _json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return f"[erro ao executar {name}: {type(e).__name__}: {e}]"


def _resp_to_history_content(resp) -> list:
    """Convert NormalizedResponse to Anthropic-style content list for history."""
    result = []
    from .provider import TextBlock, ToolUseBlock
    for block in resp.content:
        if isinstance(block, TextBlock):
            result.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            result.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
    return result


# ── Chat tool subset ──────────────────────────────────────────────────────────

_CHAT_TOOL_NAMES = {
    "get_stock_data", "get_news", "get_technical_indicators",
    "detect_candle_patterns", "get_fear_greed_index", "get_sector_performance",
    "get_short_interest", "get_analyst_ratings", "get_options_data",
}
CHAT_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _CHAT_TOOL_NAMES]

# Subconjunto para a varredura rápida intradiária. O prompt do premarket já
# proíbe as demais ferramentas; aqui cortamos de fato o schema delas do request,
# economizando ~7k tokens de input por turno (das 17 ferramentas só 5 são usadas).
_PREMARKET_TOOL_NAMES = {
    "get_fear_greed_index", "get_sector_performance",
    "detect_sector_contagion", "get_stock_data", "get_options_data",
}
PREMARKET_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _PREMARKET_TOOL_NAMES]


# Tool subset for portfolio fast mode
_PORTFOLIO_TOOL_NAMES = {
    "get_stock_data", "get_news", "get_technical_indicators",
    "detect_candle_patterns", "get_short_interest", "get_analyst_ratings",
    "save_observation", "get_fear_greed_index",
}
PORTFOLIO_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _PORTFOLIO_TOOL_NAMES]


def _system_stable_portfolio(tickers: list[str]) -> str:
    return f"""Você é um analista de ações fazendo uma análise RÁPIDA focada na carteira.
Ativos da carteira: {", ".join(tickers)}.

**Fluxo obrigatório — siga EXATAMENTE esta sequência sem pular etapas:**
1. get_fear_greed_index — sentimento macro
2. get_stock_data — cotação de TODOS os ativos juntos (N chamadas paralelas)
3. get_news — manchetes de TODOS os ativos juntos
4. get_technical_indicators — indicadores de TODOS os ativos juntos
5. detect_candle_patterns — padrões de vela de TODOS os ativos juntos. Se um
   padrão de reversão coincidir (mesma data ou ±1 dia) com uma manchete do
   get_news, destaque isso no resumo — é sinal mais forte que qualquer um isolado.
6. get_short_interest — short interest de TODOS os ativos juntos
7. get_analyst_ratings — consenso de TODOS os ativos juntos
8. **OBRIGATÓRIO — NÃO PULE:** save_observation para CADA ativo individualmente.
   Você DEVE chamar save_observation {len(tickers)} vezes (uma por ativo: {", ".join(tickers)}).
   Somente após salvar TODAS as observações escreva o relatório final.

**ATENÇÃO:** Não escreva o relatório final antes de completar o passo 8 (save_observation).
Se você pular o passo 8, a análise é considerada incompleta e inválida.

**Regras:**
- Agrupe por categoria, nunca por ativo.
- Seja conciso. Foque em variação do dia, nível técnico mais relevante e risco imediato.
- NÃO use: search_edgar_filings, read_filing, detect_sector_contagion, get_sector_performance,
  get_options_data, get_earnings_calendar, list_alerts, create_alert, delete_alert.

**Formato do relatório final (escreva APÓS salvar todas as observações):**
## ⚡ Carteira — Análise Rápida {{data}}
Para cada ativo: preço atual | variação % | sentimento | 1-2 linhas de análise."""


# ── Agent loop (shared by all run modes) ──────────────────────────────────────

def _agent_loop(
    client,
    model: str,
    system,
    tools: list,
    messages: list,
    max_turns: int,
    max_tokens: int,
    progress_callback=None,
    step_prefix: str = "",
    require_observations: bool = False,
    min_observations: int = 1,
) -> str:
    from .provider import TextBlock, ToolUseBlock

    final_text = ""
    observations_saved = 0
    nudges_left = 2  # cobranças de save_observation antes de aceitar o fim da run
    for turn in range(max_turns):
        if progress_callback:
            label = f"{step_prefix}Turno {turn + 1} — consultando {client.provider_name}..."
            progress_callback(label)

        resp = client.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": _resp_to_history_content(resp)})

        for block in resp.content:
            if isinstance(block, TextBlock):
                final_text = block.text

        if resp.stop_reason != "tool_use":
            # Modelos mais fracos (visto em produção com gemini-2.5-flash-lite)
            # encerram no meio do fluxo sem registrar observações — a run sai
            # "success" mas a memória do agente não avança. Cobra a conclusão
            # antes de aceitar o relatório final. Compara contra min_observations
            # (não só "== 0"): um modelo que salva 1 de 5 tickers e para também
            # deixa a análise incompleta — checar só "zero" deixava esse caso
            # passar em silêncio (bug visto em produção em runs de carteira).
            missing = min_observations - observations_saved
            if require_observations and missing > 0 and nudges_left > 0:
                nudges_left -= 1
                if progress_callback:
                    progress_callback(f"{step_prefix}Cobrando save_observation pendente...")
                messages.append({"role": "user", "content": (
                    f"Você encerrou COM APENAS {observations_saved} de pelo menos "
                    f"{min_observations} save_observation esperadas. A análise só é "
                    "válida após registrar a observação de CADA ativo restante. "
                    "Chame save_observation AGORA para os ativos que faltam "
                    "(resumo curto + sentimento) e só então escreva o relatório final."
                )})
                continue
            break

        tool_results = []
        for block in resp.content:
            if isinstance(block, ToolUseBlock):
                if progress_callback:
                    progress_callback(f"Executando ferramenta: {block.name}")
                result = run_tool(block.name, block.input)
                if block.name == "save_observation":
                    # save_observation NUNCA levanta exceção — em falha (rede,
                    # validação no server, etc.) ela retorna {"saved": False,
                    # "error": ...} normalmente. Checar só a ausência de
                    # "[erro" contava falhas como sucesso e destravava a
                    # cobrança sem nada persistido de fato (bug visto em
                    # produção em 03/07 — run completa, zero observações).
                    saved_ok = False
                    try:
                        saved_ok = _json.loads(result).get("saved") is True
                    except Exception:
                        pass
                    if saved_ok:
                        observations_saved += 1
                    else:
                        print(f"[agent] save_observation falhou: {result}", flush=True)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})
    else:
        final_text += "\n\n[Aviso: limite de turnos atingido — análise pode estar incompleta.]"

    if require_observations and observations_saved < min_observations:
        final_text += (
            f"\n\n[Aviso: apenas {observations_saved} de pelo menos "
            f"{min_observations} observações esperadas foram salvas nesta execução.]"
        )
    return final_text


# ── Run modes ─────────────────────────────────────────────────────────────────

def run(progress_callback=None) -> str:
    client = _get_client()
    return _agent_loop(
        client=client,
        model=client.models["full"],
        system=build_system_prompt_blocks(),
        tools=t.TOOLS,
        messages=[{"role": "user", "content": (
            "Faça a análise pré-mercado de hoje para os ativos sob cobertura, "
            "seguindo seu fluxo. Use as ferramentas conforme necessário e registre "
            "as observações do dia ao final."
        )}],
        max_turns=config.MAX_AGENT_TURNS,
        max_tokens=config.MAX_TOKENS,
        progress_callback=progress_callback,
        require_observations=True,
        # Piso seguro: as posições da carteira SEMPRE recebem save_observation
        # (completa ou reduzida, pela regra de economia) — os líderes de
        # contágio fora da carteira somam mais chamadas, mas sua contagem
        # exata só é conhecida em runtime, então não entram no piso.
        min_observations=len(config.PORTFOLIO_TICKERS),
    )


def run_portfolio(progress_callback=None) -> str:
    env_tickers = os.environ.get("AGENT_PORTFOLIO_TICKERS", "")
    tickers = [tk.strip().upper() for tk in env_tickers.split(",") if tk.strip()] or config.PORTFOLIO_TICKERS
    client = _get_client()
    today = datetime.date.today().strftime("%d/%m/%Y")
    system = _system_stable_portfolio(tickers).replace("{data}", today) + "\n\n" + _system_volatile()
    # Allow more turns and tokens for larger ticker sets (coal=5, ai=8)
    n = len(tickers)
    max_turns = max(20, n * 4)
    max_tokens = max(config.MAX_TOKENS, 8192)
    return _agent_loop(
        client=client,
        model=client.models["flash"],
        system=system,
        tools=PORTFOLIO_TOOLS,
        messages=[{"role": "user", "content": "Faça a análise rápida da carteira agora."}],
        max_turns=max_turns,
        max_tokens=max_tokens,
        progress_callback=progress_callback,
        step_prefix="[Carteira] ",
        require_observations=True,
        min_observations=n,
    )


def run_premarket(progress_callback=None) -> str:
    client = _get_client()
    return _agent_loop(
        client=client,
        model=client.models["flash"],
        system=build_premarket_prompt(),
        tools=PREMARKET_TOOLS,
        messages=[{"role": "user", "content": "Faça a varredura rápida de pré-mercado intradiário agora."}],
        max_turns=min(config.MAX_AGENT_TURNS, 8),
        max_tokens=config.MAX_TOKENS_PREMARKET,
        progress_callback=progress_callback,
        step_prefix="[Flash] ",
    )


def run_chat_stream(message: str, history: list) -> None:
    client = _get_client()
    system = build_chat_prompt()
    model = client.models["chat"]
    messages = list(history) + [{"role": "user", "content": message}]

    def _chat_progress(step: str) -> None:
        print(f"STEP:{step}", flush=True)

    final_text = _agent_loop(
        client=client,
        model=model,
        system=system,
        tools=CHAT_TOOLS,
        messages=messages,
        max_turns=6,
        max_tokens=config.MAX_TOKENS_CHAT,
        progress_callback=_chat_progress,
    )

    print(f"RESULT:{_json.dumps(final_text, ensure_ascii=False)}", flush=True)

    if not history:
        try:
            title_resp = client.create(
                model=model,
                max_tokens=20,
                system="Generate a concise title for this chat conversation. Max 6 words. Same language as the user message. No quotes, no trailing punctuation.",
                tools=[],
                messages=[{"role": "user", "content": f"First message: {message[:300]}"}],
            )
            from .provider import TextBlock
            for block in title_resp.content:
                if isinstance(block, TextBlock) and block.text.strip():
                    print(f"TITLE:{_json.dumps(block.text.strip(), ensure_ascii=False)}", flush=True)
                    break
        except Exception:
            pass
