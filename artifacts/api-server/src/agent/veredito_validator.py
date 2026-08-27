"""
veredito_validator.py — Validação de snapshot + lint do texto do Veredito do Dia.

Duas fases:
1. validate_snapshot(snapshot) -> roda ANTES de montar o prompt do veredito.
   Garante que os números que entram no prompt são coerentes entre si
   (percentual recalculado, sinal, frescor, RSI da mesma data do quote).
2. lint_veredito(texto, snapshot) -> roda DEPOIS da geração pelo LLM.
   Pega alucinações típicas: dia da semana errado, data de earnings
   divergente do painel, afirmação "pós-earnings" quando o earnings
   ainda não ocorreu, percentuais citados que não batem com o snapshot.

Sem dependências externas (stdlib only). Integração em run_veredito()
(agent.py).

Formato esperado do snapshot (dict):
{
  "as_of": "2026-07-31",  # dia de pregão do snapshot -- SEMPRE o último
                          # pregão fechado, nunca "hoje" cru: num fim de
                          # semana/feriado isso derrubaria RSI_STALE pra
                          # TODO ticker (tolerância de frescor é zero, ver
                          # STALE_TECHNICAL_DAYS abaixo). Quem monta o
                          # snapshot (agent.py::_build_veredito_snapshot)
                          # deriva isso da data real do último candle
                          # baixado, não do relógio do processo.
  "generated_at": "2026-08-01T16:15:00-03:00",
  "quotes": {
    "SMCI": {"price": 28.40, "previous_close": 27.73, "open": 28.65,
             "high": 29.30, "low": 27.21, "change_percent": 2.4162,
             "volume": 34956061, "as_of": "2026-07-31"},
    ...
  },
  "technicals": {
    "SMCI": {"rsi": 48.91, "rsi_date": "2026-07-31", ...},
    ...
  },
  "earnings": {"SMCI": "2026-08-10", "NVDA": "2026-08-25", ...}
}
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from itertools import combinations
from typing import Any

from .validador_nucleo import frase_com_moeda_errada

# radar_ia_2026 é stdlib-only (dados embutidos + funções puras), então não
# quebra o contrato "sem dependências externas" deste módulo. Import dos
# DOIS jeitos porque este arquivo também roda standalone (__main__ no fim).
try:
    from radar_ia_2026 import CORR_ALTA, correlacao
except ImportError:
    from agent.radar_ia_2026 import CORR_ALTA, correlacao

# ---------------------------------------------------------------- config ---

PCT_TOLERANCE_PP = 0.10  # tolerância em pontos percentuais p/ recomputo
STALE_TECHNICAL_DAYS = 0  # RSI deve ser do MESMO dia do quote (0 dias de gap)
FADE_FROM_HIGH_PCT = 5.0  # fechou X% ou mais abaixo do high do dia -> alerta
GAP_UP_PCT = 2.0  # abertura X% acima do fech. anterior conta como gap
MENTION_PCT_TOLERANCE_PP = 0.30  # tolerância p/ percentuais citados no texto
FLAT_CLAIM_TOLERANCE_PP = 1.5  # |variação real| acima disso invalida um claim de "flat"

# Perfil que NÃO pode ser chamado de "distribuição": RSI perto de sobrevenda e
# preço bem abaixo da SMA50 é fundo/capitulação, não topo.
#
# O prompt do veredito já traz essa instrução em prosa desde um incidente
# anterior -- e ela não segurou: em 01/08 o veredito abriu com "padrão de
# distribuição confirmado" citando ARM (RSI 31.55, -26.17% vs SMA50) e MRVL
# (RSI 38.77, -21.66%), exatamente o perfil que a instrução descreve como o
# oposto. E não é cosmético: a conclusão foi "vender ARM amanhã na abertura".
#
# Os cortes cobrem os dois casos reais com folga, sem pegar um ticker que
# esteja só de lado (RSI 45, -3% da SMA50 não vira erro).
DISTRIB_RSI_MAX = 40.0
DISTRIB_PCT_SMA50_MAX = -10.0

WEEKDAYS_PT = [
    "segunda-feira", "terca-feira", "quarta-feira",
    "quinta-feira", "sexta-feira", "sabado", "domingo",
]

MONTHS_PT = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}

# ---------------------------------------------------------------- modelo ---


@dataclass
class Issue:
    severity: str  # "ERROR" | "WARN" | "INFO"
    ticker: str | None
    code: str
    message: str

    def __str__(self) -> str:
        t = f"[{self.ticker}] " if self.ticker else ""
        return f"{self.severity:5s} {t}{self.code}: {self.message}"


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)
    signals: list[Issue] = field(default_factory=list)  # sinais detectados (fade etc.)

    def add(self, severity: str, code: str, message: str,
             ticker: str | None = None, signal: bool = False) -> None:
        issue = Issue(severity, ticker, code, message)
        (self.signals if signal else self.issues).append(issue)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "ERROR" for i in self.issues)

    def summary(self) -> str:
        lines = [str(i) for i in self.issues] + [str(s) for s in self.signals]
        return "\n".join(lines) if lines else "OK: nenhum problema encontrado."

    def bloco_para_a_tela(self) -> str:
        """Os achados como MARKDOWN, para viajarem junto do veredito.

        Ate 26/08/2026 os achados do Veredito iam para o stderr e para o
        retry, e paravam ai. Um AVISO (que nao dispara retry) nunca chegava a
        lugar nenhum, e um ERRO que sobrevivesse ao retry era publicado sem
        marca. A tela de Analise Rapida ja' mostrava os apontamentos dela; o
        Veredito nao mostrava nenhum -- e foi por isso que toda geracao que o
        operador leu neste dia apareceu com "a caixa vazia" enquanto tinha
        erro dentro.

        O relatorio E' o artefato aqui: e' ele que vai pro histórico, pro
        e-mail e pro .md exportado. Entao o apontamento vai no texto, e nao
        num campo a parte -- assim ele viaja com o que descreve.
        """
        if not self.issues:
            return ""
        erros = [i for i in self.issues if i.severity == "ERROR"]
        avisos = [i for i in self.issues if i.severity != "ERROR"]
        linhas = ["", "---", "",
                  f"> ⚠️ **O validador apontou {len(self.issues)} problema(s) "
                  f"neste veredito** — o texto acima fica assim mesmo, leia "
                  f"com estes pontos em mente.", ">"]
        for i in erros + avisos:
            alvo = f"({i.ticker}) " if i.ticker else ""
            rotulo = "ERRO" if i.severity == "ERROR" else "AVISO"
            linhas.append(f"> - **[{rotulo}]** `{i.code}` {alvo}{i.message}")
        return "\n".join(linhas)

    def prompt_block(self) -> str:
        """Bloco para injetar no prompt do veredito com fatos verificados,
        evitando que o LLM recalcule (e erre) percentuais."""
        if not self.signals:
            return ""
        out = ["SINAIS DETECTADOS PELO VALIDADOR (use estes fatos, nao recalcule):"]
        out += [f"- {s.message}" for s in self.signals]
        return "\n".join(out)


# ------------------------------------------------------------- utilidades ---


def _norm(text: str) -> str:
    """minusculas + sem acentos, p/ casar 'sexta-feira' com 'Sexta-Feira'."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


def _pct_change(price: float, prev: float) -> float:
    return (price - prev) / prev * 100.0


# ------------------------------------------- fase 1: valida o snapshot ---


