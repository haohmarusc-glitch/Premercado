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
from typing import Any

# ---------------------------------------------------------------- config ---

PCT_TOLERANCE_PP = 0.10  # tolerância em pontos percentuais p/ recomputo
STALE_TECHNICAL_DAYS = 0  # RSI deve ser do MESMO dia do quote (0 dias de gap)
FADE_FROM_HIGH_PCT = 5.0  # fechou X% ou mais abaixo do high do dia -> alerta
GAP_UP_PCT = 2.0  # abertura X% acima do fech. anterior conta como gap
MENTION_PCT_TOLERANCE_PP = 0.30  # tolerância p/ percentuais citados no texto
FLAT_CLAIM_TOLERANCE_PP = 1.5  # |variação real| acima disso invalida um claim de "flat"

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
