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

# Limiar do gate de IV, como multiplicador direto de atr_pct.
#
# A conta por trás: anualizar volatilidade diária multiplica por sqrt(252) ≈
# 15.87, e o gate exige o DOBRO disso -- 2 × 15.87 = 31.74. Arredondado para 32
# de propósito: este mesmo número está no prompt, e o modelo precisa conseguir
# aplicá-lo de cabeça sem decompor a conta. A primeira versão pedia "compare
# atm_iv_pct com atr_pct × 16" e explicava o 2× em prosa; o relatório de 02/08
# mostrou o modelo comparando contra 16× em três ativos (NVDA, AVGO, ARM), ou
# seja, METADE do limiar. Um número fechado elimina a decomposição.
#
# O arredondamento custa 0,8% no limiar e compra uma instrução sem ambiguidade.
# atr_pct é média de range diário, não desvio-padrão de retorno, então tudo isso
# já é aproximação de ordem de grandeza -- suficiente para a única pergunta que o
# gate faz: esta IV é de evento ou é a IV normal deste ativo?
IV_EVENT_MULTIPLE_ATR = 32.0

# Janela de earnings em DOIS níveis.
#
# ≤5 dias é crítico: o evento domina o setup do dia. 6-14 dias é ativo: ainda
# no radar, mas a IV já cobra o prêmio e o dia não é sobre o evento.
#
# O tier de 6-14 existia só na cabeça do modelo e virava citação de gate
# inexistente -- em 02/08 o relatório escreveu "Gate: earnings em 9 dias
# (≤14d) → teto 🟡", inventando um limiar que a rubrica não tinha. Formalizar
# resolve a ambiguidade em vez de proibir a leitura, que era razoável.
EARNINGS_CRITICO_DIAS = 5
EARNINGS_ATIVO_DIAS = 14

# Extensão vs MM200: distância historicamente insustentável, risco de
# mean-reversion. Só conta com bloco técnico FRESCO -- em 02/08 o MRVL trazia
# "38,9% acima da MM200" de um pico anterior, e o próprio relatório descartou
# o número com razão. Gate sobre dado defasado é pior que gate nenhum.
EXTENSAO_SMA200_PCT = 25.0

# Manchete com risco binário: processo/patente/antitruste ou rebaixamento não
# confirmado. Caso real: SMCI + investigação ITC/Netlist, que apareceu em
# 31/07 e 02/08 e nunca teve peso formal no rótulo.
_HEADLINE_RISCO = re.compile(
    r"\b(itc|antitrust|antitruste|patente|patent|"
    r"investiga[çc][ãa]o|investigation|processo judicial|lawsuit|"
    r"downgrade|rebaixa|sell rating)\b",
    re.IGNORECASE,
)

# Short alto: gate de "não perseguir", não de cor. Assimetria de squeeze corta
# para os dois lados -- pode subir no squeeze OU desabar no evento -- então
# rebaixar o rótulo por ele seria assumir uma direção que o dado não dá.
SHORT_ALTO_PCT = 15.0

# Severidades. "info" aparece no texto do achado mas não entra na conta da cor.
CRITICO, ATIVO, INFO = "critico", "ativo", "info"

VERDE = "🟢"
AMARELO = "🟡"
VERMELHO = "🔴"
LABELS = (VERDE, AMARELO, VERMELHO)

# ------------------------------------------------ fase 1: coleta no loop ---


def new_snapshot() -> dict[str, Any]:
    return {"quotes": {}, "technicals": {}, "options": {}, "earnings": {},
            "short": {}, "headlines": {}}


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
                "pct_above_sma200": dados.get("pct_above_sma200"),
            }

    elif nome == "get_options_data" and isinstance(dados, dict):
        ticker = dados.get("ticker")
        if ticker and "error" not in dados:
            snap["options"][ticker] = {
                "atm_iv_pct": dados.get("atm_iv_pct"),
                "as_of": dados.get("as_of"),
            }

    elif nome == "get_short_interest" and isinstance(dados, dict):
        ticker = dados.get("ticker")
        if ticker and "error" not in dados:
            snap["short"][ticker] = {
                "short_pct_of_float": dados.get("short_pct_of_float"),
                "squeeze_risk": dados.get("squeeze_risk"),
            }

    elif nome == "get_news" and isinstance(dados, dict):
        # get_news devolve {ticker: [item, ...]}; só o título interessa aqui.
        for ticker, itens in dados.items():
            if not isinstance(itens, list):
                continue
            titulos = [i.get("title", "") for i in itens if isinstance(i, dict)]
            if titulos:
                snap["headlines"].setdefault(ticker, []).extend(titulos)

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


