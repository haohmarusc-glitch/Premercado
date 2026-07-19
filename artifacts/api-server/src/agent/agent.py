"""
Loop agêntico do analisador de pré-mercado.
Suporta múltiplos provedores: anthropic, openai, gemini, openrouter, kimi.
Configurado via variável AGENT_PROVIDER (padrão: anthropic).
"""
import datetime
import json as _json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

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
3. Chame get_geopolitical_news para falas/decisões de chefes de estado (EUA e
   outros países) sobre tarifas/comércio, guerra, petróleo, Big Techs e
   controle de exportação de semicondutores. Se algo relevante aparecer,
   cite explicitamente no resumo do(s) ativo(s)/setor(es) afetado(s) nas
   fases seguintes — não é só contexto genérico, é catalisador real.
4. Chame get_sector_performance para verificar se os setores da cesta estão em movimento
   antes de analisar ativos individuais (semis: SMH/SOXX; saúde: XLV/IBB; amplo: SPY/QQQ).
5. Chame get_earnings_calendar para identificar quais ativos têm resultados iminentes (≤ 14 dias).
6. Chame detect_sector_contagion para mapear contágio entre os grupos setoriais monitorados:
{_sector_groups_text()}
   Os tickers em "catch_up" são candidatos prioritários para análise aprofundada nesta sessão.
   Para captura intradiária: period='1d', interval='5m'.
7. Chame get_global_market_snapshot para contexto de Ásia overnight, Europa em
   overlap e futuros de índice. É só contexto informativo — não é um sinal de
   compra/venda; não ajuste thresholds com base nele sem validação histórica prévia.
8. Chame get_europe_regime_signal — sinal de regime validado por backtest real
   (não é contexto genérico como o passo 7: só existe recomendação quando a
   Nasdaq está fora de tendência de alta, e mesmo assim é um sinal SOMENTE
   sobre o índice ^IXIC, nunca aplique como sinal de entrada/saída de um
   ativo individual da cesta sem dizer explicitamente essa limitação no relatório.

**FASE 2 — Análise por ativo** (dois grupos; não misture a profundidade)

*Grupo A — análise COMPLETA*:
  • Tickers marcados como "líder" ou "catch_up" pelo detect_sector_contagion (FASE 1 passo 6)
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
2. Manchetes — get_news (UMA chamada só, passando a lista com TODOS os
   tickers do Grupo A juntos — get_news já aceita a lista inteira de uma vez)
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
separadas. O mesmo vale para cada categoria seguinte (get_news é a única
exceção: ela já recebe a lista inteira de tickers numa chamada única).

Exemplo correto com Grupo A = [MU, NVDA, SMCI]:
  Turno X: você chama get_stock_data(MU) + get_stock_data(NVDA) +
           get_stock_data(SMCI) — as 3 JUNTAS na mesma resposta.
  Turno X+1: você chama get_news(tickers=["MU", "NVDA", "SMCI"]) — UMA
           única chamada com os 3 juntos, não uma por ticker.
  Turno X+2: get_technical_indicators(MU) + get_technical_indicators(NVDA)
           + get_technical_indicators(SMCI) — de novo as 3 juntas, e assim
           por diante a cada categoria seguinte.
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
get_analyst_ratings, get_options_data, get_geopolitical_news (falas/decisões de
chefes de estado, guerra, petróleo, Big Techs, semicondutores).

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
        # Loga o traceback real no stderr -- runner.ts/chat.ts ja capturam
        # stderr do subprocesso via logger.warn (ver "Agent stderr"), entao
        # isso aparece nos logs do servidor sem precisar de plumbing nova.
        # Sem isso, a falha so' existia como uma string curta devolvida pro
        # LLM, invisivel a quem monitora o servidor.
        import traceback
        print(f"ERRO_TOOL {name}: {type(e).__name__}: {e}\n{traceback.format_exc()}", file=sys.stderr, flush=True)
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
    "get_geopolitical_news",
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
    "save_observation", "get_fear_greed_index", "get_geopolitical_news",
}
PORTFOLIO_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _PORTFOLIO_TOOL_NAMES]