def validate_snapshot(snapshot: dict[str, Any]) -> ValidationReport:
    rep = ValidationReport()
    as_of = _parse_date(snapshot.get("as_of"))
    quotes: dict = snapshot.get("quotes", {})
    technicals: dict = snapshot.get("technicals", {})

    for tk, q in quotes.items():
        price = q.get("price")
        prev = q.get("previous_close")
        claimed = q.get("change_percent")

        # 1) recomputa o percentual e confere sinal
        if price is not None and prev:
            real = _pct_change(price, prev)
            if claimed is not None:
                diff = abs(real - float(claimed))
                if (real > 0) != (float(claimed) > 0) and abs(real) > 0.05:
                    rep.add("ERROR", "PCT_SIGN_FLIP",
                            f"change_percent informado {claimed:+.2f}% tem sinal "
                            f"oposto ao recalculado {real:+.2f}% "
                            f"(price={price}, prev_close={prev}). Corrigir fonte.",
                            ticker=tk)
                elif diff > PCT_TOLERANCE_PP:
                    rep.add("ERROR", "PCT_MISMATCH",
                            f"change_percent informado {claimed:+.2f}% difere do "
                            f"recalculado {real:+.2f}% em {diff:.2f}pp "
                            f"(provavel snapshot de datas misturadas).",
                            ticker=tk)
            # normaliza: o valor que vai pro prompt e o recalculado
            q["change_percent_verified"] = round(real, 2)

        # 2) frescor do quote vs as_of global
        q_date = q.get("as_of")
        if q_date and _parse_date(q_date) != as_of:
            rep.add("ERROR", "QUOTE_STALE",
                    f"quote datado de {q_date}, mas snapshot as_of={as_of}. "
                    f"Nao misturar dias de pregao no mesmo veredito.", ticker=tk)

        # 3) deteccao de reversao intradiaria (fade do topo)
        high, op = q.get("high"), q.get("open")
        if price is not None and high:
            fade = (high - price) / high * 100.0
            gapped_up = bool(op and prev and _pct_change(op, prev) >= GAP_UP_PCT)
            if fade >= FADE_FROM_HIGH_PCT:
                extra = " apos gap de alta na abertura (padrao de distribuicao)" if gapped_up else ""
                rep.add("WARN", "INTRADAY_FADE",
                        f"{tk}: fechou {fade:.1f}% abaixo do high do dia "
                        f"(high={high}, close={price}){extra}.",
                        ticker=tk, signal=True)

    # 5) sentimento FIXADO vira fato no prompt.
    #
    # `signal=True` de proposito: prompt_block() renderiza os sinais sob
    # "use estes fatos, nao recalcule", que e' exatamente o contrato aqui --
    # o modelo cita ESTE numero em vez de chamar a ferramenta por conta e
    # trazer outra leitura do mesmo indice.
    sentimento = snapshot.get("sentimento") or {}
    score = sentimento.get("score")
    if isinstance(score, (int, float)):
        rotulo = sentimento.get("rating_pt") or sentimento.get("rating_en") or ""
        lido = sentimento.get("lido_em") or "?"
        rep.add("INFO", "SENTIMENTO_FIXADO",
                f"Fear & Greed: {score} ({rotulo}), lido em {lido}. "
                f"Cite ESTE valor -- o indice anda intradia e uma segunda "
                f"leitura daria outro numero.", signal=True)

    # 4) tecnico da mesma data do quote
    for tk, t in technicals.items():
        rsi_date = t.get("rsi_date")
        if rsi_date:
            gap = (as_of - _parse_date(rsi_date)).days
            if gap > STALE_TECHNICAL_DAYS:
                rep.add("ERROR", "RSI_STALE",
                        f"RSI datado de {rsi_date} ({gap} dia(s) atras do quote "
                        f"{as_of}). Recarregar indicador antes de gerar veredito.",
                        ticker=tk)

    # 6) GATE de balanço iminente -- fato, e regra junto do fato.
    #
    # Auditoria de 26/08/2026: o texto do veredito escreveu, para NVDA,
    # "Earnings estão longe (próximo em nov/dez)" com o balanço saindo NAQUELE
    # dia, depois do fechamento. A tela de Previsão de Vol avisava; a Análise
    # Rápida ganhou o gate na #413; o veredito era a única tela sem ele.
    #
    # O fato vai no prompt com a regra colada, porque regra longe do fato é o
    # que o modelo ignora: e' assim que "earnings hoje" virou "earnings longe".
    for tk, data in (snapshot.get("earnings") or {}).items():
        try:
            dias = (_parse_date(data) - as_of).days
        except Exception:
            continue
        if 0 <= dias <= EARNINGS_PROXIMO_DIAS:
            rep.add("INFO", "EVENTO_IMINENTE",
                    f"{tk}: balanço em {dias} dia(s) ({data}) -- a sessão de "
                    f"reação ainda NÃO ocorreu. Não converta técnica em "
                    f"COMPRAR/AUMENTAR; se a decisão depender do resultado, "
                    f"é MANTER com EARNINGS_PROXIMO declarado, nunca 'earnings "
                    f"longe'.", ticker=tk, signal=True)

    # 7) o lado do nível do plano é CONTA, não leitura.
    #
    # Auditoria de 26/08/2026: BABA a 119,83 com "vender se quebrar suporte
    # $126" saiu no texto como "ainda acima, mas em risco" -- 119,83 é MENOR
    # que 126, o gatilho do plano já tinha disparado, e o bloco saiu MANTER.
    # O modelo recebia os dois números e fazia a comparação errado; agora
    # recebe a comparação feita, com a distância em % e em ATR (o mesmo -4,9%
    # é ruído num papel de ATR 11% e evento num de ATR 2%).
    for tk, itens in (snapshot.get("plano_de_saida") or {}).items():
        if not isinstance(itens, list):
            continue
        preco = (quotes.get(tk) or {}).get("price")
        atr_pct = (technicals.get(tk) or {}).get("atr_pct")
        if not preco:
            continue
        for it in itens:
            nivel = it.get("nivel") if isinstance(it, dict) else None
            if not nivel:
                continue
            dist_pct = (preco - nivel) / nivel * 100
            em_atr = (f", {abs(dist_pct) / atr_pct:.1f} ATR"
                      if atr_pct else "")
            rep.add("INFO", "NIVEL_DO_PLANO",
                    f"{tk}: preço {preco:.2f} está "
                    f"{'ACIMA' if dist_pct > 0 else 'ABAIXO'} do nível "
                    f"${nivel:.2f} do plano ({dist_pct:+.2f}%{em_atr}) -- "
                    f"item: \"{str(it.get('acao'))[:60]}\". Use ESTE lado, "
                    f"não recalcule.", ticker=tk, signal=True)

    # 8) reação a earnings como fato nomeado.
    #
    # Os nomes importam mais que os números: `runup_ate_agora_pct` é o
    # acumulado rumo ao PRÓXIMO balanço -- não é reação, e não é o run-up de
    # chegada de evento passado. Foi a confusão exata de SMCI/ARM/NVDA.
    for tk, r in (snapshot.get("reacao_earnings") or {}).items():
        if not isinstance(r, dict):
            continue
        partes = [f"{tk}: balanço em {r.get('dias_ate_earnings')} dia(s)."]
        if r.get("runup_ate_agora_pct") is not None:
            partes.append(
                f"Run-up ATÉ AGORA rumo ao balanço: "
                f"{r['runup_ate_agora_pct']:+.2f}%"
                + (f" ({r['estado']})" if r.get("estado") else "") + ".")
        if r.get("reacao_abs_media_pct") is not None:
            partes.append(
                f"Histórico ({r.get('n_eventos')} eventos): |reação média| "
                f"{r['reacao_abs_media_pct']:.2f}%"
                + (f", média assinada {r['reacao_media_pct']:+.2f}%"
                   if r.get("reacao_media_pct") is not None else "")
                + (f", gap |médio| {r['gap_abs_medio_pct']:.2f}%"
                   if r.get("gap_abs_medio_pct") is not None else "") + ".")
        if r.get("threshold_pct") is not None:
            partes.append(f"Threshold de reação: ±{r['threshold_pct']:.2f}%.")
        partes.append("Estes são OS números de earnings deste veredito -- "
                      "variação do dia não é reação histórica.")
        rep.add("INFO", "REACAO_EARNINGS_FIXADA", " ".join(partes),
                ticker=tk, signal=True)

    # 9) direção do MACD -- o delta vale mais que o sinal.
    for tk, tec in technicals.items():
        hist = tec.get("macd_hist")
        direcao = tec.get("macd_direcao")
        if hist is None or not direcao:
            continue
        rep.add("INFO", "MACD_FIXADO",
                f"{tk}: MACD hist {hist:+.4f}, {direcao} vs 5 pregões atrás. "
                f"'Negativo melhorando' e 'negativo piorando' pedem leituras "
                f"opostas -- cite a direção junto do sinal.",
                ticker=tk, signal=True)

    # 10) tendência com FORÇA e DIREÇÃO, não só posição.
    #
    # P2 da auditoria de 26/08/2026: o texto chamava de "tendência" qualquer
    # posição relativa a média. Posição + inclinação + estrutura + ADX é o
    # quadro inteiro: "acima da MM50" com a média caindo, estrutura LH/LL e
    # ADX 13 é lateral vestido de alta.
    for tk, tec in technicals.items():
        partes = []
        if tec.get("sma50_inclinacao"):
            partes.append(f"MM50 {tec['sma50_inclinacao']}")
        if tec.get("sma20_inclinacao"):
            partes.append(f"MM20 {tec['sma20_inclinacao']}")
        if tec.get("estrutura"):
            partes.append(f"estrutura {tec['estrutura']}")
        adx = tec.get("adx_14")
        if adx is not None:
            forca = ("muito fraca" if adx < 15 else "fraca" if adx < 20
                     else "surgindo" if adx < 25 else "relevante" if adx < 40
                     else "muito forte")
            di = ""
            if tec.get("plus_di") is not None and tec.get("minus_di") is not None:
                lado = "+DI>-DI" if tec["plus_di"] > tec["minus_di"] else "-DI>+DI"
                di = f", {lado}"
            partes.append(
                f"ADX {adx:.0f} ({forca}"
                + (f", {tec['adx_direcao']}" if tec.get("adx_direcao") else "")
                + f"{di})")
        if not partes:
            continue
        rep.add("INFO", "ESTRUTURA_FIXADA",
                f"{tk}: " + "; ".join(partes) + ". Tendência é posição + "
                f"inclinação + estrutura + força -- não chame de tendência o "
                f"que o ADX diz que é lateral.", ticker=tk, signal=True)

    # 11) pares da carteira que são quase o mesmo trade.
    #
    # MANTER dois papéis a 0,8 de correlação é dobrar a aposta sem dizer. O
    # corte (0,70) é o mesmo CORR_MESMO_TRADE do reacao_earnings_validator.
    for par in snapshot.get("correlacoes_carteira") or []:
        if not isinstance(par, dict):
            continue
        rep.add("INFO", "CORRELACAO_ALTA",
                f"{par.get('a')} e {par.get('b')} têm correlação medida de "
                f"{par.get('corr')} -- na prática o mesmo trade. Decisões "
                f"iguais nos dois somam o MESMO risco, não diversificam; se "
                f"citar os dois, diga isso.", signal=True)

    return rep


# --------------------------------------- fase 2: lint do texto gerado ---

_DATE_PT = re.compile(r"(\d{1,2})\s*/?\s*(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)",
                       re.IGNORECASE)
# Data por EXTENSO ou NUMERICA. O veredito escreve os dois jeitos, as vezes na
# mesma frase -- e ate 26/08/2026 so' a forma por extenso era enxergada.
_DIA_MES = r"(\d{1,2})\s*/\s*(?:(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)|(\d{1,2}))"

# A data que o texto ATRIBUI a earnings, em qualquer das duas ordens. Casar a
# data solta do trecho nao serve: o paragrafo do INTC de 26/08/2026 trazia
# "monitoramento ate 22/out" (que por acaso E' a data do painel) e, tres
# oracoes depois, "proximos earnings em 24/11" (que nao e'). Com `.search()`
# a primeira casava, a checagem dava por conferido e a segunda nunca era
# olhada.
#
# `[^\n]` e nao `[^.\n]`: ponto e' separador decimal em "$507.66", e a
# janela morria dentro do numero -- mesma armadilha que matou as janelas por
# ticker. O que delimita aqui e' o tamanho, nao a pontuacao.
# `[^;\n]` e nao `[^\n]`: ponto-e-virgula e' quebra dura de oracao. Sem ela a
# janela pulava de "monitoramento ate 22/out;" para o "earnings" da oracao
# seguinte e pendurava naquela data uma afirmacao que o texto nao fez.
_EARNINGS_COM_DATA = re.compile(
    rf"(?:earnings|resultado|balan[cç]o)[^;\n]{{0,30}}?{_DIA_MES}"
    rf"|{_DIA_MES}[^;\n]{{0,20}}?(?:earnings|resultado|balan[cç]o)",
    re.IGNORECASE)


def _datas_atribuidas_a_earnings(seg: str):
    """(dia, mes) de cada data que o trecho pendura em earnings.

    Devolve TODAS, nao a primeira: a forma tipica do erro e' o trecho trazer
    uma data certa e uma errada, e conferir so' a primeira transforma o acerto
    num alibi para o erro."""
    achadas = []
    for m in _EARNINGS_COM_DATA.finditer(seg):
        # Os dois lados da alternancia: grupos 1-3 (predicado antes) ou 4-6.
        dia, mes_txt, mes_num = m.group(1), m.group(2), m.group(3)
        if dia is None:
            dia, mes_txt, mes_num = m.group(4), m.group(5), m.group(6)
        mes = MONTHS_PT[mes_txt[:3].lower()] if mes_txt else int(mes_num)
        dia = int(dia)
        if 1 <= dia <= 31 and 1 <= mes <= 12:
            achadas.append((dia, mes))
    return achadas


# "dados tecnicos limitados", "sem dados no painel", "informacoes
# indisponiveis". Precisa da NEGACAO junto com o SUJEITO ser o dado -- "o
# volume esta limitado" fala de liquidez, nao de disponibilidade.
_NEGA_DADO_DO_TICKER = re.compile(
    r"(?:dados?|informa[cç][oõ]es|indicadores?|m[ée]tricas?)"
    r"[^;\n]{0,30}?(?:limitad\w+|indispon[ií]ve\w+|ausent\w+|faltando|"
    r"insuficient\w+|n[aã]o\s+dispon[ií]ve\w+)"
    r"|(?:sem|falta\w*)\s+(?:dados?|informa[cç][oõ]es|indicadores?)"
    r"[^;\n]{0,25}?(?:no\s+painel|dispon[ií]ve\w+|para\s+este)",
    re.IGNORECASE)


def _o_que_tem(tec: dict) -> str:
    """Os campos que o snapshot REALMENTE trouxe, nomeados na mensagem: sem
    isso o apontamento vira "voce esta errado" sem dizer sobre o que."""
    nomes = {"rsi": "RSI", "pct_above_sma50": "distância à SMA50",
             "rsi_date": "data do RSI"}
    tem = [rotulo for campo, rotulo in nomes.items()
           if tec.get(campo) is not None and campo != "rsi_date"]
    return ", ".join(tem) if tem else "dados técnicos"


_DATE_WEEKDAY = re.compile(
    r"(\d{1,2})\s*/?\s*(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)\w*\s*"
    # [\s(]* em vez de \s*: a forma mais comum no texto gerado e' "dd/mes
    # (dia-da-semana)", com o dia entre parenteses -- so' pular espaco em
    # branco deixava o "(" sem casar e o bloco inteiro nunca disparava.
    r"([\s(]*(segunda|terca|terça|quarta|quinta|sexta|sabado|sábado|domingo)[^)])",
    re.IGNORECASE)
