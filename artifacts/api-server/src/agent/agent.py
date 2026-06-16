"""
Loop agêntico do analisador de pré-mercado.
Gemini decide quais ferramentas chamar, lê a memória e grava observações.
"""
import datetime
import os
import sys

import google.generativeai as genai

from . import config
from . import memory
from . import tools as t


genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))


def _to_gemini_tools(anthropic_tools: list) -> list:
    """Convert Anthropic tool list (input_schema key) to Gemini function declarations (parameters key)."""
    declarations = []
    for tool in anthropic_tools:
        decl = {
            "name": tool["name"],
            "description": tool["description"],
        }
        schema = tool.get("input_schema", {})
        if schema.get("properties"):
            decl["parameters"] = schema
        declarations.append(decl)
    return [{"function_declarations": declarations}]


def _make_model(model_name: str, tools: list, system_instruction: str, max_tokens: int):
    return genai.GenerativeModel(
        model_name=model_name,
        tools=_to_gemini_tools(tools),
        system_instruction=system_instruction,
        generation_config=genai.GenerationConfig(max_output_tokens=max_tokens),
    )


def _get_text(response) -> str:
    text = ""
    try:
        for part in response.candidates[0].content.parts:
            if hasattr(part, "text") and part.text:
                text += part.text
    except (IndexError, AttributeError):
        pass
    return text


def _get_tool_calls(response) -> list:
    calls = []
    try:
        for part in response.candidates[0].content.parts:
            if hasattr(part, "function_call") and part.function_call.name:
                calls.append(part.function_call)
    except (IndexError, AttributeError):
        pass
    return calls


def _make_fn_part(name: str, result: str):
    return genai.protos.Part(
        function_response=genai.protos.FunctionResponse(
            name=name,
            response={"output": result},
        )
    )


