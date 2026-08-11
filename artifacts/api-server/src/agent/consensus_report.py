"""
consensus_report.py — relatório diário com consenso entre múltiplos provedores.

Roda o mesmo relatório de pré-mercado da carteira (config.PORTFOLIO_TICKERS)
de forma independente em CONSENSUS_PROVIDERS, e publica -- ticker por ticker --
o rótulo (🟢/🟡/🔴) que a MAIORIA dos provedores deu, junto com a análise de
quem bateu a maioria. Quando os 3 discordam entre si, marca a divergência
explicitamente em vez de decidir sozinho qual provedor está certo.

Por que a coleta de dado não usa tool-calling do LLM (diferente de run()):
desde a PR #240, o Grupo A do relatório diário é sempre config.PORTFOLIO_TICKERS
(fixo), e as categorias de dado por ticker (cotação, técnico, candles, short,
analistas, opções) já são sempre as mesmas -- não sobra nenhuma decisão real
pro modelo tomar nessa etapa. Coletar direto em Python (mesmo padrão que
run_veredito já usa em agent.py) elimina ~10 turnos de tool-calling por
provedor, que é a maior fatia do custo do relatório diário normal -- e o
mesmo pacote de dados é reaproveitado pelos 3 provedores, coletado uma vez só.

Fluxo:
1. gather_portfolio_data() -- coleta os dados uma vez (chamada direta das
   funções de tools.py, sem LLM).
2. run_consensus_report() dispara UMA chamada não-agêntica (sem tools, um
   turno) por provedor em CONSENSUS_PROVIDERS, em paralelo, pedindo o
   relatório completo a partir dos dados já coletados.
3. reconcile() extrai o rótulo de cada ticker de cada relatório (reaproveita
   _secao_do_ticker/_rotulo_da_secao de report_validator.py, a mesma lógica
   que já audita o relatório diário normal) e decide por maioria (2 de 3).
4. _apply_gate_backstop() confere o rótulo majoritário de CADA ticker contra
   os mesmos gates determinísticos que auditam o relatório diário normal
   (report_validator._gates_violados/_rotulo_esperado). Maioria entre
   provedores não é garantia de correção -- dois provedores mais fracos
   podem compartilhar o mesmo viés e vencer o voto contra o primário (já
   visto em produção: gemini-flash inflou 5/5 rótulos numa run de
   fallback). O gate corrige o rótulo quando ele viola a rubrica, mesmo que
   2 de 3 provedores tenham concordado no rótulo errado.
5. _assemble_final_report() monta o relatório final concatenando, por
   ticker, a seção do provedor que bateu o rótulo (já com a correção do
   gate aplicada, se houve) -- com apêndices listando toda divergência entre
   provedores E todo rótulo corrigido pelo gate, pra nunca escolher/ajustar
   um lado em silêncio.

Custo: ~3x o relatório diário normal na parte de LLM (uma chamada de escrita
por provedor), mas SEM repetir a coleta de dado (que é a parte mais numerosa
em chamadas, embora não a mais cara em tokens). Ver PR que introduziu este
módulo para a estimativa de custo mensal.
"""
from __future__ import annotations

import concurrent.futures
import sys

from . import config
from . import tools as t
from .brt import today_brt_str
from .provider import ProviderClient, TextBlock
from .report_validator import (
    EARNINGS_ATIVO_DIAS,
    EARNINGS_CRITICO_DIAS,
    EXTENSAO_SMA200_PCT,
    INFO,
    IV_EVENT_MULTIPLE_ATR,
    LABELS,
    SHORT_ALTO_PCT,
    VERDE,
    VERMELHO,
    _gates_violados,
    _rotulo_da_secao,
    _rotulo_esperado,
    _secao_do_ticker,
    collect_tool_result,
    new_snapshot,
)

# Ordem de prioridade: também é a ordem de desempate quando não há maioria
# (o primeiro da lista que respondeu com sucesso vira o "provedor primário").
CONSENSUS_PROVIDERS = ["anthropic", "deepseek", "gemini"]

