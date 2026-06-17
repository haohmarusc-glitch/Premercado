"""
Loop agêntico do analisador de pré-mercado.
Gemini decide quais ferramentas chamar, lê a memória e grava observações.
"""
import datetime
import os
import re
import sys
import time

from google import genai
from google.genai import types

from . import config
from . import memory
from . import tools as t


client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))


class QuotaExhaustedError(Exception):
    """Raised when a model's free-tier daily request quota is exhausted."""


def _clean_schema(schema: object) -> object:
    """Recursively remove JSON Schema fields unsupported by Gemini function declarations."""
    _UNSUPPORTED = {"additionalProperties", "default", "$schema", "$id"}
    if isinstance(schema, dict):
        return {k: _clean_schema(v) for k, v in schema.items() if k not in _UNSUPPORTED}
    if isinstance(schema, list):
        return [_clean_schema(item) for item in schema]
    return schema


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
            decl["parameters"] = _clean_schema(schema)
        declarations.append(decl)
    return [{"function_declarations": declarations}]


_GROQ_SAFE = {"type", "description", "properties", "required", "items", "enum",
              "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"}


def _clean_schema_openai(schema: object, safe: frozenset | set = _GROQ_SAFE) -> object:
    """Strip fields unsupported by OpenAI-compatible APIs (Groq)."""
    if isinstance(schema, dict):
        cleaned = {}
        for k, v in schema.items():
            if k in safe:
                cleaned[k] = _clean_schema_openai(v, safe)
        if "properties" in cleaned and "type" not in cleaned:
            cleaned["type"] = "object"
        return cleaned
    if isinstance(schema, list):
        return [_clean_schema_openai(item, safe) for item in schema]
    return schema


def _to_openai_tools(anthropic_tools: list) -> list:
    """Convert Anthropic tool list to OpenAI function calling format (Groq)."""
    result = []
    for tool in anthropic_tools:
        schema = tool.get("input_schema", {})
        func: dict = {"name": tool["name"], "description": tool["description"]}
        params = _clean_schema_openai(schema)
        if "type" not in params:
            params["type"] = "object"
        if "properties" not in params:
            params["properties"] = {}
        func["parameters"] = params
        result.append({"type": "function", "function": func})
    return result


def _to_kimi_tools(anthropic_tools: list) -> list:
    """Build minimal tool schemas for Kimi (Moonshot) from scratch.
    Kimi's 'moonshot flavored json' only accepts type/description/enum/items/properties/required
    and rejects empty arrays/objects. Built explicitly to avoid recursive-cleaning edge cases.
    """
    result = []
    for tool in anthropic_tools:
        raw = tool.get("input_schema", {})
        raw_props = raw.get("properties", {})
        required = [r for r in raw.get("required", []) if r]

        props: dict = {}
        for pname, ps in raw_props.items():
            p: dict = {}
            if "type" in ps:
                p["type"] = ps["type"]
            if "description" in ps:
                p["description"] = ps["description"]
            if "enum" in ps:
                p["enum"] = ps["enum"]
            if ps.get("type") == "array" and isinstance(ps.get("items"), dict):
                p["items"] = {"type": ps["items"].get("type", "string")}
            props[pname] = p

        params: dict = {"type": "object"}
        if props:
            params["properties"] = props
        if required:
            params["required"] = required

        result.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": params,
            },
        })
    return result