def build_system_prompt() -> str:
    today = datetime.date.today().strftime("%d/%m/%Y")
    return f"""Você é um analista de ações sênior fazendo a leitura pré-mercado do dia {today}.
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

*Grupo A — análise COMPLETA* (ferramentas 5–12):
  • Tickers marcados como "líder" ou "catch_up" pelo detect_sector_contagion (FASE 1 passo 5)
  • Posições da carteira: {", ".join(config.PORTFOLIO_TICKERS)}
Para cada ativo do Grupo A, nesta ordem:
5. get_stock_data — cotação e pré-mercado
6. get_news — manchetes
7. get_technical_indicators — RSI, MACD, Bollinger, médias móveis
8. get_short_interest — exposição short e risco de squeeze
9. get_analyst_ratings — consenso, preço-alvo, upgrades/downgrades
10. get_options_data — put/call ratio e IV
11. Se catalisador (resultados, guidance, contrato): search_edgar_filings + read_filing
12. Compare com a MEMÓRIA DOS DIAS ANTERIORES — o que mudou?
13. Chame save_observation com resumo curto e sentimento.

*Grupo B — cotação RÁPIDA* (só get_stock_data):
  • Todos os demais tickers em cobertura não incluídos no Grupo A
  Registre preço e variação no relatório; não chame outras ferramentas para eles.

**FASE 2.5 — Radar de mercado** (após coletar notícias de TODOS os ativos)
14. Chame check_market_alerts passando todas as manchetes coletadas em headlines_by_ticker.
    Esta ferramenta verifica automaticamente:
    - Contágio de setor: bellwethers (NVDA, AVGO, TSM, SOXX) caindo > 4%
    - Pares asiáticos de memória: SK Hynix / Samsung (sinal antecedente para MU)
    - Gatilhos macro: Payroll, CPI, FOMC, juro de 10 anos
    - Técnico por ativo: RSI sobrecomprado, distância da MM200, proximidade da máxima de 52s,
      spike de volume, gap de abertura
    - Earnings: alerta se resultado estiver em até 7 dias
    - Notícias: downgrade/corte de alvo, padrão sell-the-news
    Use o campo "prompt_block" do resultado para enriquecer sua análise e o relatório final.
    Inclua uma seção "## Radar de Mercado" com os alertas críticos e de atenção encontrados.

**FASE 3 — Gestão de alertas** (execute ao final, depois de analisar todos os ativos)
Com base em tudo que coletou, gerencie os alertas de forma dinâmica:

- **Criar novos alertas** com create_alert quando identificar:
  • Níveis técnicos relevantes não cobertos (suporte, resistência, máxima/mínima de 52 semanas)
  • Catalisador iminente que justifique monitorar um nível específico (ex: resultados, apresentação)
  • Volume anormal ou padrão que sugira breakout iminente
  • Nível de preço citado explicitamente em notícia ou filing como pivô

- **Remover alertas** com delete_alert quando:
  • O nível já foi superado e não faz mais sentido monitorar
  • O contexto mudou (ex: guidance novo tornou o alerta anterior irrelevante)
  • O alerta está duplicado ou desatualizado

- **Critérios de qualidade para alertas**:
  • Não crie mais de 3 alertas novos por execução — prefira qualidade a quantidade
  • Cada alerta deve ter justificativa técnica clara no campo reason
  • Evite duplicar thresholds que já existem (verifique list_alerts antes)
  • threshold_pct é sempre relativo ao fechamento anterior: negativo = queda, positivo = alta

Princípios:
- Seja factual e cite os números. Não dê recomendação de compra/venda; apresente os fatos
  e os riscos para o investidor decidir.
- Sinalize claramente quando algo for incerto ou quando os dados não vierem.
- No relatório final, inclua:
  • "## Sentimento de Mercado" — Fear & Greed score + desempenho dos ETFs de setor + contágio setorial (líderes, confirmações e catch-ups)
  • "## [TICKER] — Análise Completa" para cada ativo, contendo:
    - Cotação e pré-mercado
    - Indicadores técnicos (RSI, MACD, Bollinger)
    - Short interest e risco de squeeze
    - Consenso de analistas e preço-alvo
    - Put/call ratio e IV de opções
    - Notícias e catalisadores
  • "## Radar de Mercado" — alertas críticos e de atenção do check_market_alerts
  • "## Alertas Atualizados" — alertas criados/removidos com justificativa
  • "## Resumo Executivo" — prosa curta com o diagnóstico geral do dia
- Formate a resposta em Markdown com seções por ativo.

=== MEMÓRIA DOS DIAS ANTERIORES ===
{memory.recent_context()}
=== FIM DA MEMÓRIA ==="""


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


def run_premarket(progress_callback=None) -> str:
    """
    Executa a varredura intradiária de pré-mercado.
    Mais rápida que run(): menos ferramentas, menos turnos, output curto.
    """
    system = build_premarket_prompt()
    model = _make_model(config.MODEL_FLASH, t.TOOLS, system, config.MAX_TOKENS_PREMARKET)
    chat = model.start_chat()
    max_turns = min(config.MAX_AGENT_TURNS, 8)

    if progress_callback:
        progress_callback("[Flash] Turno 1...")
    response = chat.send_message("Faça a varredura rápida de pré-mercado intradiário agora.")

    final_text = ""
    for turn in range(max_turns):
        text = _get_text(response)
        if text:
            final_text = text

        tool_calls = _get_tool_calls(response)
        if not tool_calls:
            break

        parts = []
        for fc in tool_calls:
            if progress_callback:
                progress_callback(f"[Flash] {fc.name}")
            result = run_tool(fc.name, dict(fc.args))
            parts.append(_make_fn_part(fc.name, result))

        if progress_callback:
            progress_callback(f"[Flash] Turno {turn + 2}...")
        response = chat.send_message(parts)

    return final_text


# ── Chat mode ────────────────────────────────────────────────────────────────

_CHAT_TOOL_NAMES = {
    "get_stock_data", "get_news", "get_technical_indicators",
    "get_fear_greed_index", "get_sector_performance",
    "get_short_interest", "get_analyst_ratings", "get_options_data",
}
CHAT_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _CHAT_TOOL_NAMES]


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