# Mínimo de provedores respondendo com sucesso pra reconciliar por maioria --
# com só 1 não há o que comparar, e o modo perde o sentido (seria só rodar o
# relatório normal).
MIN_PROVIDERS_PARA_CONSENSO = 2


# ── Fase 1: coleta de dados (uma vez, compartilhada pelos 3 provedores) ────────


def gather_portfolio_data(tickers: list[str]) -> dict:
    """Mesma cobertura de dado do Grupo A do relatório diário normal (FASE 1
    macro + FASE 2 por ticker), coletada direto -- ver docstring do módulo."""
    macro = {
        "fear_greed": t.get_fear_greed_index(),
        "geopolitical_news": t.get_geopolitical_news(),
        "sector_performance": t.get_sector_performance(),
        "earnings_calendar": t.get_earnings_calendar(tickers),
        "global_snapshot": t.get_global_market_snapshot(),
        "europe_regime": t.get_europe_regime_signal(),
    }
    news = t.get_news(tickers)
    per_ticker = {}
    for tk in tickers:
        per_ticker[tk] = {
            "quote": t.get_stock_data(tk),
            "technicals": t.get_technical_indicators(tk),
            "candles": t.detect_candle_patterns(tk),
            "short_interest": t.get_short_interest(tk),
            "analyst_ratings": t.get_analyst_ratings(tk),
            "options": t.get_options_data(tk),
            "news": news.get(tk, []),
        }
    return {"macro": macro, "tickers": per_ticker}


def _build_snapshot(data: dict, tickers: list[str]) -> dict:
    """Reconstrói o snapshot de gates (mesmo formato que collect_tool_result
    monta dentro do loop agêntico normal) a partir do dado já coletado por
    gather_portfolio_data() -- sem refazer nenhuma chamada de rede, e sem LLM
    no meio: os dicts que tools.py devolve têm o formato exato que
    collect_tool_result já sabe converter."""
    snap = new_snapshot()
    for tk in tickers:
        info = data["tickers"].get(tk) or {}
        collect_tool_result(snap, "get_stock_data", {}, info.get("quote"))
        collect_tool_result(snap, "get_technical_indicators", {}, info.get("technicals"))
        collect_tool_result(snap, "get_options_data", {}, info.get("options"))
        collect_tool_result(snap, "get_short_interest", {}, info.get("short_interest"))
        collect_tool_result(snap, "get_news", {}, {tk: info.get("news") or []})
    collect_tool_result(snap, "get_earnings_calendar", {}, data["macro"].get("earnings_calendar"))
    return snap


# ── Fase 2: prompt do "escritor" (dado já coletado, sem tools) ─────────────────