def _make_config(tools: list, system_instruction: str, max_tokens: int) -> types.GenerateContentConfig:
    return types.GenerateContentConfig(
        tools=_to_gemini_tools(tools),
        system_instruction=system_instruction,
        max_output_tokens=max_tokens,
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
            fc = getattr(part, "function_call", None)
            if fc and fc.name:
                calls.append(fc)
    except (IndexError, AttributeError):
        pass
    return calls


def _send(chat, message, progress_callback=None, step_label: str = "") -> object:
    """Send a chat message, retrying automatically on 429 / 503 errors."""
    from google.genai.errors import ClientError, ServerError
    max_retries = int(os.environ.get("GEMINI_MAX_RETRIES", "5"))
    for attempt in range(max_retries + 1):
        try:
            return chat.send_message(message)
        except ClientError as e:
            err_str = str(e)
            sc = getattr(e, "status_code", None)
            is_429 = sc == 429 or str(sc) == "429" or err_str.startswith("429") or "RESOURCE_EXHAUSTED" in err_str
            if not is_429 or attempt == max_retries:
                raise
            # Daily quota cannot be recovered by waiting — signal for model fallback
            if "PerDay" in err_str or "per_day" in err_str.lower():
                raise QuotaExhaustedError(err_str) from e
            m = re.search(r"retry in (\d+(?:\.\d+)?)s", err_str)
            wait = min(float(m.group(1)) + 5 if m else 65.0, 130.0)
            if progress_callback:
                progress_callback(f"{step_label}Rate limit — aguardando {int(wait)}s...")
            time.sleep(wait)
        except ServerError as e:
            err_str_s = str(e)
            sc_s = getattr(e, "status_code", None)
            is_503 = sc_s == 503 or str(sc_s) == "503" or err_str_s.startswith("503") or "UNAVAILABLE" in err_str_s
            if not is_503:
                raise
            if attempt == max_retries:
                # Persistent 503 — trigger model fallback the same way quota exhaustion does
                raise QuotaExhaustedError(f"503 persistente após {max_retries} tentativas: {e}") from e
            wait = 15.0 * (attempt + 1)
            if progress_callback:
                progress_callback(f"{step_label}Servidor sobrecarregado — aguardando {int(wait)}s...")
            time.sleep(wait)


def _openai_compat_send(oc_client, model: str, messages: list, tools: list, max_tokens: int,
                        provider_name: str = "Provider", max_retries: int = 1,
                        progress_callback=None, step_label: str = "") -> object:
    """Send a request to any OpenAI-compatible API, retrying on rate limits."""
    try:
        from openai import RateLimitError
    except ImportError:
        raise RuntimeError("openai package not installed; run: uv pip install openai")
    for attempt in range(max_retries + 1):
        try:
            return oc_client.chat.completions.create(
                model=model,
                messages=messages,
                tools=tools if tools else None,
                max_tokens=max_tokens,
            )
        except RateLimitError as e:
            if attempt == max_retries:
                raise QuotaExhaustedError(str(e)) from e
            wait = 60.0 * (attempt + 1)
            if progress_callback:
                progress_callback(f"{step_label}{provider_name} rate limit — aguardando {int(wait)}s...")
            time.sleep(wait)

def _execute_tools(tool_calls: list, progress_callback=None, prefix="") -> list:
    """Execute tool calls and return a list of function-response Parts."""
    parts = []
    for fc in tool_calls:
        if progress_callback:
            progress_callback(f"{prefix}{fc.name}")
        result = run_tool(fc.name, dict(fc.args))
        parts.append(types.Part.from_function_response(
            name=fc.name,
            response={"output": result},
        ))
    return parts


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


def build_system_prompt_compact() -> str:
    """Shorter system prompt for fallback providers with tight token limits."""
    today = datetime.date.today().strftime("%d/%m/%Y")
    return f"""Você é um analista de ações fazendo leitura pré-mercado do dia {today}.
Ativos: {", ".join(config.TICKERS)}. Carteira: {", ".join(config.PORTFOLIO_TICKERS)}.

Para cada ativo da carteira: use get_stock_data, get_news, get_technical_indicators.
Para o mercado geral: use get_fear_greed_index e get_sector_performance.

Seja factual, cite números, formate em Markdown com seção por ativo.
Inclua: cotação, variação pré-mercado, principais notícias, RSI e MACD se disponíveis.
Termine com "## Resumo Executivo" com diagnóstico geral."""


# Minimal tool subset for fallback providers (avoids 413 token-too-large errors)
_FALLBACK_TOOL_NAMES = {
    "get_stock_data", "get_news", "get_technical_indicators",
    "get_fear_greed_index", "get_sector_performance",
}
FALLBACK_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _FALLBACK_TOOL_NAMES]


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


def _run_premarket_impl(model: str, progress_callback=None) -> str:
    system = build_premarket_prompt()
    cfg = _make_config(t.TOOLS, system, config.MAX_TOKENS_PREMARKET)
    chat = client.chats.create(model=model, config=cfg)
    max_turns = min(config.MAX_AGENT_TURNS, 8)

    if progress_callback:
        progress_callback(f"[Flash] Turno 1... ({model})")
    response = _send(chat, "Faça a varredura rápida de pré-mercado intradiário agora.", progress_callback, "[Flash] ")

    final_text = ""
    for turn in range(max_turns):
        text = _get_text(response)
        if text:
            final_text = text

        tool_calls = _get_tool_calls(response)
        if not tool_calls:
            break

        fn_parts = _execute_tools(tool_calls, progress_callback, prefix="[Flash] ")

        if progress_callback:
            progress_callback(f"[Flash] Turno {turn + 2}...")
        response = _send(chat, fn_parts, progress_callback, "[Flash] ")

    return final_text


