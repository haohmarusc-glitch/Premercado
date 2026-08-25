"""
Loop agêntico do analisador de pré-mercado.
Suporta múltiplos provedores: anthropic, openai, gemini, openrouter, kimi.
Configurado via variável AGENT_PROVIDER (padrão: anthropic).
"""
import datetime
import json as _json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import yfinance as yf

from . import brt
from . import market_data_provider
from . import config
from . import memory
from . import tools as t
from .provider import get_client, ProviderClient
from .sector_contagion import SECTOR_GROUPS
from .report_validator import (
    collect_tool_result,
    correction_prompt,
    lint_report,
    new_snapshot,
)
from .veredito_validator import (
    extrair_bloco_estruturado,
    validar_veredito_completo,
    validate_snapshot,
)

# Helpers de horário de Brasília moram em brt.py (fonte única) -- tools.py
# também precisa deles e não pode importar agent.py, que importaria de volta.
# Os nomes privados abaixo continuam existindo como fachada: são usados em
# ~10 pontos deste módulo e nos testes (test_agent_brt_time.py).
_BRT_OFFSET = brt.BRT_OFFSET
_now_brt = brt.now_brt
_today_brt_str = brt.today_brt_str
_now_brt_str = brt.now_brt_str


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
1. Chame get_fear_greed_index para capturar o sentimento macro do mercado.
2. Chame get_geopolitical_news para falas/decisões de chefes de estado (EUA e
   outros países) sobre tarifas/comércio, guerra, petróleo, Big Techs e
   controle de exportação de semicondutores. Se algo relevante aparecer,
   cite explicitamente no resumo do(s) ativo(s)/setor(es) afetado(s) nas
   fases seguintes — não é só contexto genérico, é catalisador real.
3. Chame get_sector_performance para verificar se os setores da cesta estão em movimento
   antes de analisar ativos individuais (semis: SMH/SOXX; saúde: XLV/IBB; amplo: SPY/QQQ).
4. Chame get_earnings_calendar para identificar quais ativos têm resultados iminentes (≤ 14 dias).
5. Chame detect_sector_contagion para mapear contágio entre os grupos setoriais monitorados:
{_sector_groups_text()}
   Isto é só CONTEXTO para o relatório final: o relatório cobre exclusivamente
   as posições da carteira (FASE 2), então não abra análise aprofundada de um
   ticker só por ele ter sido marcado "líder"/"catch_up" aqui — cite o
   contágio no resumo de um ativo da carteira apenas se ele estiver no mesmo
   setor do movimento detectado.
   Para captura intradiária: period='1d', interval='5m'.
6. Chame get_global_market_snapshot para contexto de Ásia overnight, Europa em
   overlap e futuros de índice. É só contexto informativo — não é um sinal de
   compra/venda; não ajuste thresholds com base nele sem validação histórica prévia.
7. Chame get_europe_regime_signal — sinal de regime validado por backtest real
   (não é contexto genérico como o passo 6: só existe recomendação quando a
   Nasdaq está fora de tendência de alta, e mesmo assim é um sinal SOMENTE
   sobre o índice ^IXIC, nunca aplique como sinal de entrada/saída de um
   ativo individual da cesta sem dizer explicitamente essa limitação no relatório.

**FASE 2 — Análise por ativo** (só a carteira — o relatório NÃO cobre outros
tickers da lista de cobertura; contágio setorial em FASE 1 passo 5 é só
contexto, não motivo pra abrir análise de um ticker fora da carteira)

*Grupo A — análise COMPLETA*:
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
   tickers do Grupo A juntos — get_news já aceita a lista inteira de uma vez).
   As manchetes chegam de várias origens já mescladas: priorize FATO
   verificável (guidance, contrato, tarifa, filing) sobre opinião de manchete,
   e ignore o campo `origin` na análise — ele só existe para depuração.
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

**FASE 2.5 — Radar de mercado** (após coletar notícias de TODOS os ativos)
14. Chame check_market_alerts passando todas as manchetes coletadas em headlines_by_ticker.

A gestão de alertas de preço (criar/remover com create_alert/delete_alert)
NÃO é parte deste fluxo — roda numa execução própria, separada, logo depois
desta (ver run_alerts_management), pra nunca ser sacrificada quando esta
análise principal estoura o tempo disponível.

**RÓTULO POR ATIVO** (aplicar ao ESCREVER o relatório final; só Grupo A)

Cada seção de ativo do Grupo A abre com UM rótulo de cor. O rótulo é SEMPRE
sobre o setup dos próximos 1–5 pregões — NUNCA sobre a tese de 6–12 meses.
Essa separação é obrigatória: a tese vai numa linha própria "Tese (6–12m):",
pra não disputar espaço com o timing. Um ativo pode perfeitamente ter tese
boa e rótulo 🟡/🔴 no dia; isso não é contradição, é o formato funcionando.

  🟢 = setup favorável AGORA (entrar/aumentar faz sentido hoje)
  🟡 = tese ok, timing ruim ou não confirmado — esperar
  🔴 = risco de curto prazo domina a decisão de hoje

GATES — cada um tem PESO. Só CRÍTICO e ATIVO contam para a cor.

CRÍTICOS (o evento/dado domina o setup do dia):
  • bloco técnico defasado — `rsi_date` anterior ao `as_of` do get_stock_data
    (regra de frescor abaixo)
  • `days_until_earnings` ≤ 5 (get_earnings_calendar; dias CORRIDOS, use o
    campo como vem)

ATIVOS:
  • `days_until_earnings` entre 6 e 14 — ainda no radar, mas o dia não é sobre
    o evento e a IV já cobra o prêmio
  • variação do dia negativa (get_stock_data)
  • IV de evento: `atm_iv_pct` (get_options_data) ≥ **32 × `atr_pct`**
    (get_technical_indicators). Use esta conta exata, com o 32 já embutido —
    não a decomponha. (Ela é 2× a volatilidade anualizada do próprio ativo:
    anualizar o ATR% multiplica por ~16, e o gate exige o dobro disso.
    Comparar `atm_iv_pct` com `atr_pct` × 16 sozinho usa METADE do limiar
    e reprova ativo com IV perfeitamente normal — não faça isso.)
    O corte é por ATIVO em vez de um número fixo de IV: 96% é normal em SMCI
    e seria evento em GOOGL, mesma lógica das bandas de RSI calibradas por ATR%.
  • `pct_above_sma200` ≥ 25% — extensão historicamente insustentável. SÓ vale
    com o bloco técnico FRESCO; se ele estiver defasado, este gate não existe.
  • manchete de risco binário: ITC, antitruste, patente, processo, downgrade
    ou rating "Sell" não confirmado.

INFORMATIVO (NÃO muda a cor):
  • short alto (≥15% do float). É gate de "não perseguir a alta do dia", não
    de rebaixar rótulo: a assimetria de squeeze corta para os dois lados, e
    rebaixar por ela assumiria uma direção que o dado não dá. Cite no texto.

NÃO são gates, de propósito:
  • macro (juro 10y): foi verdadeiro em todos os relatórios revisados. Gate que
    não varia entre ativos no mesmo dia não informa nada — só desloca o piso de
    todo mundo igualmente. Continua na seção de contexto macro.
  • técnico "fraco" genérico (MACD bearish, abaixo da SMA50): mesmo problema em
    correção prolongada, e é exatamente o que o 🟡 de julgamento já cobre.

Quantos gates, qual rótulo:
  • ZERO gates que contam → 🟢 permitido (não obrigatório).
  • 🔴 exige deterioração COMBINADA: dois críticos, OU um crítico com pelo
    menos um ativo, OU três ativos.
  • Qualquer outro caso com pelo menos um gate → 🟡.
Um gate só conta se a condição dele for REALMENTE verdadeira com os números
que você tem. Se o seu receio sobre o ativo não corresponde a nenhum gate,
escreva o receio no texto — não conte um gate que você mesmo acabou de
descrever como não atendido só para chegar a 🔴. Visto em produção (02/08):
o relatório rotulou ARM 🔴 alegando "dois gates", sendo que o segundo era a
IV, e o próprio texto dizia que ela estava ABAIXO do limiar.

Estes gates governam o RÓTULO (consistência do texto), não são sinal de
entrada/saída validado por backtest — não os reaproveite como threshold de
estratégia nem os cite como se fossem sinal testado.

Logo abaixo do rótulo, UMA linha justificando com o número que o determinou
(ex.: "🟡 — earnings em 3 dias e engolfo de baixa na zona da MM200").

**Frescor do dado técnico**