def _build_writer_prompt(today: str, data: dict, tickers: list[str]) -> str:
    """Mesma rubrica de rótulo do relatório diário normal (agent.py
    ::_system_stable_full, seção RÓTULO POR ATIVO), reescrita pra um contexto
    SEM tool-calling: os limiares numéricos vêm importados direto de
    report_validator.py, então não podem divergir da checagem determinística
    que audita este mesmo texto depois (reconcile() usa os mesmos helpers)."""
    return f"""Você é um analista de ações sênior escrevendo a análise pré-mercado de {today}
para a carteira: {", ".join(tickers)}.

Todos os dados que você precisa já foram coletados e estão abaixo, em JSON.
Você NÃO tem acesso a nenhuma ferramenta -- escreva o relatório só com o que
está aqui. Se um campo estiver ausente ou nulo, diga isso explicitamente em
vez de inventar um número.

**Dados coletados:**
```json
{_json_dump(data)}
```

**RÓTULO POR ATIVO** -- cada seção de ativo abre com UM rótulo de cor, sobre
o setup dos próximos 1-5 pregões (nunca sobre a tese de 6-12 meses, que vai
numa linha própria "Tese (6-12m):"):

  🟢 = setup favorável AGORA
  🟡 = tese ok, timing ruim ou não confirmado -- esperar
  🔴 = risco de curto prazo domina a decisão de hoje

GATES -- só CRÍTICO e ATIVO contam pra cor:

CRÍTICOS:
  • bloco técnico defasado (`rsi_date` do technicals anterior ao `as_of` do quote)
  • `days_until_earnings` ≤ {EARNINGS_CRITICO_DIAS} dias corridos

ATIVOS:
  • `days_until_earnings` entre {EARNINGS_CRITICO_DIAS + 1} e {EARNINGS_ATIVO_DIAS}
  • variação do dia negativa
  • IV de evento: `atm_iv_pct` ≥ {IV_EVENT_MULTIPLE_ATR:.0f} × `atr_pct` (conta exata, já com o multiplicador embutido)
  • `pct_above_sma200` ≥ {EXTENSAO_SMA200_PCT:.0f}% -- SÓ com bloco técnico fresco
  • manchete de risco binário (ITC, antitruste, patente, processo, downgrade/Sell não confirmado)

INFORMATIVO (não muda a cor, só cita no texto):
  • short alto (≥{SHORT_ALTO_PCT:.0f}% do float)

Quantos gates, qual rótulo:
  • ZERO gates que contam → 🟢 permitido (não obrigatório)
  • 🔴 exige: dois críticos, OU um crítico + um ativo, OU três ativos
  • qualquer outro caso com ≥1 gate → 🟡
Um gate só conta se a condição for REALMENTE verdadeira com os números que
você tem -- não invente gate pra justificar um receio que os dados não sustentam.

**Formato do relatório final (Markdown):**
- Uma seção "## Contexto Macro" no início, resumindo fear&greed/geopolítica/setores.
- Uma seção "### TICKER" por ativo (use exatamente o símbolo, ex. "### NVDA"),
  cada uma abrindo com o rótulo de cor + uma linha de justificativa, seguida de
  preço, técnicos, candles, short, analistas, opções, tese (6-12m).
- Seja factual, cite os números que recebeu.
- Tickers com sufixo .SA são B3, cotados em R$, sem pré-mercado."""


def _json_dump(data: dict) -> str:
    import json
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


# ── Fase 3: uma chamada por provedor ────────────────────────────────────────────


def _extract_labels(text: str, tickers: list[str]) -> dict[str, str | None]:
    labels: dict[str, str | None] = {}
    for tk in tickers:
        secao = _secao_do_ticker(text, tk)
        labels[tk] = _rotulo_da_secao(secao) if secao else None
    return labels


def _write_with_provider(provider_name: str, today: str, prompt: str, tickers: list[str]) -> dict:
    try:
        client = ProviderClient(provider_name)
        model = client.models["full"]
        resp = client.create(
            model=model,
            max_tokens=config.MAX_TOKENS,
            system=prompt,
            tools=[],
            messages=[{
                "role": "user",
                "content": f"Escreva o relatório de {today} agora, seguindo o formato pedido.",
            }],
        )
        texto = " ".join(b.text for b in resp.content if isinstance(b, TextBlock)).strip()
        if not texto:
            return {"provider": provider_name, "text": "", "labels": {}, "error": "resposta vazia"}
        return {"provider": provider_name, "text": texto, "labels": _extract_labels(texto, tickers), "error": None}
    except Exception as e:
        print(f"[consensus_report] {provider_name} falhou: {e}", file=sys.stderr, flush=True)
        return {"provider": provider_name, "text": "", "labels": {}, "error": str(e)}


# ── Fase 4: reconciliação ───────────────────────────────────────────────────────


def _primary_among(writer_results: list[dict]) -> dict:
    """Primeiro provedor de CONSENSUS_PROVIDERS presente em writer_results
    (que já deve conter só respostas válidas) -- desempate e fallback quando
    não há maioria."""
    by_name = {r["provider"]: r for r in writer_results}
    for name in CONSENSUS_PROVIDERS:
        if name in by_name:
            return by_name[name]
    return writer_results[0]


