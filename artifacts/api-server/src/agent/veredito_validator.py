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
from datetime import date, datetime
from itertools import combinations
from typing import Any

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

    return rep


# --------------------------------------- fase 2: lint do texto gerado ---

_DATE_PT = re.compile(r"(\d{1,2})\s*/?\s*(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)",
                       re.IGNORECASE)
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
    norm_text = _norm(texto)

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

    # 2) datas de earnings citadas batem com o painel?
    for tk, edate in earnings.items():
        ed = _parse_date(edate)
        # procura mencoes tipo "earnings ... 11/ago" perto do ticker
        for m in re.finditer(rf"{tk.lower()}[^.\n]{{0,120}}", norm_text):
            seg = m.group(0)
            if "earnings" not in seg and "resultado" not in seg:
                continue
            dm = _DATE_PT.search(seg)
            if dm:
                day, mon = int(dm.group(1)), MONTHS_PT[dm.group(2)[:3]]
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
    technicals: dict = snapshot.get("technicals", {})
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