get_technical_indicators devolve `rsi_date`: a data da barra que gerou TODO o
bloco técnico (rsi, macd, sma50, sma200, ema, bollinger vêm todos do mesmo
histórico, não só o RSI). Se `rsi_date` for anterior ao `as_of` do
get_stock_data do mesmo ativo (as duas datas são comparáveis: ambas são a
data da última barra diária, no formato YYYY-MM-DD):
  • não use esse bloco para sustentar o rótulo;
  • se citar algum número dele mesmo assim, diga a data explicitamente;
  • o gate de frescor acima passa a valer (teto 🟡).
Visto em produção (02/08): o relatório citou "38,9% acima da MM200 (dado
defasado)" e ainda assim usou a MM200 no veredito do ativo — marcar como
defasado em prosa não basta, o número não pode sustentar a conclusão.

Princípios:
- Seja factual e cite os números.
- No relatório final, inclua seções por ativo em Markdown.
- Tickers com sufixo .SA são da B3 (Brasil), cotados em REAIS (R$) e sem
  pré-mercado — antes das 10h de Brasília, reporte o fechamento anterior e
  sinalize a moeda; não misture R$ com US$ em comparações diretas."""


_DIAS_SEMANA_PT = [
    "segunda-feira", "terça-feira", "quarta-feira",
    "quinta-feira", "sexta-feira", "sábado", "domingo",
]


def _aviso_sem_pregao(agora_utc: "datetime.datetime | None" = None) -> str:
    """Aviso explícito quando o dia corrente não tem pregão nos EUA.

    Visto em produção: as análises de sábado (01/08) e domingo (02/08)
    apresentaram o fechamento de sexta como se fosse a leitura do dia, sem
    dizer em nenhum momento que o mercado estava fechado. O veredito de 01/08
    chegou a escrever "+2,32% no fechamento de 01 ago" e "SKHY desceu -3,54%
    hoje" -- os dois números eram de 31/07.

    Cobre só FIM DE SEMANA. Feriado de bolsa não entra de propósito: manter
    uma lista de feriados à mão é a mesma armadilha do TICKER_TO_CIK
    (desatualiza em silêncio e passa a mentir), e um feriado não sinalizado é
    menos danoso que uma lista errada afirmando que houve pregão. Quando
    houver dúvida, o `as_of` do get_stock_data é a fonte real do último pregão.
    """
    hoje = brt.now_brt(agora_utc).date()
    if hoje.weekday() < 5:
        return ""
    return (
        f"\nATENÇÃO — HOJE É {_DIAS_SEMANA_PT[hoje.weekday()].upper()}: "
        f"NÃO HÁ PREGÃO nos EUA.\n"
        f"Todos os preços, variações e indicadores que você receber são do "
        f"ÚLTIMO PREGÃO FECHADO (confirme a data no campo `as_of` do "
        f"get_stock_data), não de hoje.\n"
        f"  • Diga isso logo no início do relatório, com a data do pregão.\n"
        f"  • NUNCA escreva \"hoje\", \"no fechamento de {hoje.strftime('%d/%m')}\" "
        f"ou \"a ação subiu/caiu hoje\" para esses números — eles não são de hoje.\n"
        f"  • O gate de variação do dia continua valendo, mas você deve deixar "
        f"claro que a variação é do último pregão, não de hoje.\n"
    )


def _system_volatile(agora_utc: "datetime.datetime | None" = None) -> str:
    """Parte VOLÁTIL: muda a cada execução, fica num bloco SEM cache."""
    today = _today_brt_str(agora_utc)
    dia_semana = _DIAS_SEMANA_PT[brt.now_brt(agora_utc).date().weekday()]
    return f"""Data de hoje: {today} ({dia_semana}).
{_aviso_sem_pregao(agora_utc)}
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
    today = _today_brt_str()
    now = _now_brt_str()
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


def build_news_prompt() -> str:
    today = _today_brt_str()
    now = _now_brt_str()
    return f"""Você é um analista de ações fazendo uma VARREDURA RÁPIDA SÓ DE NOTÍCIAS às {now} de {today}.
Ativos sob cobertura: {", ".join(config.TICKERS)}.

Esta é uma varredura só de notícias — NÃO é o relatório diário completo, não
tem técnicos, opções, candles ou EDGAR. Seja conciso.

**Fluxo obrigatório (execute na ordem):**
1. get_geopolitical_news — falas/decisões de chefes de estado (tarifas, comércio),
   guerra, petróleo, Big Techs, controle de exportação de semicondutores
2. get_news — UMA chamada só, passando TODOS os tickers sob cobertura juntos
   (nunca um por vez em turnos separados)

**NÃO USE:** nenhuma outra ferramenta (sem técnicos, opções, candles, EDGAR,
short interest, analistas, alertas, save_observation).

**Formato da saída — "## 📰 Notícias {now}":**
- Seção de contexto macro/geopolítico (2-4 linhas, só se algo relevante apareceu)
- Por ativo: só os que tiveram manchete relevante nas últimas ~24h — 1-2 linhas
  cada, citando a manchete e por que importa
- Ativos sem notícia relevante: omitir da lista (não escreva "sem notícias" pra cada um)

Limite: no máximo 400 palavras. Seja direto e factual."""


def build_exit_plan_prompt() -> str:
    today = _today_brt_str()
    return f"""Você é um analista de ações reavaliando o PLANO DE SAÍDA da carteira em {today}.
Posições atuais da carteira (fonte: lotes reais, não campo cacheado): {", ".join(config.PORTFOLIO_TICKERS) or "(nenhuma)"}.
Note que ETF de caixa (ex.: SGOV) fica de fora desta lista de propósito -- não
tem catalisador direcional pra plano de saída, não é item faltando.

O Plano de Saída é uma lista de metas/janelas de venda por posição (data-alvo, ação,
motivo), cadastrada manualmente pelo usuário ou por uma reavaliação sua anterior.
Preços mudam bastante -- saltos de +15-25% num único pregão não são raros --
então um plano de dias atrás pode já estar desatualizado.

**Fluxo obrigatório (execute na ordem):**
1. get_exit_plan_items -- traz todos os itens atuais (ticker, fase, data-alvo,
   ação, motivo, status).
2. Para cada item com status "pending" cujo ticker NÃO está na lista de
   posições atuais acima: chame update_exit_plan_item(item_id, status="skipped",
   rationale="Posição não está mais na carteira") -- a posição foi zerada
   (vendida) e o item ficaria "pending" pra sempre sem essa checagem. Não
   pule esta etapa mesmo que pareça óbvio.
3. Para cada item restante com status "pending" (ticker ainda na carteira):
   busque dado atual do ticker (get_stock_data sempre; get_technical_indicators,
   get_news, get_analyst_ratings, check_squeeze_setup, get_earnings_calendar
   conforme a situação pedir) e decida:
   - MANTER como está, se o plano ainda faz sentido -- não chame
     update_exit_plan_item em item que não precisa mudar.
   - ATUALIZAR (update_exit_plan_item) se o preço/contexto mudou o bastante
     pra alterar a data-alvo, a ação ou o motivo -- sempre cite o dado real
     que mudou sua avaliação no rationale, nunca invente número.
4. Se um ticker da lista de posições atuais não aparecer em NENHUM item do
   plano (nem "pending" nem "skipped"/"sold"), considere criar um item
   (create_exit_plan_item) -- só se fizer sentido, não force um plano pra
   tudo.

**NÃO USE:** save_observation, alertas, EDGAR, opções.

Ao final, responda com um resumo curto (até 300 palavras): o que mudou e por
quê. Não repita o que já estava certo -- foque nas mudanças de fato feitas."""


def build_alerts_management_prompt() -> str:
    return f"""Você é um analista de ações revisando e calibrando os alertas de preço automáticos.
Ativos sob cobertura: {", ".join(config.TICKERS)}. Posições da carteira: {", ".join(config.PORTFOLIO_TICKERS)}.

Esta é uma execução SÓ de gestão de alertas — não é o relatório diário
completo. Você já tem acesso à memória das análises recentes (abaixo) pra
saber o que já foi observado nos últimos dias, sem precisar refazer a
pesquisa completa.

**Fluxo obrigatório:**
1. Chame list_alerts (sem filtro) pra ver todos os alertas cadastrados.
2. Remova com delete_alert qualquer alerta obsoleto, duplicado (mesmo
   symbol+condition repetido), ou sem threshold_pct coerente com a
   volatilidade do ativo — sempre com motivo claro no reason.
3. Chame detect_sector_contagion (period='1d', interval='5m') pra
   identificar os tickers com movimento relevante hoje.
4. Para até 5 candidatos (líderes/catch_up do passo 3 + posições da
   carteira ainda sem alerta calibrado), chame get_technical_indicators
   pra pegar o atr_pct de cada um.
5. Crie até 3 alertas novos com create_alert, com threshold_pct ≈
   atr_pct * 1.5 (nunca um valor fixo igual pra todos os ativos) e reason
   justificando o nível escolhido.

Ao final, responda com um resumo curto (até 200 palavras): quantos
alertas removeu (e por quê) e quais criou (symbol, condition, threshold,
motivo). Não repita o relatório de mercado do dia — foque só nos alertas."""


