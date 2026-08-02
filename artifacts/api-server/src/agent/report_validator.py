"""
report_validator.py — enforcement mecânico da rubrica de rótulo do relatório
diário (agent.py::_system_stable_full, seção "RÓTULO POR ATIVO").

Por que existe: o Veredito do Dia tem validador desde que citou preço/RSI
defasado em produção (veredito_validator.py), mas o RELATÓRIO DIÁRIO só tinha
checagem estrutural — contagem de save_observation e tamanho mínimo. A classe
de erro que sobrava era justamente a de rótulo: no relatório de 02/08 saiu
ARM 🟢 caindo 0,8% com RSI 32, e SKHY 🟢 caindo 3,5% com IV 137%.

A rubrica no prompt define quatro gates que proíbem 🟢. Prompt sozinho é
pedido, não garantia — aqui os mesmos gates viram checagem determinística
sobre o texto gerado, com um retry de correção (mesma mecânica do
lint_veredito, ver agent.py::run_veredito).

Duas fases, espelhando o veredito:

1. `collect_tool_result(snap, nome, entrada, resultado)` roda DENTRO do loop,
   a cada tool_result. Monta o snapshot a partir do que o modelo realmente
   recebeu, sem refazer nenhuma chamada de rede -- refazer custaria tempo do
   orçamento da run e ainda poderia divergir do que o modelo viu.
2. `lint_report(texto, snap)` roda DEPOIS da geração e devolve ERROs por
   violação de gate.

Sem dependências externas (stdlib only). Reusa Issue/ValidationReport do
veredito_validator pra não duplicar o modelo de relatório de erro.
"""

from __future__ import annotations

import json as _json
import re
from typing import Any

from .veredito_validator import ValidationReport, _parse_date

# Fator de anualização da volatilidade diária: sqrt(252 pregões) ≈ 15.87.
# atr_pct é uma média de range diário, não desvio-padrão de retorno, então isto
# é aproximação de ordem de grandeza -- suficiente pra separar "IV de evento"
# de "IV normal do ativo", que é a única pergunta que o gate faz.
ANNUALIZE = 15.87
IV_EVENT_MULTIPLE = 2.0
EARNINGS_GATE_DAYS = 5

VERDE = "🟢"
LABELS = ("🟢", "🟡", "🔴")


# ------------------------------------------------ fase 1: coleta no loop ---


def new_snapshot() -> dict[str, Any]:
    return {"quotes": {}, "technicals": {}, "options": {}, "earnings": {}}


def _as_dict(resultado: str | dict) -> Any:
    if isinstance(resultado, (dict, list)):
        return resultado
    try:
        return _json.loads(resultado)
    except Exception:
        return None


def collect_tool_result(
    snap: dict[str, Any], nome: str, entrada: dict, resultado: str | dict
) -> None:
    """Acumula no snapshot o que interessa aos gates. Silencioso em erro:
    ferramenta que falhou simplesmente não contribui, e o gate correspondente
    não roda pra aquele ticker (não inventa violação a partir de dado ausente).
    """
    dados = _as_dict(resultado)
    if dados is None:
        return

    if nome == "get_stock_data" and isinstance(dados, dict):
        ticker = dados.get("ticker")
        if ticker and "error" not in dados:
            snap["quotes"][ticker] = {
                "change_pct": dados.get("change_pct"),
                "as_of": dados.get("as_of"),
            }

    elif nome == "get_technical_indicators" and isinstance(dados, dict):
        ticker = dados.get("ticker")
        if ticker and "error" not in dados:
            snap["technicals"][ticker] = {
                "rsi_date": dados.get("rsi_date"),
                "atr_pct": dados.get("atr_pct"),
            }

    elif nome == "get_options_data" and isinstance(dados, dict):
        ticker = dados.get("ticker")
        if ticker and "error" not in dados:
            snap["options"][ticker] = {"atm_iv_pct": dados.get("atm_iv_pct")}

    elif nome == "get_earnings_calendar" and isinstance(dados, list):
        for item in dados:
            if isinstance(item, dict) and item.get("ticker") and "error" not in item:
                snap["earnings"][item["ticker"]] = item.get("days_until_earnings")


# ------------------------------------------- fase 2: lint do texto gerado ---