_TICKER_PCT = re.compile(r"\b([A-Z]{2,5})\b[^.\n]{0,80}?([+-]?\d{1,2}[.,]\d{1,2})\s*%")
# "flat"/"estável"/"lateral"/"sem variação" perto de um ticker -- claim
# qualitativo de variação ~0%, sem número pra _TICKER_PCT comparar.
# (?i:...) escopa case-insensitive só pro grupo da palavra-chave -- sem
# isso, re.IGNORECASE no regex inteiro faria [A-Z]{2,5} casar ticker em
# minúsculo também, e group(1) devolveria algo que nunca bate com as
# chaves (sempre maiúsculas) de `quotes`.
# "amanha (26/ago)" e "26/ago (hoje)" -- o prazo relativo COLADO a data.
# A adjacencia e' exigida de proposito: em "o balanco de 22/out, mas hoje o
# papel caiu" o "hoje" fala de outra coisa, e uma janela larga o pegaria.
_MES = r"jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez"
_PRAZO_DEPOIS = re.compile(
    rf"(\d{{1,2}})\s*/?\s*({_MES})\w*\s*[(,]\s*(hoje|amanha|amanhã)\b",
    re.IGNORECASE)
_PRAZO_ANTES = re.compile(
    rf"\b(hoje|amanha|amanhã)\s*\(\s*(\d{{1,2}})\s*/?\s*({_MES})",
    re.IGNORECASE)

# O texto afirmando de que lado de um nivel o preco esta, com os DOIS numeros
# escritos por ele mesmo. Nao precisa consultar o plano: a contradicao esta
# dentro da propria frase.
_NIVEL_COM_VALOR = re.compile(
    r"(suporte|resist[êe]ncia|stop|support|resistance)"
    r"(?:[^.\n]|\.(?=\d)){0,25}?\$\s*(\d+(?:[.,]\d+)?)",
    re.IGNORECASE)
# "ABOVE" e "BELOW" entram porque o modelo escreve em ingles no meio da
# prosa em portugues -- em 26/08/2026 o MESMO erro do BABA voltou como
# "Preco $121.08, ABOVE stop-loss de $126", e a checagem que so conhecia
# "acima/abaixo" nao alcancou. O "d[oa]" fica opcional pelo mesmo motivo:
# ingles nao tem o artigo contraido.
_LADO_AFIRMADO = re.compile(
    r"\$\s*(\d+(?:[.,]\d+)?)[^.\n]{0,20}?\b(acima|abaixo|above|below)\s+"
    r"(?:d[oa]\s+|the\s+)?(suporte|resist[êe]ncia|stop|support|resistance)",
    re.IGNORECASE)
_LADO_DE_CIMA = ("acima", "above")

# O lado afirmado SEM preco na clausula -- "-- ainda acima, mas em risco".
#
# Visto em producao (26/08/2026, terceira aparicao do MESMO defeito): BABA a
# 119,83 contra suporte $126 saiu como "ainda acima, mas em risco". A regra
# de cima exige o preco COLADO a afirmacao ("Preco $121, ACIMA do suporte");
# aqui a frase e' eliptica -- o preco esta no MESMO paragrafo ("preco
# 119.83"), so' nao ao lado. Ancoras afirmativas de proposito: "ainda/segue/
# permanece/continua acima" nao casa com "nao esta acima".
_LADO_ELIPTICO = re.compile(
    r"\b(?:ainda|segue|permanece|continua)\s+(acima|abaixo|above|below)\b",
    re.IGNORECASE)

# Se a afirmacao NOMEIA outro referente ("continua acima da MM50"), o nivel
# do plano nao e' o assunto e a checagem se cala.
_REFERENTE_ALHEIO = re.compile(
    r"^\s*(?:d[oa]s?\s+|the\s+)?(?:mm|sma|ema|vwap|m[ée]dia|banda|bollinger)",
    re.IGNORECASE)

_PRECO_NO_PARAGRAFO = re.compile(
    r"pre[çc]o\s+(?:atual\s+)?(?:de\s+)?(?:us?\$\s*)?(\d+(?:[.,]\d+)?)",
    re.IGNORECASE)

# "earnings estao longe", "balanco distante", "fora do horizonte".
_EARNINGS_LONGE = re.compile(
    r"(?:earnings?|balan[cç]o|resultado)[^.\n]{0,60}?"
    r"\b(?:long[eo]s?|distantes?|fora\s+do\s+horizonte)"
    r"|\b(?:earnings?|balan[cç]o)\s+(?:esta[or]?\s+)?longe",
    re.IGNORECASE)

# "reacao media de -1,59%" -- o numero que o texto chama de reacao.
_REACAO_MEDIA_CITADA = re.compile(
    r"rea[cç][ãa]o\s+m[ée]dia\s+(?:de\s+)?([+-]?\d+(?:[.,]\d+)?)\s*%",
    re.IGNORECASE)

# Numero com cara de preco: 2 casas decimais ou cifrao. O contexto de nivel
# (VWAP, media, banda, alvo) e' excluido no uso -- VWAP a 2% do preco NAO e'
# um segundo preco.
_PRECO_LIKE = re.compile(r"(?:us?\$\s*)?(\d{2,5}[.,]\d{2})\b")
_CONTEXTO_DE_NIVEL = re.compile(
    r"(?:vwap|sma|mm|ema|m[ée]dia|banda|bollinger|alvo|suporte|resist\w*|"
    r"stop|s[12]|r[12]|m[áa]xima|m[íi]nima|target)\s*(?:\w+\s+){0,3}$",
    re.IGNORECASE)

# "-20,91% abaixo SMA50" / "3,45% vs SMA50" / "-19,11% abaixo da média
# móvel de 50 dias" -- a distância do preço à média de 50, como o texto a
# escreve. O snapshot traz `pct_above_sma50`, então isto é conferível.
_PCT_VS_SMA50 = re.compile(
    r"([+-]?\d{1,3}[.,]\d{1,2})\s*%\s*"
    r"(?:(abaixo|acima)\s+(?:d[ao]\s+)?)?"
    r"(?:vs\.?\s+)?"
    r"(?:sma\s?50|m[ée]dia\s+m[óo]vel\s+de\s+50|m[ée]dia\s+de\s+50)",
    re.IGNORECASE)

# "Earnings 63 dias (22/out)" / "earnings em 70 dias" -- a CONTAGEM, que o
# texto deriva e às vezes erra mesmo acertando a data.
_DIAS_ATE_EARNINGS = re.compile(
    r"earnings?[^.\n]{0,20}?(\d{1,3})\s*dias|(\d{1,3})\s*dias[^.\n]{0,20}?earnings?",
    re.IGNORECASE)

# "Fear & Greed em 57,6" / "Sentimento do mercado em 57,6" -- o score citado
# na prosa, que agora tem um valor FIXADO no snapshot para confrontar.
_SENTIMENTO_CITADO = re.compile(
    r"(?:fear\s*&?\s*greed|sentimento(?:\s+d[eo]\s+mercado)?)"
    r"[^.\n]{0,25}?(\d{1,3}(?:[.,]\d)?)",
    re.IGNORECASE)

# O texto citando o indice em OUTRO momento ("uma semana atras estava em 45")
# nao esta contradizendo o valor de hoje.
_SENTIMENTO_HISTORICO = re.compile(
    r"semana|m[êe]s|ano|anterior|passad|atr[áa]s|hist[óo]ric|fechamento\s+de\s+ontem",
    re.IGNORECASE)

# "RSI 38.92" / "RSI de 47,81" -- o valor citado. O `(?![<>≤≥])` e o
# lookbehind barram LIMIAR ("RSI <40", "RSI abaixo de 40"), que e' regra e
# nao afirmacao sobre o numero de hoje.
_RSI_CITADO = re.compile(
    r"\brsi\b\s*(?:de\s+|em\s+)?(?![<>≤≥])(?:aproximadamente\s+)?"
    r"(\d{1,3}(?:[.,]\d+)?)",
    re.IGNORECASE)
_RSI_LIMIAR = re.compile(r"rsi[^\d]{0,12}?(?:<|>|≤|≥|abaixo\s+de|acima\s+de|"
                         r"menor|maior)", re.IGNORECASE)

# "preço atual $240.77" / "em $65.48" -- o preco citado para o papel.
_PRECO_CITADO = re.compile(
    r"(?:pre[çc]o[^\d$]{0,15}|\bem\s+|\ba\s+|\best[áa]\s+em\s+)"
    r"(?:US)?\$\s*(\d{1,6}(?:[.,]\d{1,2})?)",
    re.IGNORECASE)

# Cifrao que NAO e' o preco do papel. Sem isto, "Stop-loss em $275, preco
# atual $240.77" acusava o proprio stop de estar errado -- o `em $` casa nos
# dois. Um nivel nomeado antes do valor o desqualifica como cotacao.
_VALOR_DE_NIVEL = re.compile(
    r"(?:stop|suporte|support|resist|alvo|target|take[-\s]?profit|bollinger|"
    r"m[áa]xima|m[íi]nima|\bsma\b|\bmm\s?\d|\bema\b|vwap|upper|lower|"
    r"breakeven|custo|entrada|quebrar)[^$\n]{0,25}$",
    re.IGNORECASE)

_TICKER_FLAT = re.compile(
    r"\b([A-Z]{2,5})\b[^.\n]{0,40}?\b((?i:flat|estavel|estável|lateral|sem variacao|sem variação))\b"
)


# Verbos de intenção de compra no veredito. Extração deliberadamente
# CONSERVADORA (texto do veredito é prosa livre, sem rótulo estruturado):
# exige o verbo na mesma frase do ticker e descarta quando há negação/
# condicional logo antes do verbo ("não é hora de comprar", "evitar
# aumentar") -- falso positivo aqui custa um retry de correção inteiro.
_VERBOS_COMPRA = r"(comprar|compra|aumentar|adicionar|reforcar|iniciar entrada|entrar)"
_NEGACAO = re.compile(
    r"(nao|não|evitar|evite|sem|nem|adiar|esperar( pra| para)?|aguardar( pra| para)?)"
    r"[\s\w]{0,20}$")


def _segmentos_por_ticker(texto: str, tickers) -> dict:
    """{ticker: [trechos]} -- cada ticker fica com o trecho que vai da sua
    mencao ate a mencao do PROXIMO ticker, ou ate o fim da linha.

    Substitui a janela de N caracteres (`{tk}[^.\n]{{0,120}}`) usada antes,
    que tinha dois furos:

      1. parava no primeiro PONTO -- e "$121.08" tem ponto, entao em texto
         com decimal americano a janela morria no meio do preco. Em
         26/08/2026 o veredito veio todo com decimal americano e as checagens
         por ticker viraram letra morta.
      2. tamanho fixo nao alcanca o que o modelo escreve tres frases adiante
         sobre o mesmo papel -- o bullet do ARM cita a data de earnings bem
         depois do preco, e a janela nunca chegava la.

    A fronteira de LINHA continua valendo: cada posicao e' um bullet, e o
    dado de um papel nao pode ser confrontado com o texto de outro."""
    saida: dict = {}
    alvos = [str(tk).upper() for tk in (tickers or []) if tk]
    if not alvos:
        return saida
    padrao = re.compile(r"\b(" + "|".join(re.escape(a.lower()) for a in alvos)
                        + r")\b")
    for linha in _norm(texto).split("\n"):
        marcas = [(m.start(), m.group(1).upper()) for m in padrao.finditer(linha)]
        for i, (pos, tk) in enumerate(marcas):
            fim = marcas[i + 1][0] if i + 1 < len(marcas) else len(linha)
            saida.setdefault(tk, []).append(linha[pos:fim])
    return saida


