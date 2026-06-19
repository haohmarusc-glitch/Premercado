"""
Loop agêntico do analisador de pré-mercado.
Suporta múltiplos provedores: anthropic, openai, groq, gemini, kimi.
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


def build_system_prompt_lite() -> str:
    """Prompt reduzido para provedores com limite baixo de tokens (Groq free tier)."""
    today = datetime.date.today().strftime("%d/%m/%Y")
    tickers = ", ".join(config.TICKERS)
    portfolio = ", ".join(config.PORTFOLIO_TICKERS)
    return f"""Você é um analista de ações. Data: {today}. Tickers: {tickers}. Carteira: {portfolio}.

Fluxo: 1) get_fear_greed_index 2) get_sector_performance 3) Para cada ticker da carteira: get_stock_data + get_news 4) save_observation com resumo.

Seja conciso. Formate em Markdown. Cite números."""


def _system_stable_full() -> str:
    """Parte ESTÁVEL do system prompt do modo completo (cacheável).
    Não inclui data nem memória — esses vão no bloco volátil."""
    return f"""Você é um analista de ações sênior fazendo a leitura pré-mercado do dia.
Ativos sob cobertura: {", ".join(config.TICKERS)}.

Seu fluxo completo:

**FASE 1 — Preparação (execute uma vez, no início)**
1. Chame list_alerts (sem filtro) para ver todos os alertas já cadastrados.
2. Chame get_fear_greed_index para capturar o sentimento macro do mercado.
3. Chame get_sector_performance para verificar se o setor de semicondutores (SMH/SOXX) está
   em movimento antes de analisar ativos individuais.
4. Chame get_earnings_calendar para identificar quais ativos têm resultados iminentes (≤ 14 dias).
5. Chame detect_sector_contagion para mapear contágio entre os grupos da cadeia de IA:
   - Memória/Armazenamento (MU, SNDK, WDC)
   - Interconexão/Servidores (SMCI, ALAB, CRDO, ANET)
   - Energia/Refrigeração (VRT)
   - Fundição/Equipamentos (TSM, ASML)
   Os tickers em "catch_up" são candidatos prioritários para análise aprofundada nesta sessão.
   Para captura intradiária: period='1d', interval='5m'.

**FASE 2 — Análise por ativo** (dois grupos; não misture a profundidade)

*Grupo A — análise COMPLETA*:
  • Tickers marcados como "líder" ou "catch_up" pelo detect_sector_contagion (FASE 1 passo 5)
  • Posições da carteira: {", ".join(config.PORTFOLIO_TICKERS)}

Para o Grupo A, colete estas categorias de dados — NÃO finalize um ativo
antes de passar ao próximo; em vez disso, complete uma CATEGORIA para
TODOS os ativos do Grupo A antes de seguir para a próxima categoria:

1. Cotação e pré-mercado — get_stock_data
2. Manchetes — get_news
3. Indicadores técnicos — get_technical_indicators
4. Exposição short — get_short_interest
5. Consenso de analistas — get_analyst_ratings
6. Put/call ratio e IV — get_options_data
7. Se houver catalisador (resultados, guidance, contrato): search_edgar_filings + read_filing
8. Compare cada ativo com a MEMÓRIA DOS DIAS ANTERIORES — o que mudou?
9. Chame save_observation para cada ativo, com resumo curto e sentimento.

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
- No relatório final, inclua seções por ativo em Markdown."""


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
2. get_sector_performance — ETFs de setor (SMH, SOXX, SPY, QQQ)
3. detect_sector_contagion com period='1d', interval='5m' — contágio intradiário
4. Para cada ticker que apareceu como líder ou catch-up no contágio, chame get_stock_data
5. Para os 2–3 tickers com maior movimento, chame get_options_data

**NÃO USE:** search_edgar_filings, read_filing, save_observation, get_news,
get_technical_indicators, get_analyst_ratings, get_short_interest, get_earnings_calendar,
list_alerts, create_alert, delete_alert.

**Formato da saída — "## ⚡ Flash Pré-Mercado {now}":**
- Linha 1: Fear & Greed score e classificação
- Tabela compacta: SMH | SOXX | SPY (preço, variação %)
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
get_fear_greed_index, get_sector_performance, get_short_interest,
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
    "get_fear_greed_index", "get_sector_performance",
    "get_short_interest", "get_analyst_ratings", "get_options_data",
}
CHAT_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _CHAT_TOOL_NAMES]

# Minimal tools for providers with small token limits (Groq free tier ~6k TPM)
_GROQ_TOOL_NAMES = {
    "get_stock_data", "get_news", "get_fear_greed_index",
    "get_sector_performance", "save_observation",
}
GROQ_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _GROQ_TOOL_NAMES]

# Subconjunto para a varredura rápida intradiária. O prompt do premarket já
# proíbe as demais ferramentas; aqui cortamos de fato o schema delas do request,
# economizando ~7k tokens de input por turno (das 17 ferramentas só 5 são usadas).
_PREMARKET_TOOL_NAMES = {
    "get_fear_greed_index", "get_sector_performance",
    "detect_sector_contagion", "get_stock_data", "get_options_data",
}
PREMARKET_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _PREMARKET_TOOL_NAMES]


# ── Run modes ─────────────────────────────────────────────────────────────────

def run(progress_callback=None) -> str:
    client = _get_client()
    system_full_blocks = build_system_prompt_blocks()
    system_lite = build_system_prompt_lite()
    def _system_fn(provider_name: str):
        return system_lite if provider_name == "groq" else system_full_blocks

    def _tools_fn(provider_name: str) -> list:
        return GROQ_TOOLS if provider_name == "groq" else t.TOOLS

    model = client.models["full"]
    messages = [{
        "role": "user",
        "content": (
            "Faça a análise pré-mercado de hoje para os ativos sob cobertura, "
            "seguindo seu fluxo. Use as ferramentas conforme necessário e registre "
            "as observações do dia ao final."
        ),
    }]

    final_text = ""
    for turn in range(config.MAX_AGENT_TURNS):
        if progress_callback:
            progress_callback(f"Turno {turn + 1} — consultando {client.provider_name}...")

        resp = client.create(
            model=model,
            max_tokens=config.MAX_TOKENS,
            system=system_full_blocks,
            tools=t.TOOLS,
            messages=messages,
            system_fn=_system_fn,
            tools_fn=_tools_fn,
        )
        messages.append({"role": "assistant", "content": _resp_to_history_content(resp)})

        from .provider import TextBlock, ToolUseBlock
        for block in resp.content:
            if isinstance(block, TextBlock):
                final_text = block.text

        if resp.stop_reason != "tool_use":
            break

        tool_results = []
        for block in resp.content:
            if isinstance(block, ToolUseBlock):
                if progress_callback:
                    progress_callback(f"Executando ferramenta: {block.name}")
                result = run_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})
    else:
        final_text += "\n\n[Aviso: limite de turnos atingido — análise pode estar incompleta.]"

    return final_text


def run_premarket(progress_callback=None) -> str:
    client = _get_client()
    system = build_premarket_prompt()
    model = client.models["flash"]
    messages = [{"role": "user", "content": "Faça a varredura rápida de pré-mercado intradiário agora."}]

    final_text = ""
    max_turns = min(config.MAX_AGENT_TURNS, 8)

    for turn in range(max_turns):
        if progress_callback:
            progress_callback(f"[Flash] Turno {turn + 1}...")

        resp = client.create(
            model=model,
            max_tokens=config.MAX_TOKENS_PREMARKET,
            system=system,
            tools=PREMARKET_TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": _resp_to_history_content(resp)})

        from .provider import TextBlock, ToolUseBlock
        for block in resp.content:
            if isinstance(block, TextBlock):
                final_text = block.text

        if resp.stop_reason != "tool_use":
            break

        tool_results = []
        for block in resp.content:
            if isinstance(block, ToolUseBlock):
                if progress_callback:
                    progress_callback(f"[Flash] {block.name}")
                result = run_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})

    return final_text


def run_chat_stream(message: str, history: list) -> None:
    client = _get_client()
    system = build_chat_prompt()
    model = client.models["chat"]
    messages = list(history) + [{"role": "user", "content": message}]
    final_text = ""

    for turn in range(6):
        print(f"STEP:Turno {turn + 1} — consultando {client.provider_name}...", flush=True)

        resp = client.create(
            model=model,
            max_tokens=config.MAX_TOKENS_CHAT,
            system=system,
            tools=CHAT_TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": _resp_to_history_content(resp)})

        from .provider import TextBlock, ToolUseBlock
        for block in resp.content:
            if isinstance(block, TextBlock):
                final_text = block.text

        if resp.stop_reason != "tool_use":
            break

        tool_results = []
        for block in resp.content:
            if isinstance(block, ToolUseBlock):
                print(f"STEP:{block.name}", flush=True)
                result = run_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})

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