def _secao_do_ticker(texto: str, ticker: str) -> str | None:
    """Trecho do relatório que fala do ticker: do cabeçalho Markdown que o
    menciona até o próximo cabeçalho de mesmo nível ou maior.

    Casa o ticker só como palavra inteira -- sem isso "MU" casaria dentro de
    "MULTI"/"NVDA acumulou", e o gate seria avaliado contra a seção errada.
    """
    linhas = texto.split("\n")
    padrao = re.compile(rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])")
    inicio = None
    nivel = 0
    for i, linha in enumerate(linhas):
        if linha.lstrip().startswith("#") and padrao.search(linha):
            inicio = i
            nivel = len(linha.lstrip()) - len(linha.lstrip().lstrip("#"))
            break
    if inicio is None:
        return None

    for j in range(inicio + 1, len(linhas)):
        linha = linhas[j].lstrip()
        if linha.startswith("#"):
            nivel_j = len(linha) - len(linha.lstrip("#"))
            if nivel_j <= nivel:
                return "\n".join(linhas[inicio:j])
    return "\n".join(linhas[inicio:])


def _rotulo_da_secao(secao: str) -> str | None:
    for ch in secao:
        if ch in LABELS:
            return ch
    return None


def _gates_violados(ticker: str, snap: dict[str, Any]) -> list[str]:
    """Gates ativos para o ticker — cada um é uma razão pela qual 🟢 é proibido."""
    violados: list[str] = []

    quote = snap.get("quotes", {}).get(ticker) or {}
    tech = snap.get("technicals", {}).get(ticker) or {}
    opts = snap.get("options", {}).get(ticker) or {}
    dias = snap.get("earnings", {}).get(ticker)

    change = quote.get("change_pct")
    if isinstance(change, (int, float)) and change < 0:
        violados.append(f"variação do dia é {change:+.2f}% (negativa)")

    if isinstance(dias, (int, float)) and 0 <= dias <= EARNINGS_GATE_DAYS:
        violados.append(f"earnings em {int(dias)} dias")

    iv = opts.get("atm_iv_pct")
    atr = tech.get("atr_pct")
    if isinstance(iv, (int, float)) and isinstance(atr, (int, float)) and atr > 0:
        limite = IV_EVENT_MULTIPLE * atr * ANNUALIZE
        if iv >= limite:
            violados.append(
                f"IV ATM {iv:.1f}% ≥ {limite:.1f}% "
                f"(2x a vol anualizada do próprio ativo, atr_pct {atr:.2f}%)"
            )

    rsi_date = tech.get("rsi_date")
    as_of = quote.get("as_of")
    if rsi_date and as_of:
        try:
            if _parse_date(rsi_date) < _parse_date(as_of):
                violados.append(
                    f"bloco técnico é de {rsi_date}, anterior ao pregão da cotação ({as_of})"
                )
        except Exception:
            pass

    return violados


def lint_report(texto: str, snap: dict[str, Any]) -> ValidationReport:
    """Confere que nenhum ativo rotulado 🟢 tem gate ativo.

    Só avalia ticker que aparece no snapshot E tem seção com rótulo no texto:
    ausência de dado nunca vira violação, e o Grupo B (sem IV/técnico) não é
    checado porque a rubrica manda não rotulá-lo.
    """
    rep = ValidationReport()
    tickers = set(snap.get("quotes", {})) | set(snap.get("technicals", {}))

    for ticker in sorted(tickers):
        secao = _secao_do_ticker(texto, ticker)
        if not secao:
            continue
        rotulo = _rotulo_da_secao(secao)
        if rotulo != VERDE:
            continue

        violados = _gates_violados(ticker, snap)
        if violados:
            esperado = "🔴" if len(violados) > 1 else "🟡"
            rep.add(
                "ERROR",
                "GATE_ROTULO",
                f"rotulado {VERDE} com {len(violados)} gate(s) ativo(s): "
                f"{'; '.join(violados)}. Pela rubrica o rótulo deveria ser {esperado}.",
                ticker=ticker,
            )

    return rep


def correction_prompt(rep: ValidationReport) -> str:
    """Mensagem de correção pro retry — lista o que reescrever, sem reescrever
    pelo modelo (o texto continua sendo dele; só o rótulo está errado)."""
    linhas = [
        "O relatório violou a rubrica de rótulo. Corrija APENAS os rótulos "
        "listados abaixo e a linha de justificativa de cada um; não altere o "
        "resto da análise nem recolete dados:",
    ]
    linhas += [f"- {i}" for i in rep.issues]
    linhas.append(
        "Reescreva o relatório completo já corrigido, no mesmo formato."
    )
    return "\n".join(linhas)