def _tickers_com_intencao_de_compra(texto: str, universo: list[str]) -> list[str]:
    """Tickers do universo (carteira do snapshot) que o texto recomenda
    comprar/aumentar. Frase = trecho entre pontuações fortes; o verbo tem
    que estar a até ~90 chars do ticker, sem negação imediatamente antes."""
    achados: list[str] = []
    norm = _norm(texto)
    for tk in universo:
        for m in re.finditer(rf"\b{tk.lower()}\b", norm):
            ini, fim = max(0, m.start() - 90), m.end() + 90
            trecho = norm[ini:fim]
            corte_ini = trecho.rfind(".", 0, m.start() - ini)
            corte_fim = trecho.find(".", m.end() - ini)
            frase = trecho[corte_ini + 1: corte_fim if corte_fim != -1 else len(trecho)]
            vm = re.search(_VERBOS_COMPRA, frase)
            if not vm:
                continue
            antes = frase[:vm.start()]
            if _NEGACAO.search(antes):
                continue
            achados.append(tk)
            break
    return achados


def checar_concentracao_veredito(tickers_comprar: list[str]) -> list[str]:
    """Passo 3 do guia Radar IA 2026: veredito que recomenda comprar 2+
    nomes com corr >= CORR_ALTA é o mesmo trade contado duas vezes."""
    erros = []
    for a, b in combinations([t.upper() for t in tickers_comprar], 2):
        c = correlacao(a, b)
        if c and c >= CORR_ALTA:
            erros.append(
                f"CONCENTRAÇÃO: {a}+{b} têm correlação {c:.2f} (janela de 6 "
                f"meses até 14/08/26) — recomendar compra dos dois sem citar "
                f"a concentração é o mesmo trade 2x. Mencione a correlação e "
                f"ajuste a recomendação (ou o sizing) explicitamente.")
    return erros