def _gates_violados(ticker: str, snap: dict[str, Any]) -> list[tuple[str, str]]:
    """Gates ativos do ticker como (severidade, descrição).

    Severidade existe porque contar gates iguais não funciona quando eles
    passam de meia dúzia: dado defasado e "earnings em 3 dias" não pesam o
    mesmo que "manchete de processo". Com contagem simples, qualquer ativo em
    correção acumula gates e vira 🔴 todo dia -- o rótulo para de discriminar,
    que é o mesmo colapso do bug original, só que pelo lado vermelho.

    Fora daqui de propósito:
      • MACRO (10y ≥ 4,5%): foi verdadeiro nos 7 relatórios revisados. Gate que
        nunca varia entre ativos no mesmo dia não carrega informação -- só
        desloca o piso de todo mundo igualmente e ocupa vaga na conta do 🔴.
        Segue como contexto na seção macro, não como gate.
      • TÉCNICO FRACO (MACD bearish + abaixo da SMA50): mesmo problema num
        mercado em correção prolongada, e já é o que o 🟡 de julgamento cobre.
    """
    gates: list[tuple[str, str]] = []

    quote = snap.get("quotes", {}).get(ticker) or {}
    tec = snap.get("technicals", {}).get(ticker) or {}
    opts = snap.get("options", {}).get(ticker) or {}
    short = snap.get("short", {}).get(ticker) or {}
    dias = snap.get("earnings", {}).get(ticker)

    # --- crítico: bloco técnico defasado -------------------------------------
    rsi_date = tec.get("rsi_date")
    as_of = quote.get("as_of")
    tecnico_fresco = True
    if rsi_date and as_of:
        try:
            if _parse_date(rsi_date) < _parse_date(as_of):
                tecnico_fresco = False
                gates.append((CRITICO,
                    f"bloco técnico é de {rsi_date}, anterior ao pregão da "
                    f"cotação ({as_of})"))
        except Exception:
            pass

    # --- crítico / ativo: janela de earnings ---------------------------------
    if isinstance(dias, (int, float)):
        if 0 <= dias <= EARNINGS_CRITICO_DIAS:
            gates.append((CRITICO, f"earnings em {int(dias)} dias"))
        elif dias <= EARNINGS_ATIVO_DIAS:
            gates.append((ATIVO, f"earnings em {int(dias)} dias (≤{EARNINGS_ATIVO_DIAS})"))

    # --- ativo: variação do dia negativa -------------------------------------
    change = quote.get("change_pct")
    if isinstance(change, (int, float)) and change < 0:
        gates.append((ATIVO, f"variação do dia é {change:+.2f}% (negativa)"))

    # --- ativo: IV de evento --------------------------------------------------
    #
    # Suprimido na semana de earnings: nesse período a IV está alta POR CAUSA
    # do evento, e o gate de earnings (crítico) já captura o mesmo fato.
    # Contar os dois seria double-count -- inflaria o 🔴 a partir de uma
    # informação só, que é o tipo de acúmulo que a severidade veio evitar.
    earnings_domina = isinstance(dias, (int, float)) and 0 <= dias <= EARNINGS_CRITICO_DIAS
    iv = opts.get("atm_iv_pct")
    atr = tec.get("atr_pct")
    if not earnings_domina and isinstance(iv, (int, float)) and isinstance(atr, (int, float)) and atr > 0:
        limite = IV_EVENT_MULTIPLE_ATR * atr
        if iv >= limite:
            gates.append((ATIVO,
                f"IV ATM {iv:.1f}% ≥ {limite:.1f}% (32 × atr_pct de {atr:.2f}%)"))

    # --- ativo: extensão vs MM200 (só com técnico fresco) --------------------
    ext = tec.get("pct_above_sma200")
    if tecnico_fresco and isinstance(ext, (int, float)) and ext >= EXTENSAO_SMA200_PCT:
        gates.append((ATIVO,
            f"{ext:+.1f}% acima da MM200 (≥{EXTENSAO_SMA200_PCT:.0f}%), "
            f"extensão historicamente insustentável"))

    # --- ativo: manchete de risco binário ------------------------------------
    for titulo in snap.get("headlines", {}).get(ticker, []) or []:
        m = _HEADLINE_RISCO.search(titulo or "")
        if m:
            gates.append((ATIVO,
                f"manchete de risco binário ({m.group(1).lower()}): "
                f"\"{(titulo or '')[:80]}\""))
            break

    # --- info: short alto (não conta pra cor) --------------------------------
    short_pct = short.get("short_pct_of_float")
    if isinstance(short_pct, (int, float)) and short_pct >= SHORT_ALTO_PCT:
        gates.append((INFO,
            f"short {short_pct:.1f}% do float (≥{SHORT_ALTO_PCT:.0f}%) — "
            f"assimetria de squeeze corta pros dois lados: não perseguir alta, "
            f"mas não é motivo de rebaixar cor"))

    return gates