def reconcile(writer_results: list[dict], tickers: list[str]) -> dict:
    """writer_results: só respostas VÁLIDAS (sem erro, com texto) -- filtrar
    antes de chamar. Devolve o rótulo/provedor escolhido por ticker e a lista
    de tickers sem maioria clara (2 de N concordando)."""
    per_ticker: dict[str, dict] = {}
    divergencias: list[str] = []

    for tk in tickers:
        contagem: dict[str, int] = {}
        for r in writer_results:
            label = r["labels"].get(tk)
            if label:
                contagem[label] = contagem.get(label, 0) + 1

        if not contagem:
            per_ticker[tk] = {"label": None, "provider": None, "unanimidade": False}
            divergencias.append(tk)
            continue

        label_majoritario, votos = max(contagem.items(), key=lambda kv: kv[1])
        if votos < 2:
            # Nenhum rótulo repetido -- sem maioria. Usa o provedor primário
            # como referência, mas marca a divergência (não decide sozinho
            # quem está certo).
            primario = _primary_among(writer_results)
            per_ticker[tk] = {
                "label": primario["labels"].get(tk),
                "provider": primario["provider"],
                "unanimidade": False,
            }
            divergencias.append(tk)
        else:
            escolhido = next(r for r in writer_results if r["labels"].get(tk) == label_majoritario)
            per_ticker[tk] = {
                "label": label_majoritario,
                "provider": escolhido["provider"],
                "unanimidade": votos == len(writer_results),
            }
            if votos < len(writer_results):
                divergencias.append(tk)

    return {"per_ticker": per_ticker, "divergencias": divergencias}


def _apply_gate_backstop(reconciled: dict, snap: dict, tickers: list[str]) -> list[str]:
    """Confere cada rótulo escolhido por reconcile() contra os gates
    determinísticos de report_validator.py -- os MESMOS que auditam o
    relatório diário normal (lint_report). Maioria entre provedores não é
    garantia de correção: dois provedores mais fracos podem compartilhar o
    mesmo viés e vencer o voto contra o provedor primário (já visto em
    produção -- gemini-flash inflou o rótulo de 5/5 ativos numa run de
    fallback, e só o lint determinístico pegou). O gate é a última palavra
    aqui, não a maioria: corrige o rótulo in-place em reconciled quando ele
    viola a rubrica, e devolve a lista de tickers corrigidos pra o relatório
    final nunca esconder o ajuste."""
    corrigidos: list[str] = []
    for tk in tickers:
        info = reconciled["per_ticker"].get(tk)
        rotulo = info.get("label") if info else None
        if not rotulo:
            continue

        gates = _gates_violados(tk, snap)
        contam = [(sev, d) for sev, d in gates if sev != INFO]
        esperado = _rotulo_esperado(gates)

        violado = (rotulo == VERDE and contam) or (rotulo == VERMELHO and esperado != VERMELHO)
        if violado:
            info["label_original"] = rotulo
            info["label"] = esperado
            info["gate_detalhe"] = "; ".join(f"[{sev}] {d}" for sev, d in contam) or "nenhum"
            corrigidos.append(tk)

    return corrigidos


def _rotular_secao(secao: str, novo_rotulo: str) -> str:
    """Troca a primeira ocorrência de rótulo (🟢/🟡/🔴) na seção pelo rótulo
    corrigido pelo gate -- sem isso o texto exibido divergiria do rótulo que
    o relatório efetivamente reconciliou."""
    for ch in secao:
        if ch in LABELS:
            return secao.replace(ch, novo_rotulo, 1)
    return secao


# ── Fase 5: montagem do relatório final ─────────────────────────────────────────


def _intro_ate_primeiro_ticker(texto: str, tickers: list[str]) -> str:
    """Texto antes da primeira seção de ticker -- normalmente o Contexto
    Macro. Vazio se nenhum ticker foi encontrado (não deveria acontecer com
    um relatório bem formado, mas não trava a montagem se acontecer)."""
    posicoes = []
    for tk in tickers:
        secao = _secao_do_ticker(texto, tk)
        if secao:
            idx = texto.find(secao)
            if idx >= 0:
                posicoes.append(idx)
    if not posicoes:
        return ""
    return texto[: min(posicoes)].strip()