def lint_veredito(texto: str, snapshot: dict[str, Any],
                   year: int | None = None) -> ValidationReport:
    rep = ValidationReport()
    as_of = _parse_date(snapshot.get("as_of"))
    year = year or as_of.year
    quotes: dict = snapshot.get("quotes", {})
    earnings: dict = snapshot.get("earnings", {})
    technicals: dict = snapshot.get("technicals", {})
    norm_text = _norm(texto)
    # Um trecho por mencao de ticker, com fronteira de linha -- ver
    # _segmentos_por_ticker para o que isto substitui e por que.
    segmentos = _segmentos_por_ticker(
        texto, set(quotes) | set(technicals) | set(earnings))

    # 0) moeda
    #
    # Visto em producao (26/08/2026): "ARM encerra 26/08 em R$ 249,57" -- ARM
    # e' NASDAQ. Nao e' erro de digitacao: e' um numero cinco vezes menor que
    # o real, num texto cuja conclusao era "recomenda-se saida ordenada".
    #
    # A checagem existia desde 25/08 -- no `analise_rapida_validator`, e so'
    # la'. Uma copia por validador e' uma chance de a proxima tela ficar sem;
    # agora mora no nucleo e os dois chamam a mesma.
    #
    # Sobre `texto` e nao `norm_text`: a versao normalizada e' MINUSCULA, "R$"
    # vira "r$", e o padrao nunca casaria. Terceira vez que esta armadilha
    # aparece neste repo.
    frase_moeda = frase_com_moeda_errada(texto)
    if frase_moeda:
        rep.add("ERROR", "MOEDA_ERRADA",
                f"usa R$ para o preço do ativo — os papéis são listados nos "
                f"EUA e o prompt manda não converter; escreva US$. "
                f"Trecho: “{frase_moeda.strip()[:120]}”.")

    # 1) dia da semana citado bate com o calendario?
    for m in _DATE_WEEKDAY.finditer(_norm(texto)):
        # group(4) e' a palavra do dia-da-semana "limpa" (sem o "(" que
        # [\s(]* do group(3) engole pra casar o formato comum "dd/mes
        # (dia-da-semana)") -- usar group(3) aqui compararia contra um
        # prefixo contaminado com "(" e derrubaria toda data CORRETA como
        # falso positivo (real_wd nunca comeca com "(").
        day, mon, wd_claimed = int(m.group(1)), MONTHS_PT[m.group(2)[:3]], m.group(4)
        try:
            d = date(year, mon, day)
        except ValueError:
            continue
        real_wd = WEEKDAYS_PT[d.weekday()]
        if not real_wd.startswith(_norm(wd_claimed)[:5]):
            rep.add("ERROR", "WEEKDAY_WRONG",
                    f"Texto diz '{day:02d}/{m.group(2)} ({wd_claimed}-feira?)', "
                    f"mas {d.isoformat()} e {real_wd}.")

    # 1b) o texto NEGA o dado do ticker que o snapshot trouxe
    #
    # Visto em producao duas vezes (25 e 26/08/2026), sempre no WOLF. Na
    # segunda: "WOLF: Dados tecnicos limitados no painel", enquanto o painel
    # da mesma tela mostrava "WOLF RSI 44 - Subindo 5.7% hoje" -- o unico
    # ticker do dia com sinal destacado.
    #
    # Mesmo defeito que o `ANALISE_NEGA_DADO_PRESENTE` da analise rapida
    # pega: ninguem conferia as afirmacoes do texto sobre a DISPONIBILIDADE
    # do dado, so' sobre o valor dele. E negar dado presente e' pior que
    # omitir -- quem le "dados limitados" para de procurar, e desconta a
    # posicao inteira por uma escassez que nao existe.
    #
    # O que esta checagem NAO faz, de proposito: apontar "sem mudanca
    # estrutural visivel" contra a alta de 5,7% do dia. Estrutura e'
    # tendencia e niveis, e um pregao nao muda estrutura -- um analista
    # cuidadoso defende essa frase. Mesma razao para deixar passar o "sem
    # movimento urgente" da ocorrencia de 25/08: urgencia nao e' magnitude.
    # Apontar as duas seria trocar um achado real por dois palpites.
    for tk in list(technicals):
        if not technicals.get(tk):
            continue
        for seg in segmentos.get(tk, []):
            if not _NEGA_DADO_DO_TICKER.search(seg):
                continue
            rep.add("ERROR", "DADO_DO_TICKER_NEGADO",
                    f"diz que falta dado tecnico, mas o snapshot traz "
                    f"{_o_que_tem(technicals[tk])} para {tk} — quem lê isso "
                    f"para de procurar o que está na mão.", ticker=tk)
            break

    # 2) datas de earnings citadas batem com o painel?
    for tk, edate in earnings.items():
        ed = _parse_date(edate)
        # procura mencoes tipo "earnings ... 11/ago" no trecho do ticker
        vistas: set = set()
        for seg in segmentos.get(tk, []):
            # As tres palavras que `_EARNINGS_COM_DATA` reconhece. O guard
            # conhecia so' duas, entao "24/11, data do proximo balanco" era
            # descartado antes de o regex olhar -- filtro barato mais estreito
            # que a checagem que ele protege e' filtro que esconde achado.
            if not any(p in seg for p in ("earnings", "resultado", "balanc")):
                continue
            for day, mon in _datas_atribuidas_a_earnings(seg):
                if (day, mon) in vistas:
                    continue
                vistas.add((day, mon))
                if (day, mon) != (ed.day, ed.month):
                    # WARN, nao ERROR: visto em producao um caso onde a data
                    # citada nao era a de earnings (era a data de um outro
                    # evento perto do ticker que o regex de contexto pegou
                    # junto por engano) -- PHANTOM_EARNINGS abaixo e' o check
                    # que realmente importa (afirma um evento que nao
                    # aconteceu), este aqui e' so um sinal auxiliar mais
                    # ruidoso, nao vale travar o retry por ele sozinho.
                    rep.add("WARN", "EARNINGS_DATE_MISMATCH",
                            f"Texto cita earnings em {day:02d}/{mon:02d}, "
                            f"painel diz {ed.day:02d}/{ed.month:02d}.", ticker=tk)
        # 3) "pos-earnings" antes do earnings acontecer
        if ed > as_of:
            pat = rf"{tk.lower()}[^.\n]{{0,150}}(pos-earnings|apos (divulgacao de )?earnings|apos resultado)"
            if re.search(pat, norm_text) or re.search(
                rf"(pos-earnings|apos (divulgacao de )?earnings)[^.\n]{{0,80}}{tk.lower()}",
                norm_text,
            ):
                rep.add("ERROR", "PHANTOM_EARNINGS",
                        f"Texto atribui movimento a earnings de {tk}, mas o "
                        f"earnings so ocorre em {ed.isoformat()} (as_of={as_of}). "
                        f"Alucinacao provavel.", ticker=tk)

    # 3b) "hoje"/"amanha" colado a uma data que diz outra coisa.
    #
    # Visto em producao (26/08/2026): o veredito abriu com "NVDA amanha
    # (26/ago)" num dia em que as_of ERA 26/08 -- o painel dizia "hoje". A
    # secao inteira de "URGENCIAS DO PLANO (proximas 24h)" foi escrita em
    # cima disso, mandando aguardar amanha um resultado que sai hoje.
    for pat, ordem in ((_PRAZO_DEPOIS, "data_primeiro"), (_PRAZO_ANTES, "prazo_primeiro")):
        for m in pat.finditer(texto):
            if ordem == "data_primeiro":
                dia, mes_txt, prazo = int(m.group(1)), m.group(2), m.group(3)
            else:
                prazo, dia, mes_txt = m.group(1), int(m.group(2)), m.group(3)
            try:
                citada = date(year, MONTHS_PT[mes_txt.lower()[:3]], dia)
            except (ValueError, KeyError):
                continue
            esperada = as_of if _norm(prazo) == "hoje" else as_of + timedelta(days=1)
            if citada != esperada:
                rep.add("ERROR", "PRAZO_RELATIVO_ERRADO",
                        f"Texto diz '{prazo}' para {dia:02d}/{mes_txt}, mas "
                        f"com as_of={as_of} '{_norm(prazo)}' e "
                        f"{esperada.isoformat()}. O leitor age no dia errado.")

    # 3c) o texto afirma de que lado de um nivel o preco esta, e se contradiz.
    #
    # Visto em producao (26/08/2026): "vender se quebrar suporte $126. Preco
    # ainda $121, ACIMA do suporte" -- 121 e MENOR que 126, o suporte estava
    # rompido, e a leitura virou "aguardando consolidacao". O JSON de saida
    # saiu com BABA: MANTER.
    #
    # Nao consulta o plano: os dois numeros estao no proprio paragrafo, entao
    # a contradicao e' interna e nao depende de payload nenhum.
    for paragrafo in texto.split("\n"):
        niveis = [(m.start(), m.group(1).lower(), float(m.group(2).replace(",", ".")))
                  for m in _NIVEL_COM_VALOR.finditer(paragrafo)]
        if not niveis:
            continue
        for m in _LADO_AFIRMADO.finditer(paragrafo):
            preco = float(m.group(1).replace(",", "."))
            lado, especie = m.group(2).lower(), m.group(3).lower()
            # O nivel pode vir ANTES ("suporte $126. Preco $121, abaixo do
            # suporte") ou DEPOIS ("Preco $121.08, ABOVE stop-loss de $126").
            # Vale o mais proximo da afirmacao, dentro da mesma linha.
            mesmos = [n for n in niveis if n[1].startswith(especie[:5])]
            if not mesmos:
                continue
            _, _, valor = min(mesmos, key=lambda n: abs(n[0] - m.start()))
            if preco == valor:
                continue
            if (preco > valor) != (lado in _LADO_DE_CIMA):
                rep.add("ERROR", "NIVEL_LADO_INVERTIDO",
                        f"Texto diz que ${preco:.2f} esta {lado} do {especie} "
                        f"de ${valor:.2f} -- esta "
                        f"{'acima' if preco > valor else 'abaixo'}. O lado "
                        f"errado inverte a acao que o plano pede.")

        # 3c-bis) o lado ELIPTICO -- "-- ainda acima, mas em risco".
        #
        # Sem preco na clausula a regra de cima nao alcanca; o preco esta no
        # paragrafo ("preco 119,83"), a comparacao e' a mesma. So' roda quando
        # a afirmacao completa nao casou (senao reportaria o mesmo erro duas
        # vezes) e quando ha' UM preco e o referente nao e' outra coisa.
        if not _LADO_AFIRMADO.search(paragrafo):
            precos_par = _PRECO_NO_PARAGRAFO.findall(paragrafo)
            if len(set(precos_par)) == 1:
                preco_par = float(precos_par[0].replace(",", "."))
                for m in _LADO_ELIPTICO.finditer(paragrafo):
                    depois = paragrafo[m.end():m.end() + 30]
                    if _REFERENTE_ALHEIO.match(depois):
                        continue  # "continua acima da MM50" fala de outra coisa
                    lado = m.group(1).lower()
                    _, especie, valor = min(
                        niveis, key=lambda n: abs(n[0] - m.start()))
                    if preco_par == valor:
                        continue
                    if (preco_par > valor) != (lado in _LADO_DE_CIMA):
                        rep.add("ERROR", "NIVEL_LADO_INVERTIDO",
                                f"Texto diz '{m.group(0)}' com o {especie} de "
                                f"${valor:.2f} e o preço do parágrafo em "
                                f"${preco_par:.2f} -- está "
                                f"{'acima' if preco_par > valor else 'abaixo'}. "
                                f"O lado errado esconde que o gatilho do "
                                f"plano já disparou.")
                        break

    # 3d) distância à SMA50 citada bate com `pct_above_sma50`?
    #
    # Visto em producao (26/08/2026): o paragrafo do INTC deu DOIS numeros
    # para a mesma relacao -- "-20,91% abaixo SMA50 em $106" e "-19,11%
    # abaixo media movel de 50 dias". Com $106 e preco $85,74 o certo e
    # -19,11%; o outro nao vem de lugar nenhum.
    #
    # Confere contra o DADO, nao so contra si mesmo: assim o numero certo
    # passa e so o inventado cai.
    for tk, tec in technicals.items():
        real = tec.get("pct_above_sma50")
        if not isinstance(real, (int, float)):
            continue
        for seg in segmentos.get(tk, []):
            for pm in _PCT_VS_SMA50.finditer(seg):
                citado = float(pm.group(1).replace(",", "."))
                # "20,91% ABAIXO" e' o mesmo que -20,91%: a palavra carrega o
                # sinal quando o numero vem sem ele.
                if pm.group(2) and pm.group(2).lower() == "abaixo":
                    citado = -abs(citado)
                elif pm.group(2) and pm.group(2).lower() == "acima":
                    citado = abs(citado)
                if abs(citado - real) > SMA50_TOLERANCIA_PP:
                    rep.add("ERROR", "SMA50_DISTANCIA_ERRADA",
                            f"Texto cita {citado:+.2f}% em relacao a SMA50, "
                            f"snapshot traz {real:+.2f}%.", ticker=tk)

    # 3e) a CONTAGEM de dias ate earnings bate com a data?
    #
    # Visto em producao (26/08/2026): "INTC: Earnings 63 dias (22/out)". A
    # data esta certa e a conta nao -- de 26/08 a 22/10 sao 57 dias, que e'
    # o que o painel mostrava. EARNINGS_DATE_MISMATCH so olha a data, entao
    # este erro passava inteiro.
    for tk, edate in earnings.items():
        ed = _parse_date(edate)
        real_dias = (ed - as_of).days
        if real_dias < 0:
            continue
        for seg in segmentos.get(tk, []):
            for dm in _DIAS_ATE_EARNINGS.finditer(seg):
                citado = int(dm.group(1) or dm.group(2))
                if abs(citado - real_dias) > DIAS_EARNINGS_TOLERANCIA:
                    rep.add("ERROR", "DIAS_ATE_EARNINGS_ERRADO",
                            f"Texto diz {citado} dias ate o earnings de "
                            f"{ed.isoformat()}, mas de {as_of} sao "
                            f"{real_dias}.", ticker=tk)

    # 3f) o sentimento citado bate com o valor FIXADO?
    #
    # So faz sentido depois de fixar: antes, o texto lia o indice pela
    # ferramenta e a tela lia de novo pela /api/macro, entao qualquer
    # diferenca era deriva intradia e nao erro. Com um valor unico no
    # snapshot, citar outro numero passa a ser afirmacao sem lastro.
    fixado = (snapshot.get("sentimento") or {}).get("score")
    if isinstance(fixado, (int, float)):
        for m in _SENTIMENTO_CITADO.finditer(texto):
            trecho = texto[max(0, m.start() - 60):m.end() + 60]
            if _SENTIMENTO_HISTORICO.search(trecho):
                continue  # fala de outro momento, nao do de hoje
            citado = float(m.group(1).replace(",", "."))
            if abs(citado - float(fixado)) > SENTIMENTO_TOLERANCIA:
                rep.add("ERROR", "SENTIMENTO_ERRADO",
                        f"Texto cita Fear & Greed {citado}, mas o snapshot "
                        f"fixou {fixado}. O indice anda intradia -- use o "
                        f"valor do snapshot, nao uma releitura.")

    # 3g) o RSI citado bate com o do snapshot?
    #
    # Visto em producao (26/08/2026): o veredito deu RSI 31,78 para BABA
    # (painel: 49) e 51,52 para WOLF (painel: 44) -- seis dos oito tickers
    # com o numero certo e dois com numero de lugar nenhum. RSI_STALE so
    # olhava a DATA do indicador, nunca o valor CITADO.
    for tk, tec in technicals.items():
        real = tec.get("rsi")
        if not isinstance(real, (int, float)):
            continue
        for seg in segmentos.get(tk, []):
            for rm in _RSI_CITADO.finditer(seg):
                antes = seg[max(0, rm.start() - 20):rm.end()]
                if _RSI_LIMIAR.search(antes):
                    continue  # "RSI < 40" e' regra, nao afirmacao
                citado = float(rm.group(1).replace(",", "."))
                if abs(citado - float(real)) > RSI_CITADO_TOLERANCIA:
                    rep.add("ERROR", "RSI_CITADO_ERRADO",
                            f"Texto cita RSI {citado}, snapshot traz "
                            f"{real:.2f}.", ticker=tk)

    # 3h) "earnings estão longe" com o balanço na porta.
    #
    # Visto em producao (26/08/2026): o veredito escreveu, para NVDA,
    # "Earnings estão longe (próximo em nov/dez)" com o balanço saindo
    # NAQUELE dia, depois do fechamento. `BLOCO_EARNINGS_NAO_ESTA_PROXIMO`
    # cobre so' o inverso (declarar proximo estando longe), e "nov/dez" sem
    # dia numerico escapa do casador de datas. Negar o evento e' pior que
    # inflar um: quem le "longe" atravessa o print sem saber.
    for tk, data in earnings.items():
        try:
            dias = (_parse_date(data) - as_of).days
        except Exception:
            continue
        if not (0 <= dias <= 7):
            continue
        for seg in segmentos.get(tk, []):
            m = _EARNINGS_LONGE.search(_norm(seg))
            if m:
                rep.add("ERROR", "EARNINGS_NEGADO_IMINENTE",
                        f"Texto diz que os earnings estão longe, mas o "
                        f"snapshot marca {data} -- {dias} dia(s). "
                        f"Trecho: “{m.group(0)[:80]}”.", ticker=tk)
                break

    # 3i) a variação DO DIA vestida de estatística histórica.
    #
    # Visto em producao (26/08/2026): "NVDA experimenta reação média de
    # -1,59% nos 21 pregões pós-earnings" -- -1,59% é a variação DAQUELE
    # pregão; a média real do D+21 é -3,25%. O erro típico não é inventar
    # número, é pegar o número CERTO com o rótulo errado (mesma família da
    # ESTATISTICA_TROCADA da análise rápida). Gated no snapshot: sem
    # `reacao_earnings` para o ticker não há fato para conferir, e a
    # coincidência honesta não vira acusação.
    for tk, r in (snapshot.get("reacao_earnings") or {}).items():
        if not isinstance(r, dict):
            continue
        dia = (quotes.get(tk) or {}).get("change_percent")
        if dia is None:
            continue
        reais = [v for v in (r.get("reacao_media_pct"),
                             r.get("reacao_abs_media_pct")) if v is not None]
        for seg in segmentos.get(tk, []):
            for m in _REACAO_MEDIA_CITADA.finditer(_norm(seg)):
                citado = float(m.group(1).replace(",", "."))
                if any(abs(citado - v) <= 0.11 for v in reais):
                    continue  # bate com a estatística de verdade
                if abs(citado - float(dia)) <= 0.02:
                    rep.add("ERROR", "REACAO_E_VARIACAO_DO_DIA",
                            f"chama {citado:+.2f}% de 'reação média', mas esse "
                            f"é o percentual DESTE pregão "
                            f"(change {float(dia):+.2f}%). A reação histórica "
                            f"do snapshot é |média| "
                            f"{r.get('reacao_abs_media_pct')}%"
                            + (f", assinada {r.get('reacao_media_pct'):+.2f}%"
                               if r.get('reacao_media_pct') is not None
                               else "") + ".", ticker=tk)
                    break
            else:
                continue
            break

    # 3j) dois preços para o mesmo papel no mesmo parágrafo.
    #
    # Visto em producao (26/08/2026): o parágrafo de ARM usou 250,71 e 251,06
    # como preço na MESMA frase. Um deles é o do snapshot; o outro não vem de
    # lugar nenhum, e o leitor não sabe qual dos dois baliza os níveis.
    #
    # Janela de ±1% do preço do snapshot, estreita de propósito: VWAP e MM20
    # costumam ficar a 2-5%, e nível citado perto do preço não é um segundo
    # preço. O contexto de nível é excluído explicitamente por cima.
    for tk, q in quotes.items():
        preco_snap = q.get("price")
        if not preco_snap:
            continue
        for seg in segmentos.get(tk, []):
            vistos: set = set()
            for m in _PRECO_LIKE.finditer(seg):
                antes = seg[max(0, m.start() - 28):m.start()]
                if _CONTEXTO_DE_NIVEL.search(antes):
                    continue
                v = float(m.group(1).replace(",", "."))
                if abs(v - preco_snap) / preco_snap <= 0.01:
                    vistos.add(v)
            if len(vistos) >= 2:
                lista = ", ".join(f"{v:.2f}" for v in sorted(vistos))
                rep.add("WARN", "PRECO_INCONSISTENTE",
                        f"o parágrafo usa {len(vistos)} preços diferentes "
                        f"para o papel ({lista}); o snapshot marca "
                        f"{preco_snap:.2f}. Um número só -- o do snapshot.",
                        ticker=tk)
                break


    # 3h) o preco citado bate com o do snapshot?
    #
    # Visto em producao (26/08/2026): "WOLF em $65.48" num dia em que o papel
    # negociava a $26,57 -- 2,5x. Um preco errado envenena tudo que vem
    # depois dele (distancia a media, stop, tese inteira), e nenhuma checagem
    # olhava o preco CITADO.
    for tk, q in quotes.items():
        real = q.get("price")
        if not isinstance(real, (int, float)) or not real:
            continue
        for seg in segmentos.get(tk, []):
            for pm in _PRECO_CITADO.finditer(seg):
                # Ate o INICIO do casamento: incluir o proprio "$240.77"
                # colocaria um cifrao no prefixo, e o `[^$\n]` do padrao de
                # nivel nunca conseguiria atravessa-lo.
                if _VALOR_DE_NIVEL.search(seg[:pm.start()]):
                    continue  # e' stop/suporte/alvo, nao a cotacao
                citado = float(pm.group(1).replace(",", "."))
                if not citado:
                    continue
                desvio = abs(citado - float(real)) / float(real) * 100.0
                if desvio > PRECO_CITADO_TOLERANCIA_PCT:
                    rep.add("ERROR", "PRECO_CITADO_ERRADO",
                            f"Texto cita ${citado:.2f}, snapshot traz "
                            f"${real:.2f} ({desvio:.0f}% de diferenca).",
                            ticker=tk)

    # 4) percentuais citados por ticker batem com o snapshot do dia?
    for m in _TICKER_PCT.finditer(texto):
        tk, pct_s = m.group(1), m.group(2).replace(",", ".")
        if tk not in quotes:
            continue
        cited = float(pct_s)
        real = quotes[tk].get("change_percent_verified")
        if real is None:
            p, pc = quotes[tk].get("price"), quotes[tk].get("previous_close")
            real = _pct_change(p, pc) if p and pc else None
        if real is None:
            continue
        # so compara se o texto parece falar do dia (evita falso positivo
        # com percentuais historicos tipo "+18.4% em 30 jul")
        ctx = texto[max(0, m.start() - 60):m.end() + 60].lower()
        fala_do_dia = any(w in ctx for w in ("hoje", "fecha", "fechou", "no dia", "caiu", "subiu"))
        tem_data_explicita = bool(re.search(
            r"\d{1,2}\s*/?\s*(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)", ctx,
        ))
        if fala_do_dia and not tem_data_explicita:
            diff = abs(cited - real)
            sign_flip = (cited > 0) != (real > 0) and abs(real) > 0.05
            if sign_flip:
                rep.add("ERROR", "TEXT_PCT_SIGN_FLIP",
                        f"Texto cita {cited:+.2f}% mas o dia foi {real:+.2f}%.",
                        ticker=tk)
            elif diff > MENTION_PCT_TOLERANCE_PP:
                rep.add("WARN", "TEXT_PCT_MISMATCH",
                        f"Texto cita {cited:+.2f}%, snapshot verificado "
                        f"{real:+.2f}% (diff {diff:.2f}pp).", ticker=tk)

    # 5) "flat"/"estável" citado por ticker bate com o snapshot? (a checagem
    # acima só pega percentual NUMÉRICO citado errado -- "SMCI está flat"
    # quando o dia real foi +2,4% passava batido por não ter número nenhum
    # pra comparar. Visto em produção: mesmo ticker descrito como "flat" no
    # parágrafo técnico e "+2,4%" no setorial no mesmo texto.)
    for m in _TICKER_FLAT.finditer(texto):
        tk = m.group(1)
        if tk not in quotes:
            continue
        real = quotes[tk].get("change_percent_verified")
        if real is None:
            p, pc = quotes[tk].get("price"), quotes[tk].get("previous_close")
            real = _pct_change(p, pc) if p and pc else None
        if real is None:
            continue
        if abs(real) > FLAT_CLAIM_TOLERANCE_PP:
            rep.add("WARN", "TEXT_FLAT_MISMATCH",
                    f"Texto descreve {tk} como \"{m.group(2)}\", mas o dia "
                    f"real foi {real:+.2f}% (fora da faixa considerada "
                    f"flat, ±{FLAT_CLAIM_TOLERANCE_PP:.1f}pp).", ticker=tk)

    # 6) "distribuição" atribuída a ticker em perfil de FUNDO, não de topo.
    #
    # Distribuição é padrão de topo (mãos fortes vendendo perto de uma máxima).
    # RSI perto de sobrevenda com preço bem abaixo da SMA50 é o oposto:
    # capitulação/teste de suporte. O prompt já dizia isso em prosa e não
    # segurou -- ver comentário em DISTRIB_RSI_MAX.
    if "distribuic" in norm_text:  # cobre distribuição/distribuicao/distributiva
        for tk, tec in technicals.items():
            rsi = tec.get("rsi")
            pct_sma50 = tec.get("pct_above_sma50")
            if not isinstance(rsi, (int, float)) or not isinstance(pct_sma50, (int, float)):
                continue
            if rsi > DISTRIB_RSI_MAX or pct_sma50 > DISTRIB_PCT_SMA50_MAX:
                continue
            # o ticker precisa aparecer perto da palavra pra não pegar um
            # "distribuição" dito sobre outro ativo do mesmo parágrafo
            perto = any(
                tk.lower() in norm_text[max(0, m.start() - 200):m.end() + 200]
                for m in re.finditer("distribuic", norm_text)
            )
            if not perto:
                continue
            rep.add("ERROR", "DISTRIBUICAO_INVERTIDA",
                    f"Texto associa {tk} a 'distribuição', mas RSI {rsi:.1f} "
                    f"(≤{DISTRIB_RSI_MAX:.0f}) e {pct_sma50:+.1f}% vs SMA50 "
                    f"(≤{DISTRIB_PCT_SMA50_MAX:.0f}%) são perfil de FUNDO — "
                    f"capitulação/teste de suporte, não topo. Distribuição "
                    f"pressupõe estar perto de uma máxima.", ticker=tk)

    # 7) concentração por correlação (Radar IA 2026): veredito recomendando
    # comprar/aumentar 2+ nomes com corr >= 0.70 SEM mencionar a concentração
    # é o mesmo trade recomendado duas vezes como se fossem independentes
    # (MU-SNDK 0.82). Só dispara quando o texto não fala de correlação/
    # concentração -- se o veredito já nomeia o risco, a recomendação dupla
    # é decisão consciente, não descuido.
    if not re.search(r"correlac|concentrac|mesmo trade|mesmo cluster", norm_text):
        compraveis = _tickers_com_intencao_de_compra(texto, list(quotes))
        for erro in checar_concentracao_veredito(compraveis):
            rep.add("ERROR", "CONCENTRACAO_CORRELACAO", erro)

    return rep