def _rotulo_esperado(gates: list[tuple[str, str]]) -> str:
    """Cor máxima permitida pelos gates.

    🔴 exige deterioração combinada, não acúmulo qualquer:
      • dois críticos, OU
      • um crítico acompanhado de pelo menos um ativo, OU
      • três ativos.

    Isso reproduz os casos reais melhor que a contagem simples anterior: HCC
    (earnings 3d + queda) continua 🔴; NVDA só com técnico defasado fica 🟡, e
    não 🔴; SMCI com earnings 9d + manchete ITC fica 🟡, que a contagem antiga
    teria transformado em 🔴 por serem "dois gates".
    """
    criticos = sum(1 for sev, _ in gates if sev == CRITICO)
    ativos = sum(1 for sev, _ in gates if sev == ATIVO)
    if criticos >= 2 or (criticos >= 1 and ativos >= 1) or ativos >= 3:
        return VERMELHO
    if criticos or ativos:
        return AMARELO
    return VERDE


def lint_report(texto: str, snap: dict[str, Any]) -> ValidationReport:
    """Confere que o rótulo de cada ativo bate com os gates ativos.

    Checa nos DOIS sentidos: 🟢 exige zero gates que contem pra cor, e 🔴 exige
    a combinação de _rotulo_esperado. 🟡 continua livre -- ali cabe julgamento
    que nenhum gate cobre (RVOL baixo, manchete ambígua), e engessar isso
    tiraria a saída legítima pro receio, que foi o que levou o modelo a
    inventar gate em 02/08.

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
        if rotulo is None:
            continue

        gates = _gates_violados(ticker, snap)
        contam = [(sev, d) for sev, d in gates if sev != INFO]
        esperado = _rotulo_esperado(gates)
        detalhe = "; ".join(f"[{sev}] {d}" for sev, d in contam) or "nenhum"

        if rotulo == VERDE and contam:
            rep.add(
                "ERROR",
                "GATE_ROTULO",
                f"rotulado {VERDE} com {len(contam)} gate(s) ativo(s): "
                f"{detalhe}. Pela rubrica o rótulo deveria ser {esperado}.",
                ticker=ticker,
            )

        elif rotulo == VERMELHO and esperado != VERMELHO:
            # O inverso do bug original: em vez de otimismo indevido, receio
            # indevido. Visto em produção (02/08) com ARM, que levou 🔴 alegando
            # dois gates enquanto o próprio texto dizia que a IV estava ABAIXO
            # do limiar. Erra pro lado seguro, mas se "gate ativo" puder
            # significar "achei que sim", o rótulo perde significado de novo --
            # só que puxando pro vermelho.
            rep.add(
                "ERROR",
                "ROTULO_INFLADO",
                f"rotulado {VERMELHO}, mas os gates ativos ({detalhe}) só "
                f"sustentam {esperado}. Use {esperado} e escreva o receio no "
                f"texto, em vez de contar um gate não atendido.",
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
        "Reescreva o relatório completo já corrigido, no mesmo formato. "
        "Comece direto pelo relatório, sem nenhuma frase de introdução "
        "(nada de \"Compreendido\", \"Segue o relatório corrigido\" etc.) -- "
        "o texto vai inteiro pro usuário, do jeito que você escrever."
    )
    return "\n".join(linhas)