def build_veredito_prompt() -> str:
    today = _today_brt_str()
    return f"""Você é um analista de ações escrevendo o VEREDITO DO DIA da carteira em {today},
cruzando dado de VÁRIAS ferramentas diferentes num único texto (não é o relatório
diário completo -- é uma síntese enxuta e opinativa sobre a situação atual).
Carteira: {", ".join(config.PORTFOLIO_TICKERS)}.

**Fluxo obrigatório (execute na ordem, sem pular etapas):**
1. get_scenario_status -- chance de empatar até a data-alvo e o histórico de
   confirmação diária (termômetro). Se configured=false, mencione isso e siga.
2. get_exit_plan_items -- itens pendentes e seus prazos.
3. Para cada ticker da carteira: get_technical_indicators (RSI/MACD/SMA/ATR).
4. get_macro_indicators -- contexto macro (CPI, juros, curva).
5. get_earnings_calendar (todos os tickers de uma vez) -- balanços próximos.
6. get_fear_greed_index -- termômetro de sentimento geral do mercado.
7. Opcional: get_backtest_summary só pro ticker mais arriscado/relevante do
   dia (o que tiver RSI mais esticado, earnings mais próximo, ou pior pEmpate
   individual) -- não rode pra todos, é uma chamada pesada.
8. Opcional: detect_sector_contagion e/ou get_analyst_ratings pro(s) ticker(s)
   mais relevante(s), se agregarem contexto real à conclusão.
9. Opcional: get_fundamentals_valuation (P/E, PEG, DCF) pro(s) ticker(s) cuja
   técnica pareça fraca mas o preço já possa ter descontado demais o risco
   (ou vice-versa: técnica forte com valuation esticado) -- decisão de
   vender/manter/aumentar sem olhar valuation é incompleta quando o múltiplo
   está claramente fora do normal histórico do ativo/setor.

**NÃO USE:** save_observation, alertas (list/create/delete_alert),
update_exit_plan_item, create_exit_plan_item, EDGAR, opções.

Quando o snapshot trouxer `capex_hyperscalers`, ele é o capex somado de
Microsoft, Alphabet, Amazon, Meta e Oracle no último trimestre em que TODAS
reportaram -- a medida direta do buildout de data centers. Cite o número, o
trimestre e a variação, e use CAPEX_ACELERANDO/CAPEX_DESACELERANDO só na
direção que o dado mostra (o validador confere). Ele é CONTEXTO de tese, não
gatilho de operação: capex trimestral não diz o que o papel faz amanhã.

Quando o snapshot trouxer `folego_de_caixa`, ele traz, por ticker, o balanço
do último trimestre DIVULGADO: caixa, dívida líquida, fluxo de caixa livre e
`folegoTrimestres` -- quantos trimestres o caixa cobre na queima média do
último ano. `geraCaixa: true` significa que a empresa não queima, e nesse caso
`folegoTrimestres` vem nulo de propósito: fôlego de quem gera caixa não é um
número, é a ausência do problema. Use CAIXA_CURTO só com fôlego abaixo de 4
trimestres e CAIXA_CONFORTAVEL só acima (o validador confere).

`quebraDeSerie: true` significa que a dívida ou a quantidade de ações deu um
salto grande num único trimestre -- reestruturação, emissão ou recompra
grande. Nesse caso NÃO compare com o ano anterior: a comparação mede
contabilidade, não operação. Declare BALANCO_REESTRUTURADO e diga o que a
comparação deixa de valer. Como o capex, isto é CONTEXTO de risco de tese,
não gatilho: balanço trimestral não diz o que o papel faz amanhã.

Cuidado ao usar os termos "distribuição"/"acumulação": distribuição
institucional é padrão de TOPO (mãos fortes vendendo pra mãos fracas perto
de uma máxima/exaustão de alta) e acumulação é padrão de FUNDO (mãos fortes
comprando de mãos fracas perto de uma mínima/exaustão de baixa) -- nunca
inverta isso. Um ticker com RSI baixo (perto de sobrevenda) e bem abaixo da
SMA50 rejeitando um gap de alta intradiário é mais consistente com
capitulação/teste de suporte perto de um fundo do que com "distribuição"
(que pressupõe estar perto de um topo). Visto em produção: o Veredito já
descreveu esse exato cenário como "distribuição" -- rótulo trocado.

Formato da resposta (Markdown):
- Primeira linha: **VEREDITO:** seguido de UMA frase curta e direta (favorável /
  neutro / cauteloso / atenção redobrada), sem rodeio.
- Depois, um texto corrido detalhado (400-600 palavras) que CITA NÚMEROS
  concretos de CADA ferramenta usada acima -- não é um resumo genérico, é a
  ligação entre os dados: por que a chance de empatar está no nível que está,
  o que os técnicos dizem sobre timing, o que o plano de saída e os earnings
  próximos exigem de atenção, e como o pano de fundo macro/sentimento
  encaixa nisso tudo.
- Depois, uma seção "Próximos passos" (até 3 itens curtos e acionáveis).
- Por ÚLTIMO, obrigatoriamente, o bloco estruturado da decisão -- um bloco
  de código ```json com este formato EXATO, cobrindo TODOS os tickers da
  carteira, um por um:

```json
{{
  "tickers": [
    {{"ticker": "NVDA", "action": "MANTER", "confidence": 0.6,
      "reason_codes": ["EARNINGS_PROXIMO", "RSI_SOBRECOMPRADO"]}}
  ]
}}
```

  Regras do bloco: `action` é um de COMPRAR | AUMENTAR | MANTER | REDUZIR |
  VENDER | AGUARDAR; `confidence` é número de 0 a 1; `reason_codes` (1 a 4)
  vem preferencialmente de: RSI_SOBRECOMPRADO, RSI_SOBREVENDIDO,
  TENDENCIA_ALTA, TENDENCIA_BAIXA, EARNINGS_PROXIMO, RISCO_CORRELACAO,
  MACRO_ADVERSO, MACRO_FAVORAVEL, SUPORTE_PROXIMO, RESISTENCIA_PROXIMA,
  VOLUME_FRACO, VOLUME_FORTE, VALUATION_ESTICADO, VALUATION_DESCONTADO,
  RUNUP_ESTICADO, PLANO_DE_SAIDA, SENTIMENTO_EXTREMO, CENARIO_EMPATE,
  CAPEX_ACELERANDO, CAPEX_DESACELERANDO.
  (RUNUP_ESTICADO é alta pré-earnings acumulada no preço; VALUATION_ESTICADO
  é múltiplo caro -- não confunda os dois.)
  O bloco e o texto têm que contar a MESMA história: o bloco é a decisão,
  o texto é a explicação dela. Um validador determinístico confere os dois
  entre si e contra os dados -- razão contradita pelo dado (ex.:
  RSI_SOBREVENDIDO com RSI 50) e compra às vésperas de earnings sem
  declarar EARNINGS_PROXIMO são erros que forçam correção."""