# ------------------------------------------------- bloco estruturado (20/08) ---
#
# Etapa 4 do motor de pesquisa auditável: o LLM passa a emitir, no FIM do
# veredito, um bloco JSON com a decisão por ticker (action/confidence/
# reason_codes), e o texto vira a EXPLICAÇÃO dessa estrutura -- não o
# contrário. O motivo é a fragilidade que a auditoria externa apontou e que
# os incidentes já tinham mostrado: regex sobre prosa detecta intenção de
# compra "conservadoramente", e prosa financeira é semanticamente traiçoeira
# ("apesar da deterioração, a assimetria favorece uma entrada..."). Com o
# bloco, a intenção é DECLARADA; o regex vira contraprova (JSON x texto
# divergirem é ERRO), e as regras determinísticas (concentração, veto de
# earnings, razão contradita pelo dado) rodam sobre estrutura, não sobre
# interpretação.

ACOES_VALIDAS = {"COMPRAR", "AUMENTAR", "MANTER", "REDUZIR", "VENDER", "AGUARDAR"}
# Acoes que ATENDEM uma ordem de venda do plano. REDUZIR entra: vender parte
# e' cumprir um plano que manda reduzir exposicao, e tratar como contradicao
# transformaria a checagem em exigencia de tudo-ou-nada.
ACOES_DE_SAIDA = {"VENDER", "REDUZIR"}

# A acao do item do plano que constitui ORDEM DE VENDA. "Monitorar", "aguardar
# earnings" e "reavaliar" sao itens de acompanhamento, nao ordens -- e' por
# isso que a checagem casa o VERBO e nao a existencia do item.
_ORDEM_DE_VENDA = re.compile(
    r"\bvend\w+|\bsai[rd]\w*|\bzer\w+\s+(?:a\s+)?posi[cç][aã]o|"
    r"\bstop[-\s]?loss\s+acionado|\brealizar?\s+(?:o\s+)?lucro|"
    r"\btake[-\s]?profit", re.IGNORECASE)

ACOES_DE_COMPRA = {"COMPRAR", "AUMENTAR"}

# Vocabulário CONHECIDO de reason_codes. Código fora da lista é WARN, não
# ERROR: o vocabulário evolui, e travar um retry inteiro por um código novo
# razoável seria pior que registrá-lo para promoção posterior. Os códigos
# RSI_*/EARNINGS_* têm checagem dura contra o snapshot logo abaixo.
REASON_CODES_CONHECIDOS = {
    "RSI_SOBRECOMPRADO", "RSI_SOBREVENDIDO", "TENDENCIA_ALTA", "TENDENCIA_BAIXA",
    "EARNINGS_PROXIMO", "RISCO_CORRELACAO", "MACRO_ADVERSO", "MACRO_FAVORAVEL",
    "SUPORTE_PROXIMO", "RESISTENCIA_PROXIMA", "VOLUME_FRACO", "VOLUME_FORTE",
    "VALUATION_ESTICADO", "VALUATION_DESCONTADO", "PLANO_DE_SAIDA",
    "SENTIMENTO_EXTREMO", "CENARIO_EMPATE",
    # Promovido em 20/08/2026: no primeiro veredito real do contrato, o
    # modelo usou VALUATION_ESTICADO para descrever run-up pré-earnings de
    # +15% (MRVL) -- evidência de preço esticado, não de múltiplo. O rótulo
    # certo merecia existir; é a evolução de vocabulário que o WARN permite.
    "RUNUP_ESTICADO",
    # 25/08/2026: a tese de IA/data center vira razão DECLARÁVEL -- e, por
    # isso, checável. Só vale quando o capex agregado do snapshot está
    # mesmo na direção citada (ver BLOCO_CAPEX_CONTRADITO abaixo).
    "CAPEX_ACELERANDO", "CAPEX_DESACELERANDO",
    # 25/08/2026: fôlego de caixa vira razão declarável pelo mesmo motivo do
    # capex -- é fato datado no snapshot, então é checável. CAIXA_CURTO exige
    # que o ticker esteja mesmo queimando com fôlego abaixo do limite, e
    # CAIXA_CONFORTAVEL exige o contrário (ver BLOCO_CAIXA_CONTRADITO).
    "CAIXA_CURTO", "CAIXA_CONFORTAVEL",
    # A série que atravessa reestruturação não permite comparação a/a -- o
    # rótulo existe para o modelo poder DIZER que sabe disso em vez de
    # comparar assim mesmo (caso WOLF).
    "BALANCO_REESTRUTURADO",
}

# Abaixo de quantos trimestres de fôlego o caixa é "curto". Quatro trimestres
# = um ano: abaixo disso a empresa precisa de captação, venda de ativo ou
# reviravolta operacional DENTRO do horizonte em que se opera o papel, e isso
# é risco de tese, não ruído de balanço.
FOLEGO_CURTO_TRIMESTRES = 4.0
RSI_SOBRECOMPRADO_MIN = 65.0
RSI_SOBREVENDIDO_MAX = 35.0
# Janela do veto de catalisador (mesma convenção do ConfluenceEngine):
# COMPRAR com earnings a <= 2 pregões exige EARNINGS_PROXIMO nos
# reason_codes -- a compra pode até ser defensável, mas nunca inconsciente.
EARNINGS_PROXIMO_DIAS = 2
# Teto de confiança para quem atravessa o balanço exposto. 0,75 porque o
# proprio bloco usa 0,95 para "plano manda vender" e 0,45-0,65 para leituras
# tecnicas -- acima de 0,75 na vespera e' certeza sobre um numero que ainda
# nao saiu.
CONFIANCA_MAXIMA_NA_VESPERA = 0.75
# A partir de quantos dias EARNINGS_PROXIMO deixa de ser defensavel. Bem
# acima da janela do veto (2 pregoes) de proposito: "proximo" e' julgamento, e
# apontar aos 12 dias so' geraria alarme falso. Aos 70 -- o caso real -- nao
# ha' o que discutir.
EARNINGS_LONGE_DIAS = 30

# Acima/abaixo disto a tendência declarada tem que bater com onde o preço
# está em relação à média de 50. A faixa morta no meio existe porque perto da
# média não há tendência nenhuma a declarar, e exigir uma seria inventar.
TENDENCIA_PCT_SMA50 = 5.0

# Folga entre a distância citada e a do snapshot. Um ponto percentual cobre
# arredondamento de SMA50 escrita como "$106" no texto.
SMA50_TOLERANCIA_PP = 1.0