def _assemble_final_report(
    today: str,
    writer_results: list[dict],
    reconciled: dict,
    tickers: list[str],
    corrigidos: list[str] | None = None,
) -> str:
    by_provider = {r["provider"]: r for r in writer_results}
    primary = _primary_among(writer_results)
    corrigidos = corrigidos or []

    partes = [f"## Análise Pré-Mercado (consenso {len(writer_results)} provedores): {today}"]

    intro = _intro_ate_primeiro_ticker(primary["text"], tickers)
    if intro:
        partes.append(intro)

    for tk in tickers:
        info = reconciled["per_ticker"].get(tk, {})
        provider = info.get("provider")
        secao = _secao_do_ticker(by_provider[provider]["text"], tk) if provider else None
        if not secao:
            secao = f"### {tk}\n\n_Nenhum provedor produziu uma seção válida para este ativo._"
        elif tk in corrigidos:
            secao = _rotular_secao(secao, info["label"])
        marca = "" if info.get("unanimidade") else " ⚠️ _(rótulo sem unanimidade -- ver divergências abaixo)_"
        if tk in corrigidos:
            marca += (
                f" 🔧 _(rótulo ajustado de {info['label_original']} para {info['label']} "
                f"por gate determinístico -- ver correções abaixo)_"
            )
        partes.append(secao + (f"\n\n_(consenso: {provider}{marca})_" if provider else ""))

    if reconciled["divergencias"]:
        partes.append("\n---\n### ⚠️ Divergência entre provedores\n")
        for tk in reconciled["divergencias"]:
            votos = ", ".join(
                f"{r['provider']}={r['labels'].get(tk) or '—'}" for r in writer_results
            )
            partes.append(f"- **{tk}**: {votos}")

    if corrigidos:
        partes.append("\n---\n### 🔧 Rótulos corrigidos por gate determinístico\n")
        for tk in corrigidos:
            info = reconciled["per_ticker"][tk]
            partes.append(f"- **{tk}**: {info['label_original']} → {info['label']} — {info['gate_detalhe']}")

    return "\n\n".join(partes)


# ── Orquestrador ─────────────────────────────────────────────────────────────


def run_consensus_report(progress_callback=None) -> str:
    tickers = config.PORTFOLIO_TICKERS
    today = today_brt_str()

    if progress_callback:
        progress_callback("[Consenso] Coletando dados da carteira...")
    data = gather_portfolio_data(tickers)
    prompt = _build_writer_prompt(today, data, tickers)

    if progress_callback:
        progress_callback(f"[Consenso] Consultando {len(CONSENSUS_PROVIDERS)} provedores em paralelo...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(CONSENSUS_PROVIDERS)) as pool:
        futures = [
            pool.submit(_write_with_provider, provider, today, prompt, tickers)
            for provider in CONSENSUS_PROVIDERS
        ]
        writer_results = [f.result() for f in futures]

    validos = [r for r in writer_results if not r["error"] and r["text"]]
    if len(validos) < MIN_PROVIDERS_PARA_CONSENSO:
        falhas = "; ".join(f"{r['provider']}: {r['error']}" for r in writer_results if r["error"])
        raise RuntimeError(
            f"Consenso exige pelo menos {MIN_PROVIDERS_PARA_CONSENSO} provedores respondendo; "
            f"só {len(validos)} de {len(writer_results)} funcionaram. Falhas: {falhas}"
        )

    if progress_callback:
        progress_callback("[Consenso] Reconciliando rótulos entre provedores...")
    reconciled = reconcile(validos, tickers)

    snap = _build_snapshot(data, tickers)
    corrigidos = _apply_gate_backstop(reconciled, snap, tickers)
    if corrigidos and progress_callback:
        progress_callback(f"[Consenso] {len(corrigidos)} rótulo(s) corrigido(s) por gate determinístico...")

    return _assemble_final_report(today, validos, reconciled, tickers, corrigidos)