def build_chat_prompt() -> str:
    today = _today_brt_str()
    now = _now_brt_str()
    portfolio_tickers = list(config.PORTFOLIO_TICKERS)
    portfolio_line = (
        f"Posições da carteira (ABERTAS agora, é isso que \"a carteira\"/\"minhas "
        f"posições\" significa quando o usuário perguntar): {', '.join(portfolio_tickers)}."
        if portfolio_tickers
        else "Posições da carteira: nenhuma posição aberta no momento."
    )
    try:
        rich = memory.rich_context_block()
    except Exception as e:
        rich = f"(contexto rico indisponível: {e})"
    try:
        mem = memory.recent_context(portfolio_only=True, portfolio_tickers=portfolio_tickers)
    except Exception as e:
        mem = f"(memória indisponível: {e})"

    return f"""Você é um analista de ações conversacional em {today} ({now} BRT).
Ativos monitorados (cobertura geral, NÃO é a carteira do usuário): {", ".join(config.TICKERS)}.
{portfolio_line}

Regra importante: se o usuário perguntar sobre "a carteira", "minha carteira",
"minhas posições" ou similar, responda SOMENTE sobre os tickers listados acima
como posições da carteira -- nunca inclua um ticker de cobertura geral (ou
citado na memória de dias anteriores, que cobre setores/cestas inteiras) que
não esteja nessa lista, mesmo que ele tenha aparecido em análises recentes.
Se o usuário pedir um ticker específico fora da carteira, responda normalmente
sobre ele, só não o rotule como posição da carteira.

Ferramentas disponíveis:
- Análise: get_stock_data, get_news, get_technical_indicators, detect_candle_patterns,
  get_fear_greed_index, get_sector_performance, get_short_interest, get_analyst_ratings,
  get_options_data, get_geopolitical_news, check_squeeze_setup, get_macro_indicators,
  get_retail_sentiment, get_fundamentals_valuation, get_insider_trades,
  get_gamma_exposure (máx 1x/resposta), get_earnings_transcript
- Carteira / ações: get_portfolio_snapshot (qty, custo, P&L), list_alerts, create_alert,
  delete_alert, get_scenario_status (chance de empatar), get_exit_plan_items
  (metas/janelas de venda cadastradas)

Regras:
- Responda à pergunta do usuário de forma direta e concisa.
- Use ferramentas apenas quando necessário. Máximo 6 chamadas por resposta.
- Preferências de ação: antes de create_alert, chame list_alerts (evite duplicata).
  No máximo 2 create_alert por resposta; sempre explique o motivo ao usuário.
  delete_alert só com motivo claro (nível superado / obsoleto).
- NÃO use: save_observation, search_edgar_filings, read_filing,
  check_market_alerts, detect_sector_contagion, update_exit_plan_item,
  create_exit_plan_item.
- Formate em Markdown. Seja factual; cite números.

=== ESTADO ATUAL (carteira / alertas / cenário) ===
{rich}
=== FIM DO ESTADO ===

=== MEMÓRIA RECENTE (só tickers da carteira, 1 obs/ticker/dia) ===
{mem}
=== FIM DA MEMÓRIA ==="""


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
    """Convert NormalizedResponse to Anthropic-style content list for history.

    Quando o provedor devolveu reasoning_content (DeepSeek em modo thinking —
    ver NormalizedResponse.reasoning_content), ele é anexado no primeiro bloco
    do turno para sobreviver no histórico. provider.py se encarrega de
    reidratá-lo pro formato certo em cada provedor (reenviado pro DeepSeek,
    removido antes de ir pra Anthropic)."""
    result = []
    from .provider import TextBlock, ToolUseBlock
    for block in resp.content:
        if isinstance(block, TextBlock):
            result.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            result.append({"type": "tool_use", "id": block.id, "name": block.name, "input": block.input})
    if getattr(resp, "reasoning_content", None) and result:
        result[0] = {**result[0], "reasoning_content": resp.reasoning_content}
    return result


# ── Chat tool subset ──────────────────────────────────────────────────────────

_CHAT_TOOL_NAMES = {
    # Analise
    "get_stock_data", "get_news", "get_technical_indicators",
    "detect_candle_patterns", "get_fear_greed_index", "get_sector_performance",
    "get_short_interest", "get_analyst_ratings", "get_options_data",
    "get_geopolitical_news", "check_squeeze_setup",
    "get_macro_indicators", "get_retail_sentiment", "get_fundamentals_valuation",
    "get_insider_trades",
    # Acao / estado da carteira (liberadas no chat com guardrails no prompt)
    "get_portfolio_snapshot",
    "list_alerts", "create_alert", "delete_alert",
    "get_scenario_status", "get_exit_plan_items",
}
# get_gamma_exposure/get_earnings_transcript (CHAT_ONLY_TOOLS em tools.py)
# ficam de FORA de t.TOOLS de propósito -- tier grátis de 5 req/dia e 5
# req/min, respectivamente, estouraria numa varredura automática com vários
# tickers. Só entram aqui, no Chat, onde o usuário pede um ticker por vez.
CHAT_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _CHAT_TOOL_NAMES] + t.CHAT_ONLY_TOOLS

# Subconjunto para a varredura rápida intradiária. O prompt do premarket já
# proíbe as demais ferramentas; aqui cortamos de fato o schema delas do request,
# economizando ~7k tokens de input por turno (das 17 ferramentas só 5 são usadas).
_PREMARKET_TOOL_NAMES = {
    "get_fear_greed_index", "get_sector_performance",
    "detect_sector_contagion", "get_stock_data", "get_options_data",
    "get_macro_indicators",
}
PREMARKET_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _PREMARKET_TOOL_NAMES]


# Tool subset for portfolio fast mode
_PORTFOLIO_TOOL_NAMES = {
    "get_stock_data", "get_news", "get_technical_indicators",
    "detect_candle_patterns", "get_short_interest", "get_analyst_ratings",
    "save_observation", "get_fear_greed_index", "get_geopolitical_news",
    "get_macro_indicators", "get_retail_sentiment", "get_fundamentals_valuation",
    "get_insider_trades",
}
PORTFOLIO_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _PORTFOLIO_TOOL_NAMES]

# Subconjunto para a varredura rápida SÓ de notícias -- sem técnicos, opções,
# candles, EDGAR etc., só get_news (por ativo) + get_geopolitical_news (macro).
_NEWS_TOOL_NAMES = {"get_news", "get_geopolitical_news"}
NEWS_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _NEWS_TOOL_NAMES]

# Subconjunto pra reavaliação do Plano de Saída -- leitura/escrita do plano
# em si (get/update/create_exit_plan_item) + dado atual por ticker pra
# justificar qualquer mudança de data-alvo/ação/motivo.
_EXIT_PLAN_TOOL_NAMES = {
    "get_exit_plan_items", "update_exit_plan_item", "create_exit_plan_item",
    "get_stock_data", "get_technical_indicators", "get_news",
    "check_squeeze_setup", "get_analyst_ratings", "get_earnings_calendar",
}
EXIT_PLAN_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _EXIT_PLAN_TOOL_NAMES]

# Gestão de alertas isolada da análise diária completa (FASE 3 do run() antigo)
# -- antes era a última etapa de um único loop com FASE 1/2/2.5, então quando
# o run inteiro estourava o deadline (carteira grande, dia com muito
# contágio), a gestão de alertas era sempre a primeira sacrificada mesmo
# depois de já ter gasto o custo das fases anteriores. Rodando sozinha, sempre
# completa (poucos turnos, tools leves), independente de quanto tempo a
# análise principal levou naquele dia.
_ALERTS_TOOL_NAMES = {
    "list_alerts", "create_alert", "delete_alert",
    "get_stock_data", "get_technical_indicators",
    "detect_sector_contagion", "get_earnings_calendar",
    "get_earnings_reaction_history",
}
ALERTS_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _ALERTS_TOOL_NAMES]

# Subconjunto pro "Veredito do Dia" -- síntese cruzando Cenários, Técnicos,
# Plano de Saída, Earnings, Macro e (opcionalmente) Backtest. Sem
# save_observation/alertas/EDGAR: essa run só lê e resume, não gerencia nada.
_VEREDITO_TOOL_NAMES = {
    "get_scenario_status", "get_backtest_summary",
    "get_exit_plan_items", "get_technical_indicators", "get_stock_data",
    "get_macro_indicators", "get_earnings_calendar", "get_earnings_reaction_history",
    "get_fear_greed_index", "detect_sector_contagion", "get_analyst_ratings",
    # get_fundamentals_valuation faltava aqui -- sem ela o Veredito não tinha
    # como pesar valuation (P/E, PEG, DCF) contra a técnica/sentimento na
    # recomendação de uma posição, mesmo quando o preço já descontou boa
    # parte do risco (ex.: múltiplo historicamente baixo pro setor).
    "get_fundamentals_valuation",
}
VEREDITO_TOOLS = [tool for tool in t.TOOLS if tool["name"] in _VEREDITO_TOOL_NAMES]


# run_portfolio() e' compartilhada pelos modos "portfolio" (carteira real do
# usuario), "coal" (cesta fixa HCC/AMR/ARCH/CEIX/BTU) e "ai" (cesta fixa
# NVDA/ARM/GOOGL/META/MSFT/AMD/PLTR/SMCI) -- sem essa tabela, o prompt e o
# titulo do relatorio final diziam "Carteira" mesmo pras cestas setoriais
# fixas, que nao sao os ativos que o usuario de fato tem (e o e-mail sai com
# esse mesmo titulo errado, ver runner.ts::sendReportEmail).
_PORTFOLIO_MODE_TEXT = {
    "portfolio": {"focus": "carteira", "assets": "Ativos da carteira", "title": "Carteira"},
    "coal":      {"focus": "cesta do setor de carvão", "assets": "Ativos da cesta de carvão", "title": "Setor Carvão"},
    "ai":        {"focus": "cesta do setor de IA", "assets": "Ativos da cesta de IA", "title": "Setor IA"},
}


