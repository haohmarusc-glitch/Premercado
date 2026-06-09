"""
Loop agêntico do analisador de pré-mercado.
Claude decide quais ferramentas chamar, lê a memória e grava observações.
"""
import datetime
import os
import sys

import anthropic

from . import config
from . import memory
from . import tools as t


client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY"),
    timeout=60.0,
    max_retries=2,
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

**FASE 2 — Análise por ativo** (repita para cada ativo em cobertura)
5. Puxe a cotação/pré-mercado com get_stock_data.
6. Veja as manchetes com get_news.
7. Chame get_technical_indicators para avaliar RSI, MACD, Bollinger e médias móveis.
8. Chame get_short_interest para verificar exposição short e risco de squeeze.
9. Chame get_analyst_ratings para ver consenso, preço-alvo e upgrades/downgrades recentes.
10. Chame get_options_data para ver put/call ratio e IV — sinais de posicionamento do mercado.
11. Se houver sinal de catalisador (resultados, guidance, contratos), procure documentos
    recentes com search_edgar_filings e leia o relevante com read_filing.
12. Compare com a MEMÓRIA DOS DIAS ANTERIORES abaixo — o que mudou desde a última leitura?
13. Ao concluir cada ativo, chame save_observation com um resumo curto e o sentimento.

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
    messages = [{
        "role": "user",
        "content": "Faça a varredura rápida de pré-mercado intradiário agora.",
    }]

    final_text = ""
    max_turns = min(config.MAX_AGENT_TURNS, 8)

    for turn in range(max_turns):
        if progress_callback:
            progress_callback(f"[Flash] Turno {turn + 1}...")

        resp = client.messages.create(
            model=config.MODEL,
            max_tokens=1024,
            system=system,
            tools=t.TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        for block in resp.content:
            if block.type == "text":
                final_text = block.text

        if resp.stop_reason != "tool_use":
            break

        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                if progress_callback:
                    progress_callback(f"[Flash] {block.name}")
                result = run_tool(block.name, dict(block.input))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})

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
    messages = list(history) + [{"role": "user", "content": message}]
    final_text = ""

    for turn in range(6):
        print(f"STEP:Turno {turn + 1}...", flush=True)

        resp = client.messages.create(
            model=config.MODEL,
            max_tokens=2048,
            system=system,
            tools=CHAT_TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        for block in resp.content:
            if block.type == "text":
                final_text = block.text

        if resp.stop_reason != "tool_use":
            break

        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                print(f"STEP:{block.name}", flush=True)
                result = run_tool(block.name, dict(block.input))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})

    print(f"RESULT:{_json.dumps(final_text, ensure_ascii=False)}", flush=True)

    # Generate a concise session title for new conversations (no prior history)
    if not history:
        try:
            title_resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=20,
                system=(
                    "Generate a concise title for this chat conversation. "
                    "Max 6 words. Same language as the user message. "
                    "No quotes, no trailing punctuation."
                ),
                messages=[{"role": "user", "content": f"First message: {message[:300]}"}],
            )
            for block in title_resp.content:
                if block.type == "text" and block.text.strip():
                    print(f"TITLE:{_json.dumps(block.text.strip(), ensure_ascii=False)}", flush=True)
                    break
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
            progress_callback(f"Turno {turn + 1} — consultando Claude...")

        resp = client.messages.create(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            system=system,
            tools=t.TOOLS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        for block in resp.content:
            if block.type == "text":
                final_text = block.text

        if resp.stop_reason != "tool_use":
            break

        tool_results = []
        for block in resp.content:
            if block.type == "tool_use":
                if progress_callback:
                    progress_callback(f"Executando ferramenta: {block.name}")
                result = run_tool(block.name, dict(block.input))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                })
        messages.append({"role": "user", "content": tool_results})
    else:
        final_text += "\n\n[Aviso: limite de turnos atingido — análise pode estar incompleta.]"

    return final_text