# Folga no score de sentimento. O valor esta FIXADO no snapshot, entao a
# folga so cobre arredondamento de uma casa decimal -- nao a deriva do
# indice, que e' justamente o que fixar veio eliminar.
SENTIMENTO_TOLERANCIA = 0.1

# O painel arredonda o RSI para inteiro e o texto costuma dar duas casas --
# 1,5 ponto cobre isso sem deixar passar um numero de outro papel.
RSI_CITADO_TOLERANCIA = 1.5

# Preco citado x snapshot, em %. Generoso porque o snapshot fecha no candle
# de `as_of` e a geracao pode acontecer com o pregao andando; ainda assim
# pega o WOLF a $65,48 quando o dado dizia $26,57.
PRECO_CITADO_TOLERANCIA_PCT = 10.0

# Folga na contagem de dias até earnings: o texto pode contar em pregões e o
# snapshot em dias corridos, e um fim de semana no meio explica alguns dias.
DIAS_EARNINGS_TOLERANCIA = 4

_BLOCO_JSON_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def extrair_bloco_estruturado(texto: str) -> tuple[dict | None, str | None]:
    """(bloco, erro). Pega o ÚLTIMO bloco ```json``` que contenha "tickers"
    -- o formato pede o bloco no fim, mas o texto pode legitimamente conter
    outros trechos de código antes. Ausente -> (None, None); presente mas
    inválido -> (None, motivo) para o retry corrigir com precisão."""
    import json as _json
    candidatos = [m.group(1) for m in _BLOCO_JSON_RE.finditer(texto)
                  if '"tickers"' in m.group(1)]
    if not candidatos:
        return None, None
    try:
        bloco = _json.loads(candidatos[-1])
    except ValueError as e:
        return None, f"bloco JSON invalido: {e}"
    if not isinstance(bloco, dict) or not isinstance(bloco.get("tickers"), list):
        return None, 'bloco precisa ser um objeto com a lista "tickers"'
    return bloco, None


def validar_bloco_estruturado(bloco: dict, snapshot: dict[str, Any]) -> ValidationReport:
    """Schema + coerência do bloco contra o DADO do snapshot -- tudo
    determinístico, nada de interpretação de prosa."""
    rep = ValidationReport()
    universo = list(snapshot.get("quotes", {}))
    technicals: dict = snapshot.get("technicals", {})
    earnings: dict = snapshot.get("earnings", {})
    as_of = _parse_date(snapshot.get("as_of"))

    vistos: list[str] = []
    for item in bloco.get("tickers", []):
        if not isinstance(item, dict) or not item.get("ticker"):
            rep.add("ERROR", "BLOCO_ITEM_INVALIDO", f"item sem ticker: {item!r}")
            continue
        tk = str(item["ticker"]).upper()
        if tk in vistos:
            rep.add("ERROR", "BLOCO_TICKER_DUPLICADO",
                    "ticker aparece duas vezes no bloco.", ticker=tk)
            continue
        vistos.append(tk)
        if universo and tk not in universo:
            rep.add("ERROR", "BLOCO_TICKER_DESCONHECIDO",
                    f"{tk} não está na carteira do snapshot ({', '.join(universo)}).",
                    ticker=tk)
            continue

        acao = str(item.get("action", "")).upper()
        if acao not in ACOES_VALIDAS:
            rep.add("ERROR", "BLOCO_ACTION_INVALIDA",
                    f"action \"{item.get('action')}\" fora do vocabulário "
                    f"{sorted(ACOES_VALIDAS)}.", ticker=tk)
            continue

        conf = item.get("confidence")
        if not isinstance(conf, (int, float)) or not (0.0 <= float(conf) <= 1.0):
            rep.add("ERROR", "BLOCO_CONFIDENCE_INVALIDA",
                    f"confidence {conf!r} precisa ser número em [0, 1].", ticker=tk)

        codes = item.get("reason_codes")
        if not isinstance(codes, list) or not codes:
            rep.add("ERROR", "BLOCO_SEM_REASON_CODES",
                    "reason_codes vazio: decisão sem razão declarada não é "
                    "auditável.", ticker=tk)
            codes = []
        codes = [str(c).upper() for c in codes]
        for c in codes:
            if c not in REASON_CODES_CONHECIDOS:
                rep.add("WARN", "BLOCO_REASON_DESCONHECIDO",
                        f"reason_code \"{c}\" fora do vocabulário conhecido.",
                        ticker=tk)

        # Razão contradita pelo dado é pior que razão ausente: o leitor
        # confia no rótulo. RSI_* só pode ser citado quando o RSI do
        # snapshot sustenta.
        rsi = (technicals.get(tk) or {}).get("rsi")
        if isinstance(rsi, (int, float)):
            if "RSI_SOBRECOMPRADO" in codes and rsi < RSI_SOBRECOMPRADO_MIN:
                rep.add("ERROR", "BLOCO_REASON_CONTRADITO",
                        f"RSI_SOBRECOMPRADO com RSI {rsi:.1f} "
                        f"(< {RSI_SOBRECOMPRADO_MIN:.0f}).", ticker=tk)
            if "RSI_SOBREVENDIDO" in codes and rsi > RSI_SOBREVENDIDO_MAX:
                rep.add("ERROR", "BLOCO_REASON_CONTRADITO",
                        f"RSI_SOBREVENDIDO com RSI {rsi:.1f} "
                        f"(> {RSI_SOBREVENDIDO_MAX:.0f}).", ticker=tk)

        # Tendencia declarada contra onde o preco esta em relacao a SMA50.
        #
        # Visto em producao (26/08/2026): SKHY saiu com TENDENCIA_ALTA no
        # bloco enquanto a prosa do mesmo veredito dizia "-21,18% abaixo
        # SMA50 -- correcao em progresso". O rotulo e' o que a maquina le.
        pct_sma50 = (technicals.get(tk) or {}).get("pct_above_sma50")
        if isinstance(pct_sma50, (int, float)):
            if "TENDENCIA_ALTA" in codes and pct_sma50 < -TENDENCIA_PCT_SMA50:
                rep.add("ERROR", "BLOCO_REASON_CONTRADITO",
                        f"TENDENCIA_ALTA com o preco {pct_sma50:+.1f}% em "
                        f"relacao a SMA50.", ticker=tk)
            if "TENDENCIA_BAIXA" in codes and pct_sma50 > TENDENCIA_PCT_SMA50:
                rep.add("ERROR", "BLOCO_REASON_CONTRADITO",
                        f"TENDENCIA_BAIXA com o preco {pct_sma50:+.1f}% em "
                        f"relacao a SMA50.", ticker=tk)

        # A tese de data center só pode ser citada na direção que o dado
        # mostra. Sem isto, CAPEX_ACELERANDO viraria o rótulo bonito que
        # justifica qualquer compra -- exatamente o que a validação por
        # razão existe para impedir.
        capex = snapshot.get("capex_hyperscalers") or {}
        direcao = str(capex.get("direcao") or "")
        if direcao:
            if "CAPEX_ACELERANDO" in codes and direcao != "acelerando":
                rep.add("ERROR", "BLOCO_CAPEX_CONTRADITO",
                        f"CAPEX_ACELERANDO, mas o capex agregado de "
                        f"{capex.get('trimestre')} está {direcao} "
                        f"(t/t {capex.get('variacaoQoQPct')}%).", ticker=tk)
            if "CAPEX_DESACELERANDO" in codes and direcao != "desacelerando":
                rep.add("ERROR", "BLOCO_CAPEX_CONTRADITO",
                        f"CAPEX_DESACELERANDO, mas o capex agregado de "
                        f"{capex.get('trimestre')} está {direcao} "
                        f"(t/t {capex.get('variacaoQoQPct')}%).", ticker=tk)
        elif "CAPEX_ACELERANDO" in codes or "CAPEX_DESACELERANDO" in codes:
            rep.add("ERROR", "BLOCO_CAPEX_SEM_DADO",
                    "cita a direção do capex, mas o snapshot do dia não tem "
                    "o dado agregado -- razão sem fato por trás.", ticker=tk)

        # Fôlego de caixa: mesma regra do capex. Um rótulo sobre solidez
        # financeira que ninguém confere é o rótulo bonito que justifica
        # qualquer compra -- e "a empresa tem caixa" é dos mais fáceis de
        # afirmar sem olhar o balanço.
        folego = (snapshot.get("folego_de_caixa") or {}).get(tk) or {}
        if folego.get("disponivel"):
            tri = folego.get("folegoTrimestres")
            gera = bool(folego.get("geraCaixa"))
            curto = tri is not None and tri < FOLEGO_CURTO_TRIMESTRES
            if "CAIXA_CURTO" in codes and not curto:
                situacao = ("gera caixa" if gera else
                            f"fôlego de {tri} trimestres" if tri is not None
                            else "queima abaixo do piso de medição")
                rep.add("ERROR", "BLOCO_CAIXA_CONTRADITO",
                        f"CAIXA_CURTO, mas o balanço de "
                        f"{folego.get('trimestre')} mostra {situacao}.", ticker=tk)
            if "CAIXA_CONFORTAVEL" in codes and curto:
                rep.add("ERROR", "BLOCO_CAIXA_CONTRADITO",
                        f"CAIXA_CONFORTAVEL, mas o balanço de "
                        f"{folego.get('trimestre')} dá fôlego de {tri} "
                        f"trimestres (< {FOLEGO_CURTO_TRIMESTRES:.0f}).", ticker=tk)
            if "BALANCO_REESTRUTURADO" in codes and not folego.get("quebraDeSerie"):
                rep.add("ERROR", "BLOCO_CAIXA_CONTRADITO",
                        "BALANCO_REESTRUTURADO, mas a série do snapshot não "
                        "tem quebra marcada.", ticker=tk)
        elif {"CAIXA_CURTO", "CAIXA_CONFORTAVEL"} & set(codes):
            rep.add("ERROR", "BLOCO_CAIXA_SEM_DADO",
                    "cita o caixa da empresa, mas o snapshot do dia não tem "
                    "o balanço dela -- razão sem fato por trás.", ticker=tk)

        # O inverso do veto: EARNINGS_PROXIMO declarado com o balanco LONGE.
        #
        # Visto em producao (26/08/2026): um veredito de ARM declarou
        # EARNINGS_PROXIMO enquanto a PROPRIA PROSA dizia "Earnings em 70 dias
        # (04/11/2026) -- fora da zona imediata" e o painel dizia "em 70d".
        #
        # `EARNINGS_PROXIMO_DIAS` so' EXIGIA o codigo numa compra as vesperas;
        # nada impedia declara-lo a dois meses de distancia. E a razao inflada
        # e' pior que a ausente: ela empresta urgencia a uma decisao que nao
        # tem nenhuma, e o leitor reorganiza o dia por causa dela.
        #
        # A folga e' generosa de proposito -- "proximo" e' julgamento, e
        # discutir se 12 dias contam nao vale um alarme falso. So' aponta
        # quando a distancia e' indefensavel.
        if "EARNINGS_PROXIMO" in codes and tk in earnings:
            try:
                dias_ate = (_parse_date(earnings[tk]) - as_of).days
            except Exception:
                dias_ate = None
            if dias_ate is not None and dias_ate > EARNINGS_LONGE_DIAS:
                rep.add("ERROR", "BLOCO_EARNINGS_NAO_ESTA_PROXIMO",
                        f"declara EARNINGS_PROXIMO, mas o balanço é em "
                        f"{dias_ate} dia(s) ({earnings[tk]}) — razão sem fato "
                        f"por trás empresta urgência a uma decisão que não "
                        f"tem.", ticker=tk)

        # Veto de catalisador, agora sobre estrutura: comprar às vésperas de
        # earnings sem declarar EARNINGS_PROXIMO é decisão inconsciente.
        if acao in ACOES_DE_COMPRA and tk in earnings:
            try:
                dias = (_parse_date(earnings[tk]) - as_of).days
            except Exception:
                dias = None
            if dias is not None and 0 <= dias <= EARNINGS_PROXIMO_DIAS \
                    and "EARNINGS_PROXIMO" not in codes:
                rep.add("ERROR", "BLOCO_COMPRA_SEM_VETO_DECLARADO",
                        f"{acao} com earnings em {dias} pregão(ões) sem "
                        f"EARNINGS_PROXIMO nos reason_codes.", ticker=tk)
            # O GATE endurece o veto (26/08/2026): declarar EARNINGS_PROXIMO
            # tornava a compra consciente, mas consciente não é sustentada --
            # na véspera a técnica que justificaria COMPRAR ainda não sabe o
            # número que sai em horas. A Análise Rápida ganhou este gate na
            # #413; o bloco do veredito era a última porta aberta. VENDER e
            # REDUZIR ficam fora do gate: tirar risco não espera balanço.
            if dias is not None and 0 <= dias <= EARNINGS_PROXIMO_DIAS:
                rep.add("ERROR", "BLOCO_DIRECIONAL_NA_VESPERA",
                        f"{acao} com o balanço em {dias} pregão(ões) e a "
                        f"sessão de reação ainda por vir -- a técnica sozinha "
                        f"não sustenta entrada antes do número. O gate pede "
                        f"MANTER com EARNINGS_PROXIMO declarado.", ticker=tk)

        # Confiança alta com evento binário na janela. MANTER a 95% na
        # véspera afirma uma certeza que o balanço de amanhã pode desmontar
        # em um gap -- o MRVL do dia saiu honesto (45%), mas nada impedia o
        # contrário. Só ações que ATRAVESSAM o evento expostas; VENDER 95%
        # por stop acionado é o plano falando, não excesso de fé.
        if acao not in ACOES_DE_SAIDA and tk in earnings:
            try:
                dias_conf = (_parse_date(earnings[tk]) - as_of).days
            except Exception:
                dias_conf = None
            conf = item.get("confidence")
            if dias_conf is not None and 0 <= dias_conf <= EARNINGS_PROXIMO_DIAS \
                    and isinstance(conf, (int, float)) \
                    and conf > CONFIANCA_MAXIMA_NA_VESPERA:
                rep.add("WARN", "BLOCO_CONFIANCA_NA_VESPERA",
                        f"{acao} com confiança {conf:.0%} e balanço em "
                        f"{dias_conf} pregão(ões) -- o evento ainda não "
                        f"aconteceu, e confiança acima de "
                        f"{CONFIANCA_MAXIMA_NA_VESPERA:.0%} na véspera afirma "
                        f"o que ninguém sabe.", ticker=tk)

    # ── o bloco contra o Plano de Saída ─────────────────────────────────────
    #
    # Visto em producao (26/08/2026): o bloco saiu com ARM e INTC em MANTER
    # enquanto o painel Plano de Saida dizia, para os dois, "Vender
    # imediatamente -- stop-loss acionado", vencido havia 6 dias. Nenhum dos
    # dois declarou PLANO_DE_SAIDA nos reason_codes. E SKHY, que NAO tinha
    # item no plano, declarou.
    #
    # O plano e' decisao ja' tomada. O veredito pode contraria-la -- o mercado
    # muda, e a checagem nao proibe isso. O que ela proibe e' contrariar em
    # SILENCIO: quem le a tabela ve MANTER e nao fica sabendo que existe uma
    # ordem de venda vencida na mesma tela, tres paineis abaixo.
    #
    # Por isso a saida e' declarar, nao obedecer: com PLANO_DE_SAIDA nos
    # reason_codes o item passa, porque a divergencia virou consciente. E' a
    # mesma isencao que RISCO_CORRELACAO da' a' compra do par correlacionado.
    plano = snapshot.get("plano_de_saida")
    plano = plano if isinstance(plano, dict) else {}
    # A leitura do plano falhou: a checagem abaixo NAO pode rodar, e o leitor
    # tem que saber disso. Um validador que emudece quando a fonte cai e' pior
    # que um que nao existe -- da' a impressao de ter conferido.
    if plano.pop("_leitura_falhou", False):
        rep.add("WARN", "PLANO_NAO_CONFERIDO",
                "não foi possível ler o Plano de Saída nesta geração, então "
                "as decisões do bloco NÃO foram conferidas contra ele.")
        plano = {}
    for item in bloco.get("tickers", []):
        if not isinstance(item, dict):
            continue
        tk = str(item.get("ticker") or "").upper()
        acao = str(item.get("action") or "").upper()
        codes = [str(c).upper() for c in (item.get("reason_codes") or [])]
        itens_do_ticker = plano.get(tk) or []
        manda_vender = [i for i in itens_do_ticker
                        if _ORDEM_DE_VENDA.search(str(i.get("acao") or ""))]
        if manda_vender and acao not in ACOES_DE_SAIDA \
                and "PLANO_DE_SAIDA" not in codes:
            qual = str(manda_vender[0].get("acao") or "")[:60]
            rep.add("ERROR", "BLOCO_CONTRA_PLANO",
                    f"decide {acao}, mas o Plano de Saída manda \"{qual}\" "
                    f"(alvo {manda_vender[0].get('data_alvo') or '?'}) — "
                    f"contrariar o plano é permitido, contrariar em silêncio "
                    f"não: declare PLANO_DE_SAIDA.", ticker=tk)
        # O inverso: declarar o que nao existe. SKHY citou PLANO_DE_SAIDA sem
        # ter item pendente -- razao sem fato por tras, mesma regra do capex.
        if "PLANO_DE_SAIDA" in codes and plano and not itens_do_ticker:
            rep.add("WARN", "BLOCO_PLANO_SEM_ITEM",
                    "declara PLANO_DE_SAIDA, mas não há item pendente para "
                    "este ticker no plano do dia.", ticker=tk)

    # Completude: todo ticker da carteira precisa de um veredito -- omissão
    # silenciosa é como o LLM "resolve" o ticker difícil.
    for tk in universo:
        if tk not in vistos:
            rep.add("ERROR", "BLOCO_TICKER_FALTANDO",
                    "sem entrada no bloco estruturado.", ticker=tk)

    # Concentração sobre a DECLARAÇÃO (não sobre regex de prosa). Comprar o
    # par correlacionado declarando RISCO_CORRELACAO é decisão consciente --
    # mesma lógica da isenção que o lint dá ao texto que nomeia o risco.
    compras_sem_risco_declarado = []
    for item in bloco.get("tickers", []):
        if not isinstance(item, dict):
            continue
        if str(item.get("action", "")).upper() not in ACOES_DE_COMPRA:
            continue
        codes_item = [str(c).upper() for c in (item.get("reason_codes") or [])]
        if "RISCO_CORRELACAO" not in codes_item:
            compras_sem_risco_declarado.append(str(item.get("ticker", "")).upper())
    for erro in checar_concentracao_veredito(compras_sem_risco_declarado):
        rep.add("ERROR", "BLOCO_CONCENTRACAO", erro)

    return rep