def _system_stable_portfolio(tickers: list[str], mode: str = "portfolio") -> str:
    cfg = _PORTFOLIO_MODE_TEXT.get(mode, _PORTFOLIO_MODE_TEXT["portfolio"])
    return f"""Você é um analista de ações fazendo uma análise RÁPIDA focada na {cfg['focus']}.
{cfg['assets']}: {", ".join(tickers)}.

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
## ⚡ {cfg['title']} — Análise Rápida {{data}}
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

# Piso do preflight do lado Node (lib/report-preflight.ts::MIN_CHARS): abaixo
# disso o e-mail é BLOQUEADO e o usuário não recebe nada.
#
# Precisa ser o piso daqui também, senão as duas checagens discordam no pior
# sentido possível: o agente aceita um texto de, digamos, 400 caracteres (o
# limiar dele pra 7 ativos era 280), encerra satisfeito e não cobra reescrita
# -- e o preflight, que só roda depois, joga fora a run inteira. O agente é o
# único ponto que ainda pode CONSERTAR pedindo de novo; deixá-lo com a régua
# mais frouxa que a do porteiro desperdiça essa última chance.
PREFLIGHT_MIN_CHARS = 800


def _min_report_chars(min_observations: int) -> int:
    return max(
        PREFLIGHT_MIN_CHARS,
        MIN_REPORT_CHARS_FLOOR,
        MIN_REPORT_CHARS_PER_TICKER * min_observations,
    )


# Motivos de parada que significam "eu cortei a resposta no meio", por
# provedor. Anthropic: "max_tokens". Camada OpenAI-compat: "length".
_MOTIVOS_DE_CORTE = {"max_tokens", "length"}


def _avisar_truncamento(resp, tool_use_blocks: list, max_tokens: int, turn: int) -> None:
    """Grita no stderr quando a resposta foi cortada por limite de tokens.

    O corte é invisível por construção: provider.py achata o motivo de parada
    pra "tool_use"/"end_turn", então "o modelo terminou" e "eu cortei no meio"
    chegam aqui idênticos. Quando o corte pega o JSON de input de um tool_use,
    o bloco chega com input {} e a ferramenta estoura TypeError de argumento
    faltando -- a três camadas de distância da causa real.

    Visto em produção 03/08: turnos emitindo 9 e 12 chamadas paralelas com
    max_tokens em 4096, get_technical_indicators e get_short_interest falhando
    por falta de `ticker`, e a run terminando com 0 de 8 observações. Nada nos
    logs ligava uma coisa à outra.

    Só diagnóstico: não altera o fluxo. O tratamento dos blocos órfãos já
    existe logo abaixo.
    """
    if getattr(resp, "raw_stop_reason", "") not in _MOTIVOS_DE_CORTE:
        return
    sem_args = [b.name for b in tool_use_blocks if not b.input]
    print(
        f"[agent] turno {turn + 1}: resposta CORTADA por limite de tokens "
        f"(max_tokens={max_tokens}, motivo={resp.raw_stop_reason}, "
        f"{len(tool_use_blocks)} tool_use no turno)"
        + (f" -- chamadas com input vazio, provavelmente truncadas: {sem_args}"
           if sem_args else ""),
        file=sys.stderr, flush=True,
    )


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
    report_snapshot: dict | None = None,
    required_tickers: list[str] | None = None,
) -> str:
    from .provider import TextBlock, ToolUseBlock

    final_text = ""
    observations_saved = 0
    # Identidade, não contagem. O prompt exige save_observation de CADA posição
    # da carteira ("NUNCA pule o save_observation de uma posição da carteira",
    # FASE 2), e `observations_saved >= min_observations` só aproxima isso por
    # dois lados: não diz QUAL ativo faltou, e deixa passar uma run que salvou o
    # número certo de observações cobrindo o conjunto errado (um líder de
    # contágio de fora da carteira no lugar de uma posição).
    #
    # Visto em produção 03/08: a run salvou 7 observações (6 posições + HCC, um
    # líder de contágio), a cobrança disse "chame save_observation para os
    # ativos que faltam" sem nomear nenhum, o modelo não adivinhou quais eram, e
    # a run inteira -- já paga, com todos os dados coletados -- virou "Análise
    # incompleta". Nomear os pendentes transforma um pedido indecifrável num
    # pedido mecânico.
    requeridos = [tk.strip().upper() for tk in (required_tickers or []) if tk.strip()]
    if requeridos:
        # Com lista de ativos, ela É o piso -- evita que caller e piso divirjam.
        min_observations = len(requeridos)
    observed_tickers: set[str] = set()

    def _pendentes() -> list[str]:
        return [tk for tk in requeridos if tk not in observed_tickers]

    def _faltando() -> int:
        if requeridos:
            return len(_pendentes())
        # Sem lista de ativos (runs de tema livre, sem cesta fixa) não há
        # identidade a conferir -- o piso segue sendo a contagem.
        return max(0, min_observations - observations_saved)

    # DOIS orçamentos separados, não um só. Eles cobram falhas diferentes --
    # "não registrou as observações" e "não escreveu o relatório" -- e um
    # contador único deixa a primeira faminta a segunda.
    #
    # Visto em produção 03/08: o modelo terminou dois turnos seguidos sem
    # salvar observação (gastou as duas cobranças), no turno 10 salvou as nove
    # de uma vez, e no turno 11 devolveu texto curto. Aí não havia mais
    # orçamento pra pedir o relatório -- a run coletou tudo, salvou tudo,
    # custou US$ 0,60 e foi descartada a UM pedido da linha de chegada.
    nudges_obs_left = 2
    nudges_report_left = 2
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
        _avisar_truncamento(resp, tool_use_blocks, max_tokens, turn)
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
                        tk_salvo = str(block.input.get("ticker") or "").strip().upper()
                        if tk_salvo:
                            observed_tickers.add(tk_salvo)
                    else:
                        print(f"[agent] save_observation falhou: {result}", flush=True)
                if report_snapshot is not None:
                    # Snapshot montado do que o modelo REALMENTE recebeu, em vez
                    # de refazer as chamadas depois: refazer gastaria orçamento
                    # de tempo da run e ainda poderia divergir do que ele viu.
                    try:
                        collect_tool_result(report_snapshot, block.name, block.input, result)
                    except Exception as e:
                        # Coleta é acessória -- nunca pode derrubar a run.
                        print(f"[report_validator] coleta falhou em {block.name}: {e}",
                              file=sys.stderr, flush=True)
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
            missing = _faltando()
            if require_observations and missing > 0 and nudges_obs_left > 0:
                nudges_obs_left -= 1
                if progress_callback:
                    progress_callback(f"{step_prefix}Cobrando save_observation pendente...")
                pendentes = _pendentes()
                if pendentes:
                    cobranca = (
                        f"Faltam as observações destes ativos: {', '.join(pendentes)}. "
                        "Chame save_observation AGORA para CADA um deles (resumo "
                        "curto + sentimento) e só então escreva o relatório final."
                    )
                else:
                    cobranca = (
                        f"Você encerrou COM APENAS {observations_saved} de pelo menos "
                        f"{min_observations} save_observation esperadas. Chame "
                        "save_observation AGORA para os ativos que faltam (resumo "
                        "curto + sentimento) e só então escreva o relatório final."
                    )
                messages.append({"role": "user", "content": (
                    "A análise só é válida após registrar a observação de CADA "
                    "ativo exigido. " + cobranca
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
            # `missing <= 0` é pré-requisito: com observação faltando, pedir
            # "escreva o relatório" é o pedido errado -- o fluxo manda registrar
            # tudo ANTES de escrever. Enquanto os dois orçamentos eram um só
            # isso nunca aparecia (o de observação esgotava primeiro e o
            # `continue` não era alcançado); ao separá-los, sem esta condição a
            # run passaria a cobrar relatório de quem ainda nem coletou.
            if (
                require_observations
                and missing <= 0
                and not looks_like_report
                and nudges_report_left > 0
            ):
                nudges_report_left -= 1
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
                pendentes = _pendentes()
                # Nomear os ativos aqui também: esta é a mensagem que o usuário
                # lê no lugar do relatório, e "as observações pendentes" não diz
                # a ele (nem a quem for depurar) o que exatamente faltou.
                detalhe = (
                    f" Ficaram sem observação: {', '.join(pendentes)}."
                    if pendentes else ""
                )
                final_text = (
                    "Análise incompleta nesta execução: o modelo não conseguiu "
                    "registrar as observações pendentes mesmo após ser cobrado, "
                    "e não produziu um relatório final confiável." + detalhe
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

    if require_observations and _faltando() > 0:
        pendentes = _pendentes()
        if pendentes:
            # Com lista exigida o aviso é sobre IDENTIDADE, e só. Misturar com a
            # contagem produzia frase incoerente -- visto em produção 04/08:
            # "apenas 11 de pelo menos 8 observações esperadas foram salvas.
            # Sem observação: AVGO, MRVL, SKHY." Onze é mais que oito; quem lê
            # não tem como saber que 11 e 8 falam de conjuntos diferentes (o
            # total salvo inclui ativos de fora da carteira).
            final_text += (
                f"\n\n[Aviso: {len(pendentes)} ativo(s) exigido(s) ficaram sem "
                f"observação nesta execução: {', '.join(pendentes)}. "
                f"({observations_saved} observações foram salvas no total.)]"
            )
        else:
            final_text += (
                f"\n\n[Aviso: apenas {observations_saved} de pelo menos "
                f"{min_observations} observações esperadas foram salvas nesta "
                f"execução.]"
            )
    return final_text


# IV ATM da última run, por ticker. Mesmo padrão do get_run_usage(): o loop
# acumula, o run_agent.py lê e emite no stdout, e o runner.ts persiste.
#
# Existe porque o gate de IV precisa comparar a IV de hoje com o histórico do
# PRÓPRIO papel (IV Rank), e o yfinance só devolve a cadeia de opções ao vivo --
# não há série histórica pra consultar nem como preencher retroativamente. A
# única forma de ter rank é começar a gravar o que a run já coletou de graça.
_ULTIMA_IV: dict = {}


def _registrar_iv(snapshot: dict) -> None:
    """Copia IV ATM + ATR% do snapshot da run pro buffer de emissão.

    Só entra ticker com os DOIS números: o consumidor (IV Rank) precisa da IV,
    e o atr_pct vai junto porque é o proxy usado enquanto a série não tem
    tamanho pra rank -- guardar os dois evita ter que recalcular depois.

    Segunda barreira contra IV implausível, além da que já existe na origem
    (tools.py::_atm_iv_pct). Vale a redundância porque aqui o custo do erro é
    diferente e permanente: uma linha errada em iv_history não volta atrás e
    não dá pra distinguir de uma boa depois -- ela contamina o IV Rank de todo
    dia futuro que olhar para trás. Em 03/08 sete tickers foram gravados com
    IV entre 0,78 e 2,61 antes de existir qualquer checagem.
    """
    _ULTIMA_IV.clear()
    opcoes = snapshot.get("options", {})
    tecnicos = snapshot.get("technicals", {})
    for ticker, o in opcoes.items():
        iv = o.get("atm_iv_pct")
        if not isinstance(iv, (int, float)) or isinstance(iv, bool):
            continue
        if not (t.IV_ATM_MIN_PCT <= iv <= t.IV_ATM_MAX_PCT):
            print(
                f"[iv] {ticker}: atm_iv_pct={iv} fora da faixa plausível "
                f"({t.IV_ATM_MIN_PCT}-{t.IV_ATM_MAX_PCT}%) -- não gravado",
                file=sys.stderr, flush=True,
            )
            continue
        atr = (tecnicos.get(ticker) or {}).get("atr_pct")
        _ULTIMA_IV[ticker] = {
            "atm_iv_pct": iv,
            "atr_pct": atr if isinstance(atr, (int, float)) else None,
        }


def get_last_iv_snapshot() -> dict:
    """{ticker: {"atm_iv_pct": float, "atr_pct": float|None}} da última run."""
    return dict(_ULTIMA_IV)


# ── Retry de correção: pegar o texto sem a fala que vem junto ─────────────────
#
# Os dois validadores (relatório diário e Veredito) mandam um retry pedindo o
# texto reescrito já corrigido. Os dois prompts já dizem "reescreva o relatório
# completo", e o modelo obedece -- e ainda assim põe um "Compreendido. Segue o
# relatório corrigido apenas no rótulo e justificativa de SKHY, mantendo o
# restante da análise e formato original." na frente.
#
# Visto em produção 03/08: essa frase virou a PRIMEIRA LINHA do relatório
# diário que o usuário lê, com direito a citar o ticker corrigido e expor o
# mecanismo interno de validação a quem só queria a análise do dia.
#
# Mesma divisão de trabalho de sempre: o prompt pede, o código garante.

# Âncora curta demais casa por acaso dentro do próprio preâmbulo (que costuma
# conter "o relatório", "a análise"), então só serve de referência uma linha
# com corpo.
_ANCORA_MIN_CHARS = 20


def _primeira_linha_util(texto: str) -> str:
    for linha in texto.splitlines():
        limpa = linha.strip()
        if len(limpa) >= _ANCORA_MIN_CHARS:
            return limpa
    return ""


def _sem_preambulo(corrigido: str, original: str) -> str:
    """Corta a fala de acompanhamento antes do texto reescrito.

    A âncora é a abertura do texto ORIGINAL: o retry só troca rótulo e
    justificativa, então o começo tem que reaparecer igual. Quando não
    reaparece (o modelo reescreveu o título), devolve intacto -- errar pra menos
    aqui só mantém o comportamento de antes, enquanto errar pra mais
    decapitaria um relatório legítimo.
    """
    ancora = _primeira_linha_util(original)
    if not ancora:
        return corrigido
    idx = corrigido.find(ancora)
    return corrigido[idx:].lstrip() if idx > 0 else corrigido


# Avisos que o LOOP anexa ao final do texto -- observação faltando, limite de
# turnos. Não são texto do modelo, e o modelo não os reproduz quando reescreve.
_AVISO_RE = re.compile(r"\[Aviso:[^\]]*\]")


def _preservar_avisos(corrigido: str, original: str) -> str:
    """Reanexa os avisos do sistema que o retry de correção apagou.

    O retry pede o relatório reescrito, e o modelo reescreve o que ele considera
    SEU. Os blocos [Aviso: ...] foram anexados pelo loop depois que ele
    terminou, então não estão no texto dele e somem na reescrita -- levando
    junto a única sinalização de que algo saiu errado.

    Visto em produção 04/08: o deadline forçou o turno final sem ferramentas, a
    run acabou sem gravar observação nenhuma (elas viraram texto dentro do
    relatório), o loop anexou o aviso, o validador de rótulo disparou por causa
    do HCC -- e o relatório chegou ao usuário sem nenhuma menção de que a
    memória do dia não tinha sido salva.
    """
    faltando = [a for a in _AVISO_RE.findall(original) if a not in corrigido]
    return corrigido + "".join(f"\n\n{aviso}" for aviso in faltando)


def _texto_da_correcao(fix_resp, original: str) -> str | None:
    """Primeiro bloco de texto do retry, sem o preâmbulo e com os avisos do
    sistema preservados. None se não veio texto nenhum -- aí o chamador fica
    com o original."""
    from .provider import TextBlock
    for block in fix_resp.content:
        if isinstance(block, TextBlock) and block.text.strip():
            return _preservar_avisos(_sem_preambulo(block.text, original), original)
    return None


# ── Teto de turnos ────────────────────────────────────────────────────────────
#
# O teto existe pra parar loop desgovernado, NÃO pra conter custo nem tempo --
# esses dois já têm freio próprio (o teto diário de gasto em agent-budget.ts e o
# SOFT_DEADLINE_TS). Um teto apertado não economiza: ele mata a run já paga
# pouco antes da linha de chegada, que é o pior desfecho possível.
#
# Produção 04/08: o teto era `len(PORTFOLIO_TICKERS) * 2 + 6` = 22, derivado da
# carteira (8 ativos). Só que a análise diária cobre config.TICKERS, e naquele
# dia eram 28 -- get_stock_data em 28, técnicos em 24, candles em 20, short em
# 20, analistas em 19, opções em 19. A run bateu os 22 turnos exatos, custou
# US$ 0,96 e o preflight bloqueou o e-mail por relatório vazio. O teto foi
# calculado sobre um conjunto que não é o que a run percorre.
#
# O que consome turno, no pior caso realista:
#   - FASE 1 (contexto de mercado), em lote                     ~2
#   - as ~8 categorias de dado, uma resposta em lote cada       ~8
#   - save_observation: 1 por ativo quando o modelo NÃO agrupa  ~N
#     (o prompt pede lote, e modelos fracos ignoram -- visto
#      nesta mesma run: 10 turnos seguidos de uma observação)
#   - relatório final + retry de correção da rubrica            ~2
#   - margem pra troca de provider e cobranças                  ~4
TURNOS_FIXOS = 16


def _turnos_para_cobertura() -> int:
    """Piso de turnos para a cobertura REAL da run diária.

    Desde que o relatório passou a cobrir só a carteira (Grupo A =
    PORTFOLIO_TICKERS, sem líderes de contágio nem Grupo B), config.TICKERS é
    um teto folgado, não o tamanho real da cobertura -- mantido de propósito
    como headroom generoso: apertar pro tamanho exato da carteira foi o que
    já causou run truncada em produção 04/08 (ver comentário de TURNOS_FIXOS
    acima) quando a cobertura real acabou sendo maior que o cálculo.
    """
    return len(config.TICKERS) + TURNOS_FIXOS


# ── Run modes ─────────────────────────────────────────────────────────────────

def run(progress_callback=None) -> str:
    client = _get_client()
    max_turns = max(config.MAX_AGENT_TURNS, _turnos_para_cobertura())
    model = client.models["full"]
    system = build_system_prompt_blocks()
    user_msg = (
        "Faça a análise pré-mercado de hoje para os ativos sob cobertura, "
        "seguindo seu fluxo. Use as ferramentas conforme necessário e registre "
        "as observações do dia ao final."
    )
    snapshot = new_snapshot()

    final_text = _agent_loop(
        client=client,
        model=model,
        system=system,
        tools=t.TOOLS,
        messages=[{"role": "user", "content": user_msg}],
        max_turns=max_turns,
        max_tokens=config.MAX_TOKENS,
        progress_callback=progress_callback,
        require_observations=True,
        # Piso seguro: as posições da carteira SEMPRE recebem save_observation
        # (completa ou reduzida, pela regra de economia) — os líderes de
        # contágio fora da carteira somam mais chamadas, mas sua contagem
        # exata só é conhecida em runtime, então não entram no piso.
        min_observations=len(config.PORTFOLIO_TICKERS),
        # A mesma lista, agora por identidade: a cobrança nomeia quem faltou em
        # vez de só contar (ver o bloco de `requeridos` em _agent_loop).
        required_tickers=config.PORTFOLIO_TICKERS,
        deadline_ts=config.SOFT_DEADLINE_TS,
        report_snapshot=snapshot,
    )

    _registrar_iv(snapshot)

    # A rubrica de rótulo vive no prompt, mas prompt é pedido, não garantia --
    # aqui os mesmos gates viram checagem determinística, com um retry de
    # correção (mesma mecânica de run_veredito).
    lrep = lint_report(final_text, snapshot)
    if lrep.has_errors:
        print(f"[report_validator] rótulos violando a rubrica, tentando 1 retry:\n"
              f"{lrep.summary()}", file=sys.stderr, flush=True)
        if progress_callback:
            progress_callback("Corrigindo rótulos que violam a rubrica...")
        try:
            fix_resp = client.create(
                model=model,
                max_tokens=config.MAX_TOKENS,
                system=system,
                tools=[],
                messages=[
                    {"role": "user", "content": user_msg},
                    {"role": "assistant", "content": final_text},
                    {"role": "user", "content": correction_prompt(lrep)},
                ],
            )
            corrigido = _texto_da_correcao(fix_resp, final_text)
            if corrigido:
                final_text = corrigido
        except Exception as e:
            # Igual ao veredito: falha no retry não derruba o relatório inteiro
            # -- fica o texto original, com as violações já logadas acima.
            print(f"[report_validator] retry de correção falhou: {e}", file=sys.stderr, flush=True)

    return final_text


def run_portfolio(progress_callback=None, mode: str = "portfolio") -> str:
    env_tickers = os.environ.get("AGENT_PORTFOLIO_TICKERS", "")
    tickers = [tk.strip().upper() for tk in env_tickers.split(",") if tk.strip()] or config.PORTFOLIO_TICKERS
    client = _get_client()
    today = _today_brt_str()
    cfg = _PORTFOLIO_MODE_TEXT.get(mode, _PORTFOLIO_MODE_TEXT["portfolio"])
    system = _system_stable_portfolio(tickers, mode).replace("{data}", today) + "\n\n" + _system_volatile()
    # Allow more turns and tokens for larger ticker sets (coal=5, ai=8)
    n = len(tickers)
    max_turns = max(20, n * 4)
    max_tokens = max(config.MAX_TOKENS, 8192)
    return _agent_loop(
        client=client,
        model=client.models["flash"],
        system=system,
        tools=PORTFOLIO_TOOLS,
        messages=[{"role": "user", "content": f"Faça a análise rápida da {cfg['focus']} agora."}],
        max_turns=max_turns,
        max_tokens=max_tokens,
        progress_callback=progress_callback,
        step_prefix=f"[{cfg['title']}] ",
        require_observations=True,
        min_observations=n,
        required_tickers=tickers,
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


def run_news(progress_callback=None) -> str:
    client = _get_client()
    return _agent_loop(
        client=client,
        model=client.models["flash"],
        system=build_news_prompt(),
        tools=NEWS_TOOLS,
        messages=[{"role": "user", "content": "Faça a varredura rápida de notícias agora."}],
        max_turns=min(config.MAX_AGENT_TURNS, 6),
        max_tokens=config.MAX_TOKENS_PREMARKET,
        progress_callback=progress_callback,
        step_prefix="[Notícias] ",
    )


def run_exit_plan_review(progress_callback=None) -> str:
    client = _get_client()
    return _agent_loop(
        client=client,
        model=client.models["flash"],
        system=build_exit_plan_prompt(),
        tools=EXIT_PLAN_TOOLS,
        messages=[{"role": "user", "content": "Reavalie o plano de saída agora com dados atuais."}],
        max_turns=max(config.MAX_AGENT_TURNS, 24),
        max_tokens=config.MAX_TOKENS,
        progress_callback=progress_callback,
        step_prefix="[Plano de Saída] ",
        deadline_ts=config.SOFT_DEADLINE_TS,
    )


def run_alerts_management(progress_callback=None) -> str:
    client = _get_client()
    system = _system_blocks(build_alerts_management_prompt(), _system_volatile())
    return _agent_loop(
        client=client,
        model=client.models["flash"],
        system=system,
        tools=ALERTS_TOOLS,
        messages=[{"role": "user", "content": "Revise e calibre os alertas de preço agora."}],
        max_turns=min(config.MAX_AGENT_TURNS, 10),
        max_tokens=config.MAX_TOKENS_PREMARKET,
        progress_callback=progress_callback,
        step_prefix="[Alertas] ",
        deadline_ts=config.SOFT_DEADLINE_TS,
    )


# ── Validação factual do Veredito do Dia ──────────────────────────────────────
#
# Visto em produção (31/07): o Veredito citou preço/RSI defasados (RSI do ARM
# 2 dias atrás do quote), sinal de percentual trocado (AVGO informado -0.36%
# quando o recálculo dava +0.37%), fade intradiário ignorado (SKHY/ARM
# fecharam bem abaixo do high do dia — padrão de distribuição, não citado) e
# alucinações no texto gerado (dia da semana errado, earnings atribuído a uma
# data em que ainda não tinha ocorrido). veredito_validator.py cobre os dois
# lados: valida os DADOS antes do prompt (Fase 1) e faz lint do TEXTO gerado
# depois (Fase 2), com um retry único de correção quando o lint acha erro.


def _fetch_veredito_quote(ticker: str) -> dict | None:
    """OHLC do último pregão fechado, direto do yfinance -- separado de
    tools.get_stock_data (ferramenta do LLM, baseada em fast_info "agora",
    que muda durante o pregão) porque o validador precisa do open/high do
    MESMO candle usado no change_percent pra detectar fade intradiário."""
    try:
        hist = yf.Ticker(ticker).history(period="5d")
        # `price` sai de iloc[-1]: com a barra de hoje ainda sem Close, ele
        # viraria NaN. Aqui o filtro é seguro porque prev_close sai de iloc[-2]
        # DA MESMA série filtrada -- os dois deslizam juntos e continuam sendo
        # "último fechamento" e "o anterior a ele".
        hist = market_data_provider.sem_barra_incompleta(hist)
        if hist.empty or len(hist) < 2:
            return None
        last = hist.iloc[-1]
        price = float(last["Close"])
        prev_close = float(hist["Close"].iloc[-2])
        volume = last["Volume"]
        return {
            "price": round(price, 4),
            "previous_close": round(prev_close, 4),
            "open": round(float(last["Open"]), 4),
            "high": round(float(last["High"]), 4),
            "low": round(float(last["Low"]), 4),
            "change_percent": round((price - prev_close) / prev_close * 100, 4) if prev_close else None,
            "volume": int(volume) if volume == volume else None,  # NaN != NaN
            "as_of": hist.index[-1].strftime("%Y-%m-%d"),
        }
    except Exception as e:
        print(f"[veredito_validator] falha ao buscar quote de {ticker}: {e}", file=sys.stderr, flush=True)
        return None


def _build_veredito_snapshot(tickers: list[str]) -> dict:
    """Monta o snapshot no formato esperado por validate_snapshot()/
    lint_veredito(). as_of é derivado do dado REAL baixado (a data mais
    recente entre os quotes buscados), nunca de "hoje" cru -- num fim de
    semana isso derrubaria RSI_STALE pra TODO ticker, já que a tolerância de
    frescor é zero (ver comentário no topo de veredito_validator.py)."""
    quotes: dict = {}
    for tk in tickers:
        q = _fetch_veredito_quote(tk)
        if q:
            quotes[tk] = q

    technicals: dict = {}
    for tk in tickers:
        ti = t.get_technical_indicators(tk)
        if "rsi_14" in ti and ti.get("rsi_date"):
            # pct_above_sma50 entra pro check DISTRIBUICAO_INVERTIDA do lint:
            # sem ele não dá pra distinguir topo de fundo, que é exatamente o
            # que o veredito de 01/08 errou em ARM e MRVL.
            technicals[tk] = {
                "rsi": ti["rsi_14"],
                "rsi_date": ti["rsi_date"],
                "pct_above_sma50": ti.get("pct_above_sma50"),
            }

    earnings: dict = {}
    for row in t.get_earnings_calendar(tickers):
        if row.get("next_earnings_date"):
            earnings[row["ticker"]] = row["next_earnings_date"]

    if quotes:
        as_of = max(q["as_of"] for q in quotes.values())
    else:
        # Sem nenhum quote disponível (falha de rede geral) -- último dia
        # útil como piso seguro, só pra não travar a validação inteira.
        d = _now_brt().date()
        while d.weekday() >= 5:
            d -= datetime.timedelta(days=1)
        as_of = d.isoformat()

    return {"as_of": as_of, "quotes": quotes, "technicals": technicals, "earnings": earnings}


def _capex_do_snapshot() -> dict | None:
    """Resumo do capex dos hiperescaladores para o snapshot do Veredito.

    Lido do OVERLAY (escrito pelo checker semanal), nunca da rede: o veredito
    não pode ficar refém de uma cotação de capex no meio da geração. Ausente
    ou ilegível vira None -- o prompt simplesmente não cita o que não tem.
    """
    try:
        from .capex_hyperscalers import ler_overlay
        dados = ler_overlay()
        if not dados:
            return None
        r = dados.get("resumo") or {}
        return r if r.get("disponivel") else None
    except Exception as e:
        print(f"[veredito] capex indisponível: {e}", file=sys.stderr, flush=True)
        return None


def _folego_do_snapshot(tickers: list[str]) -> dict | None:
    """Fôlego de caixa por ticker para o snapshot do Veredito.

    Lido do OVERLAY (escrito pelo checker semanal), nunca da rede -- mesma
    regra do capex: o veredito não pode ficar refém de uma busca de balanço no
    meio da geração. Só entram os tickers do dia que têm dado; ausência vira
    ausência, e o prompt simplesmente não cita o que não tem."""
    try:
        from .folego_de_caixa import ler_overlay
        dados = ler_overlay()
        if not dados:
            return None
        resumo = dados.get("resumo") or {}
        saida = {tk: resumo[tk] for tk in tickers
                 if (resumo.get(tk) or {}).get("disponivel")}
        return saida or None
    except Exception as e:
        print(f"[veredito] fôlego de caixa indisponível: {e}", file=sys.stderr, flush=True)
        return None


def run_veredito(progress_callback=None) -> str:
    client = _get_client()
    tickers = config.PORTFOLIO_TICKERS
    model = client.models["flash"]
    system = build_veredito_prompt()

    if progress_callback:
        progress_callback("[Veredito] Validando dados antes da análise...")
    snapshot = _build_veredito_snapshot(tickers)
    # Capex dos hiperescaladores: a tese de IA/data center como FATO datado
    # no snapshot, do mesmo jeito que preço e RSI -- e portanto sujeita ao
    # validador. Sem isso, "a tese está ganhando tração" só existiria como
    # opinião do modelo.
    capex = _capex_do_snapshot()
    if capex:
        snapshot["capex_hyperscalers"] = capex
    # Fôlego de caixa: o outro lado do balanço da mesma tese. O capex diz
    # quanto o comprador de data center investe; isto diz quanto o fornecedor
    # aguenta esperando o investimento chegar.
    folego = _folego_do_snapshot(tickers)
    if folego:
        snapshot["folego_de_caixa"] = folego
    vrep = validate_snapshot(snapshot)
    if vrep.issues:
        print(f"[veredito_validator] snapshot issues:\n{vrep.summary()}", file=sys.stderr, flush=True)

    user_msg = "Gere o veredito do dia agora, cruzando todas as ferramentas do fluxo obrigatório."
    prompt_block = vrep.prompt_block()
    if prompt_block:
        user_msg += "\n\n" + prompt_block

    final_text = _agent_loop(
        client=client,
        model=model,
        system=system,
        tools=VEREDITO_TOOLS,
        messages=[{"role": "user", "content": user_msg}],
        max_turns=max(config.MAX_AGENT_TURNS, 20),
        max_tokens=config.MAX_TOKENS,
        progress_callback=progress_callback,
        step_prefix="[Veredito] ",
        deadline_ts=config.SOFT_DEADLINE_TS,
    )

    # Desde 20/08/2026 a validação cobre o lint da prosa E o bloco
    # estruturado do fim (decisão por ticker em JSON) -- inclusive a
    # coerência entre os dois. O retry recebe tudo num relatório só.
    lrep = validar_veredito_completo(final_text, snapshot)
    if lrep.has_errors:
        print(f"[veredito_validator] lint errors, tentando 1 retry:\n{lrep.summary()}", file=sys.stderr, flush=True)
        if progress_callback:
            progress_callback("[Veredito] Corrigindo erros factuais detectados...")
        try:
            fix_resp = client.create(
                model=model,
                max_tokens=config.MAX_TOKENS,
                system=system,
                tools=[],
                messages=[
                    {"role": "user", "content": user_msg},
                    {"role": "assistant", "content": final_text},
                    {"role": "user", "content": (
                        "CORRIJA os seguintes erros factuais e reescreva o "
                        "veredito completo já corrigido (mesmo formato de "
                        "antes, não apenas as partes erradas, incluindo o "
                        "bloco ```json final). Comece direto pelo veredito, "
                        "sem nenhuma frase de introdução:\n"
                        + lrep.summary()
                    )},
                ],
            )
            corrigido = _texto_da_correcao(fix_resp, final_text)
            if corrigido:
                final_text = corrigido
        except Exception as e:
            # Falha no retry não pode derrubar o veredito inteiro -- fica
            # com o texto original (com os erros já logados acima pro
            # operador investigar) em vez de propagar a exceção.
            print(f"[veredito_validator] retry de correção falhou: {e}", file=sys.stderr, flush=True)

    # Se depois do retry ainda não há bloco estruturado VÁLIDO, o veredito
    # sai marcado como degradado -- visível no texto, não só no log. A
    # alternativa (segurar o veredito) puniria o operador pela falha do
    # modelo; a degradação declarada segue a convenção do repo: dado
    # estimado/incompleto nunca se apresenta como completo.
    bloco, erro_bloco = extrair_bloco_estruturado(final_text)
    if bloco is None:
        detalhe = f" ({erro_bloco})" if erro_bloco else ""
        print(f"[veredito_validator] veredito SEM bloco estruturado após retry{detalhe}",
              file=sys.stderr, flush=True)
        final_text += (
            "\n\n> ⚠️ **Leitura degradada**: este veredito saiu sem o bloco "
            "estruturado de decisão por ticker (o modelo não o produziu nem "
            "no retry). As checagens determinísticas rodaram só sobre a "
            "prosa." )

    return final_text


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

    # Emite USAGE:{json} com tokens/custo de TODAS as chamadas desta mensagem
    # (turnos do agente + geração de título, se houve) -- mesmo padrão de
    # emit_usage() em run_agent.py, só que aqui a run inteira é uma única
    # mensagem de chat em vez de um relatório diário.
    from .provider import get_run_usage
    try:
        usage = get_run_usage()
        if usage["calls"] > 0:
            print("USAGE:" + _json.dumps(usage, ensure_ascii=False), flush=True)
    except Exception:
        pass