def run_chat_stream(message: str, history: list) -> None:
    """
    Runs a chat turn, printing STEP: progress lines and a final RESULT:<json>
    line to stdout. Called by agent.run_chat subprocess.
    """
    import json as _json

    system = build_chat_prompt()

    # Convert history from {role, content} format to Gemini {role, parts} format.
    # Gemini uses "model" instead of "assistant" for the AI role.
    gemini_history = []
    for h in history:
        role = "model" if h.get("role") == "assistant" else "user"
        content = h.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")
                for b in content
            )
        gemini_history.append({"role": role, "parts": [str(content)]})

    model = _make_model(config.MODEL_CHAT, CHAT_TOOLS, system, config.MAX_TOKENS_CHAT)
    chat = model.start_chat(history=gemini_history)
    final_text = ""

    print(f"STEP:Turno 1...", flush=True)
    response = chat.send_message(message)

    for turn in range(6):
        text = _get_text(response)
        if text:
            final_text = text

        tool_calls = _get_tool_calls(response)
        if not tool_calls:
            break

        parts = []
        for fc in tool_calls:
            print(f"STEP:{fc.name}", flush=True)
            result = run_tool(fc.name, dict(fc.args))
            parts.append(_make_fn_part(fc.name, result))

        print(f"STEP:Turno {turn + 2}...", flush=True)
        response = chat.send_message(parts)

    print(f"RESULT:{_json.dumps(final_text, ensure_ascii=False)}", flush=True)

    if not history:
        try:
            title_model = genai.GenerativeModel(
                model_name=config.MODEL_CHAT,
                generation_config=genai.GenerationConfig(max_output_tokens=20),
            )
            title_resp = title_model.generate_content(
                "Generate a concise title for this chat conversation. "
                "Max 6 words. Same language as the user message. "
                f"No quotes, no trailing punctuation.\n\nFirst message: {message[:300]}"
            )
            title = _get_text(title_resp).strip()
            if title:
                print(f"TITLE:{_json.dumps(title, ensure_ascii=False)}", flush=True)
        except Exception:
            pass  # title stays as truncated first message — no crash


def run_tool(name: str, args: dict) -> str:
    fn = t.DISPATCH.get(name)
    if not fn:
        return f"Ferramenta desconhecida: {name}"
    try:
        result = fn(**args)
        import json
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        return f"[erro ao executar {name}: {type(e).__name__}: {e}]"


def run(progress_callback=None) -> str:
    """
    Executa o loop agêntico e retorna o texto do relatório final.
    progress_callback(step: str) é chamado opcionalmente a cada passo.
    """
    system = build_system_prompt()
    model = _make_model(config.MODEL_FULL, t.TOOLS, system, config.MAX_TOKENS)
    chat = model.start_chat()

    if progress_callback:
        progress_callback("Turno 1 — consultando Gemini...")
    response = chat.send_message(
        "Faça a análise pré-mercado de hoje para os ativos sob cobertura, "
        "seguindo seu fluxo. Use as ferramentas conforme necessário e registre "
        "as observações do dia ao final."
    )

    final_text = ""
    for turn in range(config.MAX_AGENT_TURNS):
        text = _get_text(response)
        if text:
            final_text = text

        tool_calls = _get_tool_calls(response)
        if not tool_calls:
            break

        tool_results = []
        for fc in tool_calls:
            if progress_callback:
                progress_callback(f"Executando ferramenta: {fc.name}")
            result = run_tool(fc.name, dict(fc.args))
            tool_results.append(_make_fn_part(fc.name, result))

        if progress_callback:
            progress_callback(f"Turno {turn + 2} — consultando Gemini...")
        response = chat.send_message(tool_results)
    else:
        final_text += "\n\n[Aviso: limite de turnos atingido — análise pode estar incompleta.]"

    return final_text