def checar_bloco_vs_texto(bloco: dict, texto: str,
                          universo: list[str]) -> ValidationReport:
    """O texto e o bloco têm que contar a MESMA história. O regex de
    intenção deixa de ser a fonte da verdade e vira CONTRAPROVA: prosa
    dizendo comprar o que o bloco não manda comprar (ou o inverso) é a
    divergência exata que a validação por regex sozinha não resolvia."""
    rep = ValidationReport()
    norm_text = _norm(texto)
    acoes = {str(i.get("ticker", "")).upper(): str(i.get("action", "")).upper()
             for i in bloco.get("tickers", []) if isinstance(i, dict)}

    compras_no_texto = _tickers_com_intencao_de_compra(texto, universo)
    for tk in compras_no_texto:
        if acoes.get(tk) and acoes[tk] not in ACOES_DE_COMPRA:
            rep.add("ERROR", "JSON_TEXTO_DIVERGEM",
                    f"o texto recomenda comprar/aumentar, mas o bloco diz "
                    f"{acoes[tk]}. Os dois têm que contar a mesma história.",
                    ticker=tk)

    for tk, acao in acoes.items():
        if acao in ACOES_DE_COMPRA and tk.lower() not in norm_text:
            rep.add("ERROR", "COMPRA_SEM_EXPLICACAO",
                    f"o bloco manda {acao}, mas o ticker não aparece no "
                    f"texto -- decisão sem explicação.", ticker=tk)

    return rep


def validar_veredito_completo(texto: str, snapshot: dict[str, Any],
                              year: int | None = None) -> ValidationReport:
    """O ponto de entrada do run_veredito desde 20/08/2026: lint da prosa +
    bloco estruturado + coerência entre os dois, num relatório só (o retry
    de correção recebe tudo junto). Bloco AUSENTE é ERROR: sem ele, a tela
    e as regras determinísticas voltam a depender de interpretação de
    prosa, que é o que esta etapa aposenta."""
    # O lint e o cruzamento rodam sobre a PROSA (texto sem os blocos json):
    # o bloco é estrutura, não prosa -- e o regex de intenção casa "compra"
    # dentro de "RSI_SOBRECOMPRADO" do próprio bloco (pego por teste antes
    # de ir a produção), o que acusaria divergência do veredito consigo
    # mesmo.
    texto_prosa = _BLOCO_JSON_RE.sub("", texto)
    rep = lint_veredito(texto_prosa, snapshot, year=year)
    bloco, erro = extrair_bloco_estruturado(texto)
    if bloco is None:
        rep.add("ERROR", "BLOCO_AUSENTE",
                (erro or "faltou o bloco ```json``` final com a decisão por "
                         "ticker (tickers/action/confidence/reason_codes)."))
        return rep
    sub = validar_bloco_estruturado(bloco, snapshot)
    rep.issues.extend(sub.issues)
    cruz = checar_bloco_vs_texto(bloco, texto_prosa, list(snapshot.get("quotes", {})))
    rep.issues.extend(cruz.issues)
    return rep


if __name__ == "__main__":
    snap = {
        "as_of": "2026-07-31",
        "quotes": {
            "AVGO": {"price": 389.28, "previous_close": 387.84, "open": 394.83,
                      "high": 399.92, "low": 379.71, "change_percent": -0.36,
                      "as_of": "2026-07-31"},
            "SKHY": {"price": 143.73, "previous_close": 149.00, "open": 159.875,
                      "high": 162.65, "low": 143.51, "change_percent": -6.61,
                      "as_of": "2026-07-31"},
            "SMCI": {"price": 28.40, "previous_close": 27.73, "open": 28.65,
                      "high": 29.30, "low": 27.21, "change_percent": 2.4162,
                      "as_of": "2026-07-31"},
            "ARM": {"price": 239.69, "previous_close": 241.54, "open": 258.95,
                     "high": 261.905, "low": 239.26, "change_percent": -0.7659,
                     "as_of": "2026-07-31"},
        },
        "technicals": {
            "ARM": {"rsi": 31.55, "rsi_date": "2026-07-29"},
            "SMCI": {"rsi": 48.91, "rsi_date": "2026-07-31"},
        },
        "earnings": {"SMCI": "2026-08-10", "NVDA": "2026-08-25",
                     "MRVL": "2026-08-26", "AVGO": "2026-09-02"},
    }

    print("== validate_snapshot ==")
    rep = validate_snapshot(snap)
    print(rep.summary())
    print()
    print("== lint_veredito ==")
    texto = ("SMCI caiu 9,95% em 29/jul apos divulgacao de earnings. "
             "AVGO fecha em $389,28, caiu -0,36% hoje. "
             "06/ago (sexta-feira): executar saidas. "
             "SMCI earnings iminente em 11/ago traz risco.")
    print(lint_veredito(texto, snap).summary())