def _system_stable_portfolio(tickers: list[str]) -> str:
    return f"""Você é um analista de ações fazendo uma análise RÁPIDA focada na carteira.
Ativos da carteira: {", ".join(tickers)}.

**Fluxo obrigatório — siga EXATAMENTE esta sequência sem pular etapas:**
1. get_fear_greed_index — sentimento macro
2. get_geopolitical_news — falas/decisões de chefes de estado (tarifas, comércio),
   guerra, petróleo, Big Techs e controle de exportação de semicondutores. Se algo
   relevante aparecer, cite no resumo do(s) ativo(s) afetado(s).
3. get_stock_data — cotação de TODOS os ativos juntos (N chamadas paralelas)
4. get_news — UMA chamada só, passando tickers=[{", ".join(tickers)}] (todos juntos, não um por vez)
5. get_technical_indicators — indicadores de TODOS os ativos juntos
6. detect_candle_patterns — padrões de vela de TODOS os ativos juntos. Se um
   padrão de reversão coincidir (mesma data ou ±1 dia) com uma manchete do
   get_news, destaque isso no resumo — é sinal mais forte que qualquer um isolado.
7. get_short_interest — short interest de TODOS os ativos juntos
8. get_analyst_ratings — consenso de TODOS os ativos juntos
9. **OBRIGATÓRIO — NÃO PULE:** save_observation para CADA ativo individualmente.
   Você DEVE chamar save_observation {len(tickers)} vezes (uma por ativo: {", ".join(tickers)}).
   Somente após salvar TODAS as observações escreva o relatório final.

**ATENÇÃO:** Não escreva o relatório final antes de completar o passo 9 (save_observation).
Se você pular o passo 9, a análise é considerada incompleta e inválida.

**Regras:**
- Agrupe por categoria, nunca por ativo.
- Seja conciso. Foque em variação do dia, nível técnico mais relevante e risco imediato.
- NÃO use: search_edgar_filings, read_filing, detect_sector_contagion, get_sector_performance,
  get_options_data, get_earnings_calendar, list_alerts, create_alert, delete_alert.

**Formato do relatório final (escreva APÓS salvar todas as observações):**
## ⚡ Carteira — Análise Rápida {{data}}
Para cada ativo: preço atual | variação % | sentimento | 1-2 linhas de análise."""


# ── Agent loop (shared by all run modes) ──────────────────────────────────────

# Relatório de mercado real cobre vários ativos em Markdown -- escala com
# min_observations (proxy de quantos ativos o relatório precisa cobrir) pra
# não gerar falso positivo numa carteira bem pequena (1-2 ativos), mas ainda
# assim pegar os ~140-160 caracteres típicos de um reconhecimento de
# continuação ("Entendido, vou fazer X..."), que não é o relatório de
# verdade. Só é checado quando require_observations=True (fluxos de
# relatório real); chat/premarket-flash não usam essa checagem.
MIN_REPORT_CHARS_PER_TICKER = 40
MIN_REPORT_CHARS_FLOOR = 150