def run_premarket(progress_callback=None) -> str:
    """
    Executa a varredura intradiária de pré-mercado.
    Ordem de fallback: modelos Gemini → Groq → Kimi.
    """
    gemini_models = list(dict.fromkeys([config.MODEL_FLASH] + config.MODEL_FALLBACKS))
    for model in gemini_models:
        try:
            return _run_premarket_impl(model, progress_callback)
        except QuotaExhaustedError:
            if progress_callback:
                progress_callback(f"Cota diária esgotada para {model} — tentando próximo...")
        except Exception as e:
            if progress_callback:
                progress_callback(f"Modelo {model} falhou ({type(e).__name__}) — tentando próximo...")

    result = _try_openai_compat_providers(
        system=build_system_prompt_compact(),
        tools=FALLBACK_TOOLS,
        max_tokens=min(config.MAX_TOKENS_PREMARKET, 512),
        initial_message="Faça uma varredura rápida de pré-mercado: sentimento, ETFs de setor e cotações dos principais ativos.",
        max_turns=5,
        progress_callback=progress_callback,
        step_prefix="[Flash] ",
    )
    if result is not None:
        return result

    raise RuntimeError(
        "Todos os modelos falharam ou esgotaram cota. Tente novamente após meia-noite UTC."
    )


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
    Tries fallback models if the primary model's daily quota is exhausted.
    """
    import json as _json

    system = build_chat_prompt()

    # Convert history from {role, content} format to google-genai Content format.
    # google-genai uses "model" instead of "assistant" for the AI role.
    gemini_history = []
    for h in history:
        role = "model" if h.get("role") == "assistant" else "user"
        content = h.get("content", "")
        if isinstance(content, list):
            content = " ".join(
                b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "")
                for b in content
            )
        gemini_history.append({"role": role, "parts": [{"text": str(content)}]})

    models = list(dict.fromkeys([config.MODEL_CHAT] + config.MODEL_FALLBACKS))
    final_text = ""
    chat_model_used = models[0]

    for model in models:
        chat_model_used = model
        cfg = _make_config(CHAT_TOOLS, system, config.MAX_TOKENS_CHAT)
        chat = client.chats.create(
            model=model,
            config=cfg,
            history=gemini_history if gemini_history else None,
        )
        final_text = ""
        try:
            print("STEP:Turno 1...", flush=True)
            response = _send(chat, message)

            for turn in range(6):
                text = _get_text(response)
                if text:
                    final_text = text

                tool_calls = _get_tool_calls(response)
                if not tool_calls:
                    break

                fn_parts = []
                for fc in tool_calls:
                    print(f"STEP:{fc.name}", flush=True)
                    result = run_tool(fc.name, dict(fc.args))
                    fn_parts.append(types.Part.from_function_response(
                        name=fc.name,
                        response={"output": result},
                    ))

                print(f"STEP:Turno {turn + 2}...", flush=True)
                response = _send(chat, fn_parts)

            break  # success — exit model loop
        except QuotaExhaustedError:
            print(f"STEP:Cota diária esgotada para {model} — tentando próximo modelo...", flush=True)
            continue
        except Exception as e:
            _es = str(e)
            if "429" in _es or "RESOURCE_EXHAUSTED" in _es or "PerDay" in _es or "quota" in _es.lower():
                print(f"STEP:Cota/limite para {model} — tentando próximo modelo...", flush=True)
                continue
            raise

    # Groq → Kimi fallback if all Gemini models exhausted
    if not final_text:
        import json as _json2
        try:
            from openai import OpenAI
        except ImportError:
            OpenAI = None  # type: ignore

        providers_chat = []
        if OpenAI and os.environ.get("GROQ_API_KEY"):
            providers_chat.append(("Groq", config.GROQ_BASE_URL, os.environ["GROQ_API_KEY"], config.GROQ_MODEL_CHAT))
        if OpenAI and os.environ.get("KIMI_API_KEY"):
            providers_chat.append(("Kimi", config.KIMI_BASE_URL, os.environ["KIMI_API_KEY"], config.KIMI_MODEL_CHAT))
        if OpenAI and os.environ.get("OPENAI_API_KEY"):
            providers_chat.append(("OpenAI", config.OPENAI_BASE_URL, os.environ["OPENAI_API_KEY"], config.OPENAI_MODEL_CHAT))

        for pname, base_url, api_key, pmodel in providers_chat:
            print(f"STEP:Tentando {pname} ({pmodel})...", flush=True)
            try:
                oc_client = OpenAI(api_key=api_key, base_url=base_url)
                openai_tools = _to_openai_tools(CHAT_TOOLS)
                oc_messages: list = [{"role": "system", "content": system}]
                for h in gemini_history:
                    role = "assistant" if h["role"] == "model" else "user"
                    text = h["parts"][0]["text"] if h.get("parts") else ""
                    oc_messages.append({"role": role, "content": text})
                oc_messages.append({"role": "user", "content": message})

                for turn in range(6):
                    print(f"STEP:Turno {turn + 1}... ({pname})", flush=True)
                    resp = _openai_compat_send(oc_client, pmodel, oc_messages, openai_tools, config.MAX_TOKENS_CHAT, provider_name=pname)
                    msg = resp.choices[0].message
                    assistant_entry: dict = {"role": "assistant", "content": msg.content or ""}
                    if msg.tool_calls:
                        assistant_entry["tool_calls"] = [
                            {"id": tc.id, "type": "function",
                             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                            for tc in msg.tool_calls
                        ]
                    oc_messages.append(assistant_entry)
                    if msg.content:
                        final_text = msg.content
                    if not msg.tool_calls:
                        break
                    for tc in msg.tool_calls:
                        print(f"STEP:{tc.function.name}", flush=True)
                        try:
                            args = _json2.loads(tc.function.arguments)
                        except _json2.JSONDecodeError:
                            args = {}
                        result = run_tool(tc.function.name, args)
                        oc_messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
                chat_model_used = pmodel
                break  # success
            except Exception as e:
                print(f"STEP:{pname} falhou ({type(e).__name__}) — tentando próximo...", flush=True)

        if not final_text:
            final_text = "[Erro: todos os modelos falharam. Tente novamente após meia-noite UTC.]"

    print(f"RESULT:{_json.dumps(final_text, ensure_ascii=False)}", flush=True)

    if not history:
        try:
            title_resp = client.models.generate_content(
                model=chat_model_used,
                contents=(
                    "Generate a concise title for this chat conversation. "
                    "Max 6 words. Same language as the user message. "
                    f"No quotes, no trailing punctuation.\n\nFirst message: {message[:300]}"
                ),
                config=types.GenerateContentConfig(max_output_tokens=20),
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


def _run_openai_compat(base_url: str, api_key: str, model: str,
                       system: str, tools: list, max_tokens: int,
                       initial_message: str, max_turns: int,
                       provider_name: str = "Provider",
                       rate_limit_retries: int = 1,
                       max_tool_result_chars: int | None = None,
                       progress_callback=None, step_prefix: str = "") -> str:
    """Run an agentic tool-use loop via any OpenAI-compatible API (Groq, Kimi, etc.)."""
    import json as _json
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed; run: uv pip install openai")

    oc_client = OpenAI(
        api_key=api_key,
        base_url=base_url,
    )
    openai_tools = _to_kimi_tools(tools) if provider_name == "Kimi" else _to_openai_tools(tools)
    messages: list = [
        {"role": "system", "content": system},
        {"role": "user", "content": initial_message},
    ]
    final_text = ""

    for turn in range(max_turns):
        if progress_callback:
            progress_callback(f"{step_prefix}Turno {turn + 1} — consultando {provider_name} ({model})...")

        response = _openai_compat_send(
            oc_client, model, messages, openai_tools, max_tokens,
            provider_name=provider_name, max_retries=rate_limit_retries,
            progress_callback=progress_callback, step_label=step_prefix,
        )
        msg = response.choices[0].message

        assistant_entry: dict = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
        messages.append(assistant_entry)

        if msg.content:
            final_text = msg.content

        # Native function calling (OpenAI tool_calls field)
        if msg.tool_calls:
            for tc in msg.tool_calls:
                if progress_callback:
                    progress_callback(f"{step_prefix}{tc.function.name}")
                try:
                    args = _json.loads(tc.function.arguments)
                except _json.JSONDecodeError:
                    args = {}
                result = run_tool(tc.function.name, args)
                if max_tool_result_chars and len(result) > max_tool_result_chars:
                    result = result[:max_tool_result_chars] + "...[truncado]"
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
            continue

        # Kimi text-based tool call fallback: "functions.name:id$\n{args}"
        if provider_name == "Kimi" and msg.content:
            import re as _re
            text_calls = _re.findall(
                r'functions\.(\w+):\d+\$\s*\n?\s*(\{.*?\})',
                msg.content, _re.DOTALL,
            )
            if text_calls:
                tool_results: list[str] = []
                for tc_name, tc_args_raw in text_calls:
                    if progress_callback:
                        progress_callback(f"{step_prefix}{tc_name}")
                    try:
                        args = _json.loads(tc_args_raw)
                    except _json.JSONDecodeError:
                        args = {}
                    result = run_tool(tc_name, args)
                    tool_results.append(f"[{tc_name} result]\n{result}")
                messages.append({"role": "user", "content": "\n\n".join(tool_results)})
                continue

        break

    return final_text


def _run_impl(model: str, progress_callback=None) -> str:
    system = build_system_prompt()
    cfg = _make_config(t.TOOLS, system, config.MAX_TOKENS)
    chat = client.chats.create(model=model, config=cfg)

    if progress_callback:
        progress_callback(f"Turno 1 — consultando Gemini ({model})...")
    response = _send(
        chat,
        "Faça a análise pré-mercado de hoje para os ativos sob cobertura, "
        "seguindo seu fluxo. Use as ferramentas conforme necessário e registre "
        "as observações do dia ao final.",
        progress_callback,
    )

    final_text = ""
    for turn in range(config.MAX_AGENT_TURNS):
        text = _get_text(response)
        if text:
            final_text = text

        tool_calls = _get_tool_calls(response)
        if not tool_calls:
            break

        if progress_callback:
            for fc in tool_calls:
                progress_callback(f"Executando ferramenta: {fc.name}")
        fn_parts = _execute_tools(tool_calls)

        if progress_callback:
            progress_callback(f"Turno {turn + 2} — consultando Gemini...")
        response = _send(chat, fn_parts, progress_callback)
    else:
        final_text += "\n\n[Aviso: limite de turnos atingido — análise pode estar incompleta.]"

    return final_text


_INITIAL_MESSAGE_FULL = (
    "Faça a análise pré-mercado de hoje para os ativos sob cobertura, "
    "seguindo seu fluxo. Use as ferramentas conforme necessário e registre "
    "as observações do dia ao final."
)
_INITIAL_MESSAGE_COMPACT = (
    "Faça a análise pré-mercado de hoje para os ativos da carteira. "
    "Use get_fear_greed_index, get_sector_performance e para cada ativo: "
    "get_stock_data, get_news, get_technical_indicators."
)


def _run_anthropic(api_key: str, model: str, system: str, tools: list, max_tokens: int,
                   initial_message: str, max_turns: int,
                   progress_callback=None, step_prefix: str = "") -> str:
    """Run an agentic tool-use loop via the Anthropic API."""
    try:
        import anthropic as _anthropic
    except ImportError:
        raise RuntimeError("anthropic package not installed; run: uv pip install anthropic")

    ac = _anthropic.Anthropic(api_key=api_key)
    messages: list = [{"role": "user", "content": initial_message}]
    final_text = ""

    for turn in range(max_turns):
        if progress_callback:
            progress_callback(f"{step_prefix}Turno {turn + 1} — consultando Anthropic ({model})...")

        response = ac.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )

        tool_uses = []
        for block in response.content:
            if hasattr(block, "text"):
                final_text = block.text
            elif getattr(block, "type", None) == "tool_use":
                tool_uses.append(block)

        if not tool_uses or response.stop_reason == "end_turn":
            break

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for tu in tool_uses:
            if progress_callback:
                progress_callback(f"{step_prefix}{tu.name}")
            result = run_tool(tu.name, dict(tu.input))
            tool_results.append({"type": "tool_result", "tool_use_id": tu.id, "content": result})

        messages.append({"role": "user", "content": tool_results})

    return final_text


def run(progress_callback=None) -> str:
    """
    Executa o loop agêntico e retorna o texto do relatório final.
    Ordem: Groq → Gemini → Kimi → Anthropic.
    """
    def _cb(msg: str) -> None:
        if progress_callback:
            progress_callback(msg)

    # 1. Gemini — free; full prompt + all tools
    gemini_models = list(dict.fromkeys([config.MODEL_FULL] + config.MODEL_FALLBACKS))
    for model in gemini_models:
        try:
            return _run_impl(model, progress_callback)
        except QuotaExhaustedError:
            _cb(f"Cota diária esgotada para {model} — tentando próximo...")
        except Exception as e:
            _cb(f"Modelo {model} falhou ({type(e).__name__}) — tentando próximo...")

    # 2. Kimi — free, 128k context; full prompt + all tools
    if os.environ.get("KIMI_API_KEY"):
        _cb(f"Tentando Kimi ({config.KIMI_MODEL_FULL})...")
        try:
            return _run_openai_compat(
                base_url=config.KIMI_BASE_URL, api_key=os.environ["KIMI_API_KEY"],
                model=config.KIMI_MODEL_FULL,
                system=build_system_prompt(), tools=t.TOOLS,
                max_tokens=config.MAX_TOKENS, initial_message=_INITIAL_MESSAGE_FULL,
                max_turns=config.MAX_AGENT_TURNS, provider_name="Kimi",
                rate_limit_retries=2, max_tool_result_chars=None,
                progress_callback=progress_callback,
            )
        except Exception as e:
            _cb(f"Kimi falhou ({type(e).__name__}: {str(e)[:100]}) — tentando próximo...")

    # 3. OpenAI — paid; full prompt + all tools
    if os.environ.get("OPENAI_API_KEY"):
        _cb(f"Tentando OpenAI ({config.OPENAI_MODEL_FULL})...")
        try:
            return _run_openai_compat(
                base_url=config.OPENAI_BASE_URL, api_key=os.environ["OPENAI_API_KEY"],
                model=config.OPENAI_MODEL_FULL,
                system=build_system_prompt(), tools=t.TOOLS,
                max_tokens=config.MAX_TOKENS, initial_message=_INITIAL_MESSAGE_FULL,
                max_turns=config.MAX_AGENT_TURNS, provider_name="OpenAI",
                rate_limit_retries=0, max_tool_result_chars=None,
                progress_callback=progress_callback,
            )
        except Exception as e:
            _cb(f"OpenAI falhou ({type(e).__name__}: {str(e)[:100]}) — tentando próximo...")

    # 4. Groq — free but limited (5 tools, compact prompt, tight token budget)
    if os.environ.get("GROQ_API_KEY"):
        _cb(f"Tentando Groq ({config.GROQ_MODEL_FULL})...")
        try:
            return _run_openai_compat(
                base_url=config.GROQ_BASE_URL, api_key=os.environ["GROQ_API_KEY"],
                model=config.GROQ_MODEL_FULL,
                system=build_system_prompt_compact(), tools=FALLBACK_TOOLS,
                max_tokens=1024, initial_message=_INITIAL_MESSAGE_COMPACT,
                max_turns=6, provider_name="Groq", max_tool_result_chars=2500,
                progress_callback=progress_callback,
            )
        except Exception as e:
            _cb(f"Groq falhou ({type(e).__name__}: {str(e)[:100]}) — tentando próximo...")

    # 5. Anthropic — paid last resort; full prompt + all tools
    if os.environ.get("ANTHROPIC_API_KEY"):
        _cb(f"Tentando Anthropic ({config.ANTHROPIC_MODEL})...")
        try:
            return _run_anthropic(
                api_key=os.environ["ANTHROPIC_API_KEY"],
                model=config.ANTHROPIC_MODEL,
                system=build_system_prompt(), tools=t.TOOLS,
                max_tokens=config.MAX_TOKENS, initial_message=_INITIAL_MESSAGE_FULL,
                max_turns=config.MAX_AGENT_TURNS, progress_callback=progress_callback,
            )
        except Exception as e:
            _cb(f"Anthropic falhou ({type(e).__name__}: {str(e)[:100]}) — tentando próximo...")

    raise RuntimeError(
        "Todos os modelos falharam ou esgotaram cota. Tente novamente após meia-noite UTC."
    )