def _min_report_chars(min_observations: int) -> int:
    return max(MIN_REPORT_CHARS_FLOOR, MIN_REPORT_CHARS_PER_TICKER * min_observations)


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
    deadline_ts: float | None = None,
) -> str:
    from .provider import TextBlock, ToolUseBlock

    final_text = ""
    observations_saved = 0
    nudges_left = 2  # cobranças de save_observation antes de aceitar o fim da run
    for turn in range(max_turns):
        if deadline_ts is not None and time.time() >= deadline_ts:
            # runner.ts vai mandar SIGTERM em breve (deadline_ts já reserva a
            # folga necessária) -- em vez de deixar o processo morrer sem
            # nunca imprimir REPORT: (run marcada como falha total mesmo já
            # tendo gasto o dinheiro das chamadas parciais), força um turno
            # final SEM ferramentas: tools=[] garante que a resposta só pode
            # ser texto, então esse turno não corre o risco de gerar mais
            # tool_use pendente perto do fim.
            if progress_callback:
                progress_callback(f"{step_prefix}Tempo esgotando — fechando relatório com os dados já coletados...")
            messages.append({"role": "user", "content": (
                "O tempo disponível para esta execução está acabando. NÃO "
                "chame mais nenhuma ferramenta. Escreva AGORA o relatório "
                "final em Markdown com os dados que você já coletou até "
                "aqui, mesmo que incompleto — avise no início do relatório "
                "que a análise foi encerrada antes do previsto por limite "
                "de tempo."
            )})
            resp = client.create(
                model=model, max_tokens=max_tokens, system=system, tools=[], messages=messages,
            )
            messages.append({"role": "assistant", "content": _resp_to_history_content(resp)})
            for block in resp.content:
                if isinstance(block, TextBlock):
                    final_text = block.text
            if not final_text.strip():
                final_text = (
                    "Análise incompleta: o tempo disponível se esgotou antes "
                    "da conclusão, e o modelo não produziu um relatório final."
                )
            break

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

        tool_use_blocks = [b for b in resp.content if isinstance(b, ToolUseBlock)]
        if tool_use_blocks:
            # A Anthropic exige um tool_result pra CADA tool_use na mensagem
            # seguinte, mesmo quando o stop_reason normalizado não é
            # "tool_use" — a API crua pode devolver "max_tokens"/"pause_turn"
            # com blocos de tool_use já completos antes do corte (o
            # normalizador em provider.py achata esses casos pra "end_turn").
            # Resolver só quando stop_reason == "tool_use" deixava esses
            # blocos órfãos no histórico, e a próxima chamada — nudge ou
            # continuação — batia com 400 invalid_request_error ("tool_use
            # ids were found without tool_result blocks"). Bug visto em
            # produção com claude-sonnet-5 em 17/07.
            if progress_callback:
                if len(tool_use_blocks) > 1:
                    names = ", ".join(dict.fromkeys(b.name for b in tool_use_blocks))
                    progress_callback(f"{step_prefix}Executando {len(tool_use_blocks)} ferramentas em paralelo ({names})...")
                else:
                    progress_callback(f"{step_prefix}Executando ferramenta: {tool_use_blocks[0].name}")

            # As ferramentas de um turno são I/O-bound (rede: yfinance, EDGAR,
            # API interna) -- rodar em série (uma aguardando a outra) foi o
            # maior fator no timeout de 18min do runner.ts em runs com muitos
            # ativos (o modelo já pede fan-out "N chamadas paralelas" no
            # prompt, mas o loop as executava sequencialmente mesmo assim).
            # Usar threads aqui é seguro: cada tool call é independente
            # (request HTTP própria ou yf.Ticker próprio), sem estado
            # compartilhado mutável exceto o cache em disco, que já ganhou
            # lock em cache.py pra essa mudança.
            with ThreadPoolExecutor(max_workers=len(tool_use_blocks)) as pool:
                results = list(pool.map(lambda b: run_tool(b.name, b.input), tool_use_blocks))

            tool_results = []
            for block, result in zip(tool_use_blocks, results):
                if block.name == "save_observation":
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
            continue

        if resp.stop_reason != "tool_use":
            # Só aceita o texto do turno como candidato a relatório final
            # quando o modelo sinaliza que terminou (sem mais tool_use).
            # Texto de um turno que TAMBÉM chama ferramenta é narração/
            # raciocínio intermediário ("Estou na FASE 2.5... vou chamar
            # check_market_alerts"), não o relatório -- se o loop acabar
            # batendo em max_turns enquanto o modelo ainda está nesse tipo
            # de narração, essa fala intermediária não deve virar o
            # relatório exibido pro usuário (bug visto em produção: a
            # narração de FASE 2.5 apareceu como se fosse o relatório final,
            # com as manchetes em inglês cru do get_news no meio).
            for block in resp.content:
                if isinstance(block, TextBlock):
                    final_text = block.text
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

            # As observações podem já estar OK (missing <= 0) e ainda assim o
            # texto final ser só um reconhecimento curto de continuação
            # ("Entendido, peço desculpas pela interrupção, vou registrar as
            # observações restantes..."), não o relatório de mercado de
            # verdade -- bug visto em produção: esse texto passava sem
            # nenhum aviso porque a checagem de observações sozinha não
            # detecta um relatório vazio/curto demais. Um relatório real
            # cobre vários ativos em Markdown e nunca é tão curto quanto
            # isso, então usamos o tamanho como sinal.
            looks_like_report = (
                require_observations is False
                or len(final_text.strip()) >= _min_report_chars(min_observations)
            )
            if require_observations and not looks_like_report and nudges_left > 0:
                nudges_left -= 1
                if progress_callback:
                    progress_callback(f"{step_prefix}Cobrando o relatório final por completo...")
                messages.append({"role": "user", "content": (
                    "Sua última resposta foi curta demais para ser o relatório de "
                    "mercado completo — parece só um reconhecimento de continuação, "
                    "não a análise final. Escreva AGORA o relatório completo em "
                    "Markdown, com uma seção por ativo, conforme o fluxo pedido."
                )})
                continue

            if require_observations and missing > 0:
                # Cobranças esgotadas e o modelo ainda assim respondeu só com
                # texto, sem chamar save_observation -- esse texto é quase
                # sempre um reconhecimento vazio da cobrança ("Compreendi,
                # vou reenviar as observações..."), não um relatório de
                # verdade. Bug visto em produção: esse texto estava sendo
                # salvo/exibido como se fosse o relatório final. Descarta e
                # substitui por uma mensagem de diagnóstico clara.
                final_text = (
                    "Análise incompleta nesta execução: o modelo não conseguiu "
                    "registrar as observações pendentes mesmo após ser cobrado, "
                    "e não produziu um relatório final confiável."
                )
            elif require_observations and not looks_like_report:
                # Cobranças esgotadas e o texto final continua curto demais
                # pra ser um relatório de verdade -- mesma lógica do bloco
                # acima, mas pro caso em que as observações já estavam OK.
                final_text = (
                    "Análise incompleta nesta execução: o modelo encerrou com uma "
                    "resposta curta demais para ser um relatório de mercado real, "
                    "mesmo após ser cobrado para completar."
                )
            break
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
    # Escala o teto de turnos com o tamanho da carteira coberta, seguindo o
    # mesmo padrao de run_portfolio(). O Grupo A (FASE 1) so e' conhecido em
    # tempo de execucao (depende do detect_sector_contagion), entao usamos o
    # tamanho da carteira fixa como piso minimo + margem para os candidatos
    # de catch_up que costumam entrar no Grupo A.
    n_min = len(config.PORTFOLIO_TICKERS)
    max_turns = max(config.MAX_AGENT_TURNS, n_min * 2 + 6)
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
        max_turns=max_turns,
        max_tokens=config.MAX_TOKENS,
        progress_callback=progress_callback,
        require_observations=True,
        # Piso seguro: as posições da carteira SEMPRE recebem save_observation
        # (completa ou reduzida, pela regra de economia) — os líderes de
        # contágio fora da carteira somam mais chamadas, mas sua contagem
        # exata só é conhecida em runtime, então não entram no piso.
        min_observations=len(config.PORTFOLIO_TICKERS),
        deadline_ts=config.SOFT_DEADLINE_TS,
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
        deadline_ts=config.SOFT_DEADLINE_TS,
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
