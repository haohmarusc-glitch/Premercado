"""
Sinais macro de risco setorial (IA/Semicondutores).

Origem: a análise do sell-off de 28-29/07/2026 (repique em 30/07), somada ao
episódio distinto de 18/08/2026. Os dois viraram golden datasets em
test_macro_risk.py -- ver lá o racional de cada threshold.

Não é um sinal de compra ou venda. É um MODULADOR: entra no confluence_engine
reduzindo o tamanho sugerido de posição quando o pano de fundo está ruim. Um
sinal macro não sabe nada sobre o ticker específico; o que ele sabe é quando o
mercado inteiro está em condição de amplificar qualquer gatilho.

## "Sem dado" é um estado, não é "sem risco"

A versão original devolvia `active=False` tanto para "medi e está calmo" quanto
para "não consegui medir". Rodando os dois cenários:

    mercado calmo, tudo medido : score=0  kelly=0.2500
    NADA foi coletado          : score=0  kelly=0.2500

Num módulo de RISCO isso é grave: a falha de coleta vira sinal de segurança, e
o sistema autoriza posição cheia num dia sobre o qual não sabe nada. É a mesma
classe de bug do `null` que virava "volatilidade histórica baixa" na tela de
earnings (ver earnings-reaction-nulo.test.ts) -- mas aqui ela dimensiona
dinheiro.

Por isso cada sinal tem TRÊS estados, e o agregado publica a cobertura junto
com o score. Ausência de dado alarga a cautela; nunca a remove.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

# ── Estados ─────────────────────────────────────────────────────────────────
#
# `nao_aplicavel` não é o mesmo que `sem_dado`, e a diferença importa na conta
# de cobertura: "não houve balanço hoje" é uma resposta COMPLETA sobre o sinal
# de earnings. Tratá-la como buraco de dado penalizaria o Kelly em todo dia
# comum do calendário.
OK = "ok"
SEM_DADO = "sem_dado"
NAO_APLICAVEL = "nao_aplicavel"


@dataclass
class SignalResult:
    flag: str
    active: bool
    status: str = OK
    severity: str = "low"          # low | medium | high
    motivo: str = ""               # por que está sem dado / não se aplica
    details: dict = field(default_factory=dict)

    @property
    def medido(self) -> bool:
        return self.status != SEM_DADO

    def to_dict(self) -> dict:
        return {
            "flag": self.flag,
            # bool() explícito: comparação sobre valor vindo do pandas devolve
            # np.bool_, que não é subclasse de bool. O json_seguro normaliza na
            # serialização, mas o confluence_engine consome este dict em Python
            # -- e ali o np.bool_ não quebra, só vira tipo estranho viajando.
            "active": bool(self.active),
            "status": self.status,
            "severity": self.severity,
            "motivo": self.motivo,
            **self.details,
        }


def _sem_dado(flag: str, motivo: str) -> SignalResult:
    """Ativo NUNCA é True aqui: um sinal que não foi medido não pode disparar
    alarme falso. O peso dele vai para a cobertura, não para o score."""
    return SignalResult(flag=flag, active=False, status=SEM_DADO, motivo=motivo)


# ── 1. RATE_SHOCK ───────────────────────────────────────────────────────────

def check_rate_shock(
    yield_30y_today: float | None,
    yield_30y_prev: float | None,
    near_fomc_window: bool = False,
) -> SignalResult:
    """Choque no yield de 30 anos. Fonte: FRED DGS30 (a chave já existe, ver
    get_macro_indicators em tools.py).

    Calibrado em 29/07/2026: o 30Y subiu ~7bps cruzando 5,2%, perto das máximas
    de 2007, depois de o Fed manter os juros com PCE a 3,7%. Juros longos
    subindo são veneno direto para growth, cujo valor está em fluxo de caixa
    distante.

    O piso absoluto de 5,0% é DEPENDENTE DE REGIME e sobreviveu por ser o que o
    episódio mostrou -- num ciclo de juros baixos ele nunca dispara. Se o
    regime virar, é a primeira linha a revisar."""
    if yield_30y_today is None or yield_30y_prev is None:
        return _sem_dado("RATE_SHOCK", "yield de 30 anos indisponível (FRED DGS30)")

    delta_bps = (yield_30y_today - yield_30y_prev) * 100
    active = delta_bps >= 6 and yield_30y_today > 5.0

    severity = "low"
    if active:
        severity = "high" if (delta_bps >= 10 or near_fomc_window) else "medium"

    return SignalResult(
        flag="RATE_SHOCK", active=active, severity=severity,
        details={
            "delta_bps": round(delta_bps, 2),
            "yield_30y_today": yield_30y_today,
            "near_fomc_window": near_fomc_window,
        },
    )


# ── 2. ASIA_MEMORY_CONTAGION ────────────────────────────────────────────────

TICKERS_AFETADOS = ["NVDA", "MRVL", "SKHY", "MU", "AMD"]


def check_asia_contagion(
    sk_hynix_pct: float | None,
    samsung_pct: float | None,
    kospi_pct: float | None,
) -> SignalResult:
    """O sinal de maior valor prático em pré-mercado: Coreia fecha ~6-8h antes
    da abertura dos EUA, então é leading indicator de verdade, não coincidente.

    Calibrado em 28-29/07/2026: SK Hynix -14,65%, Samsung -13%, Kospi -8% com
    circuit breaker em dois dias seguidos.

    Avalia com o que houver: se o Kospi veio e as ações não, a leitura do índice
    ainda vale. Só vira sem_dado quando NENHUMA das três chegou -- exigir as
    três apagaria um sinal bom por falta de uma fonte secundária."""
    presentes = {k: v for k, v in {
        "sk_hynix_pct": sk_hynix_pct,
        "samsung_pct": samsung_pct,
        "kospi_pct": kospi_pct,
    }.items() if v is not None}

    if not presentes:
        return _sem_dado("ASIA_MEMORY_CONTAGION", "fechamento asiático indisponível")

    acoes = [v for k, v in presentes.items() if k != "kospi_pct"]
    active = any(v <= -8 for v in acoes) or (
        kospi_pct is not None and kospi_pct <= -6
    )

    severity = "low"
    if active:
        severity = "high" if any(v <= -12 for v in acoes) else "medium"

    return SignalResult(
        flag="ASIA_MEMORY_CONTAGION", active=active, severity=severity,
        motivo="" if len(presentes) == 3 else f"parcial: {', '.join(sorted(presentes))}",
        details={**presentes, "tickers_afetados": TICKERS_AFETADOS if active else []},
    )


# ── 3. PRICED_FOR_PERFECTION ────────────────────────────────────────────────

def check_priced_for_perfection(
    eps_surprise_pct: float | None,
    revenue_surprise_pct: float | None,
    premarket_reaction_pct: float | None,
) -> SignalResult:
    """"Bateu e caiu": resultado numericamente bom com a ação despencando é
    sinal de que a expectativa estava acima do fundamento.

    Calibrado na SK Hynix (29/07/2026): receita recorde +257% a/a, lucro
    operacional +557%, e o setor desabou porque os números ficaram abaixo do
    consenso. Quando o maior fornecedor de HBM entrega +557% e frustra, o
    problema não é fundamento -- é precificação.

    Sem balanço no dia, o sinal é NAO_APLICAVEL, não sem_dado: "não houve
    earnings" é resposta completa. Tratar como buraco puniria o Kelly em todo
    dia comum do calendário."""
    faltando = [
        nome for nome, v in (
            ("eps", eps_surprise_pct),
            ("receita", revenue_surprise_pct),
            ("reação", premarket_reaction_pct),
        ) if v is None
    ]
    if len(faltando) == 3:
        return SignalResult(
            flag="PRICED_FOR_PERFECTION", active=False, status=NAO_APLICAVEL,
            motivo="nenhum balanço na janela",
        )
    if faltando:
        return _sem_dado(
            "PRICED_FOR_PERFECTION",
            f"balanço na janela mas faltou: {', '.join(faltando)}",
        )

    bateu = eps_surprise_pct > 0 and revenue_surprise_pct > 0
    active = bateu and premarket_reaction_pct <= -5

    return SignalResult(
        flag="PRICED_FOR_PERFECTION", active=active,
        severity=("high" if premarket_reaction_pct <= -10 else "medium") if active else "low",
        details={
            "eps_surprise_pct": eps_surprise_pct,
            "revenue_surprise_pct": revenue_surprise_pct,
            "premarket_reaction_pct": premarket_reaction_pct,
        },
    )


# ── 4. CHINA_COMPETITION_RISK ───────────────────────────────────────────────

PALAVRAS_CHINA = [
    "china chip", "cxmt", "duv", "lithography", "litografia",
    "export restriction", "chip equipment",
]


def check_china_risk(
    manchetes: list[dict] | None,
    palavras: list[str] | None = None,
    limiar_negativo: float = -0.15,
    minimo_negativas: int = 3,
) -> SignalResult:
    """Concorrência chinesa atacando a tese de ESCASSEZ que sustenta os
    múltiplos do setor. Semana de 26-29/07/2026: IPO da CXMT em Xangai,
    reportagem de equipamento de fabricação em produção em massa por estatal, e
    desenvolvimento de litografia DUV -- a ASML caiu ~6%.

    O mais ruidoso dos seis, porque depende de manchete. `manchetes=None`
    (busca falhou) é sem_dado; lista vazia é medição legítima de "nada saiu"."""
    if manchetes is None:
        return _sem_dado("CHINA_COMPETITION_RISK", "feed de notícias indisponível")

    palavras = palavras or PALAVRAS_CHINA
    relevantes = [
        n for n in manchetes
        if any(p in str(n.get("title") or "").lower() for p in palavras)
    ]
    negativas = sum(
        1 for n in relevantes
        if (n.get("overall_sentiment_score") or 0) < limiar_negativo
    )
    active = negativas >= minimo_negativas

    return SignalResult(
        flag="CHINA_COMPETITION_RISK", active=active,
        severity="medium" if active else "low",
        details={"mencoes": len(relevantes), "negativas": negativas},
    )


# ── 5. OVEREXTENDED_SECTOR ──────────────────────────────────────────────────

MULTIPLICADOR_ESTICADO = 1.5


def check_overextended(
    sox_precos: list[float] | None,
    semanas: int = 9,
    pregoes_por_semana: int = 5,
) -> SignalResult:
    """Quanto o setor subiu na janela. Não é gatilho -- é AMPLIFICADOR: setor
    esticado mais qualquer arranhão vira avalanche, porque todo mundo está no
    mesmo trade e alavancado.

    Calibrado no rally de abr-mai/2026: o SOX subiu ~71% em 9 semanas, ritmo
    superado uma única vez na história -- 10/03/2000, o pico da bolha.

    Série curta é SEM_DADO, não "não esticado". A versão original devolvia
    inativo com uma nota, e nota não chega ao Kelly: um histórico truncado
    virava permissão para posição cheia."""
    if not sox_precos:
        return _sem_dado("OVEREXTENDED_SECTOR", "série do SOX indisponível")

    janela = semanas * pregoes_por_semana
    if len(sox_precos) <= janela:
        return _sem_dado(
            "OVEREXTENDED_SECTOR",
            f"série do SOX com {len(sox_precos)} pregões, precisa de {janela + 1}",
        )

    base = sox_precos[-(janela + 1)]
    if not base:
        return _sem_dado("OVEREXTENDED_SECTOR", "preço-base do SOX inválido")

    retorno_pct = (sox_precos[-1] / base - 1) * 100
    active = retorno_pct > 60

    return SignalResult(
        flag="OVEREXTENDED_SECTOR", active=active,
        severity="high" if retorno_pct > 70 else ("medium" if active else "low"),
        details={
            "retorno_pct": round(retorno_pct, 2),
            "semanas": semanas,
            "multiplicador": MULTIPLICADOR_ESTICADO if active else 1.0,
        },
    )


# ── 6. GEOPOLITICAL_OIL_SHOCK ───────────────────────────────────────────────

def check_geopolitical_oil_shock(
    wti_hoje: float | None,
    wti_anterior: float | None,
    yield_10y_hoje: float | None,
    yield_10y_anterior: float | None,
) -> SignalResult:
    """Petróleo e yield subindo JUNTOS: choque de juros vindo de fora
    (geopolítica, oferta), diferente do RATE_SHOCK puro, que pode vir de
    inflação doméstica ou do FOMC. O RATE_SHOCK sozinho não distinguia a causa,
    e as duas pedem reações diferentes.

    Calibrado em 18/08/2026: WTI a US$ 84 (Brent US$ 91) com tensão EUA-Irã no
    Estreito de Hormuz, 10Y a 4,72% e 30Y a 5,31%, máxima em 19 anos.

    Fonte: FRED DCOILWTICO + DGS10."""
    faltando = [
        nome for nome, v in (
            ("WTI hoje", wti_hoje), ("WTI anterior", wti_anterior),
            ("10Y hoje", yield_10y_hoje), ("10Y anterior", yield_10y_anterior),
        ) if v is None
    ]
    if faltando:
        return _sem_dado("GEOPOLITICAL_OIL_SHOCK", f"indisponível: {', '.join(faltando)}")
    if not wti_anterior:
        return _sem_dado("GEOPOLITICAL_OIL_SHOCK", "preço anterior do WTI inválido")

    oleo_delta_pct = (wti_hoje / wti_anterior - 1) * 100
    yield_delta_bps = (yield_10y_hoje - yield_10y_anterior) * 100
    active = oleo_delta_pct >= 3 and yield_delta_bps >= 3

    severity = "low"
    if active:
        severity = "high" if (oleo_delta_pct >= 6 or yield_delta_bps >= 8) else "medium"

    return SignalResult(
        flag="GEOPOLITICAL_OIL_SHOCK", active=active, severity=severity,
        details={
            "oleo_delta_pct": round(oleo_delta_pct, 2),
            "wti_hoje": wti_hoje,
            "yield_10y_hoje": yield_10y_hoje,
            "yield_delta_bps": round(yield_delta_bps, 2),
        },
    )


# ── Agregador ───────────────────────────────────────────────────────────────

PESOS = {
    "RATE_SHOCK": 15,
    "ASIA_MEMORY_CONTAGION": 25,
    "PRICED_FOR_PERFECTION": 10,
    "CHINA_COMPETITION_RISK": 10,
    "OVEREXTENDED_SECTOR": 20,
    "GEOPOLITICAL_OIL_SHOCK": 20,
}

_MULT_SEVERIDADE = {"low": 0.5, "medium": 0.8, "high": 1.0}

# Abaixo disto não há score, só flags e cobertura.
#
# Um limiar de "cobertura > 0" não serve: PRICED_FOR_PERFECTION vira
# NAO_APLICAVEL em todo dia sem balanço, o que já garante 10% sozinho. Com esse
# guarda frouxo, um dia com TUDO cego menos o calendário devolvia score 0 -- e
# 0 lê-se como "sem risco", exatamente o que este módulo existe para não fazer.
#
# 50% é juízo: acima dele os dois sinais de maior peso (contágio asiático, 25, e
# a dupla setor/petróleo, 40) não cabem os dois de fora ao mesmo tempo. Não é
# calibração -- não há amostra para calibrar isso, só dois episódios.
COBERTURA_MINIMA_PCT = 50


class MacroRiskModule:
    """Roda os seis checks e agrega. O score sozinho não basta: ele vem sempre
    acompanhado de `cobertura_pct`, porque 0/100 com tudo medido e 0/100 com
    tudo cego são a mesma nota sobre situações opostas."""

    PESOS = PESOS

    def evaluate(
        self,
        *,
        yield_30y_today: float | None = None,
        yield_30y_prev: float | None = None,
        near_fomc_window: bool = False,
        sk_hynix_pct: float | None = None,
        samsung_pct: float | None = None,
        kospi_pct: float | None = None,
        eps_surprise_pct: float | None = None,
        revenue_surprise_pct: float | None = None,
        premarket_reaction_pct: float | None = None,
        manchetes: list[dict] | None = None,
        sox_precos: list[float] | None = None,
        wti_hoje: float | None = None,
        wti_anterior: float | None = None,
        yield_10y_hoje: float | None = None,
        yield_10y_anterior: float | None = None,
    ) -> dict:
        sinais: dict[str, SignalResult] = {
            "RATE_SHOCK": check_rate_shock(
                yield_30y_today, yield_30y_prev, near_fomc_window),
            "ASIA_MEMORY_CONTAGION": check_asia_contagion(
                sk_hynix_pct, samsung_pct, kospi_pct),
            "PRICED_FOR_PERFECTION": check_priced_for_perfection(
                eps_surprise_pct, revenue_surprise_pct, premarket_reaction_pct),
            "CHINA_COMPETITION_RISK": check_china_risk(manchetes),
            "OVEREXTENDED_SECTOR": check_overextended(sox_precos),
            "GEOPOLITICAL_OIL_SHOCK": check_geopolitical_oil_shock(
                wti_hoje, wti_anterior, yield_10y_hoje, yield_10y_anterior),
        }

        cobertura = sum(PESOS[n] for n, s in sinais.items() if s.medido)
        saida: dict = {n: s.to_dict() for n, s in sinais.items()}
        saida["cobertura_pct"] = cobertura
        # Mesmo nome usado no Radar (ver openapi.yaml): fonte externa que falhou
        # nunca some em silêncio -- ou erro legível, ou degradação anunciada.
        saida["fontesDegradadas"] = {
            n: s.motivo for n, s in sinais.items() if s.status == SEM_DADO
        }
        # None, não 0: score construído sobre meia dúzia de buracos não é
        # score. Devolver None obriga quem consome a tratar o caso, em vez de
        # somar um zero falso que se parece com "medi e está tudo bem".
        saida["aggregate_score"] = (
            self._agregar(sinais) if cobertura >= COBERTURA_MINIMA_PCT else None
        )
        saida["evaluated_at"] = datetime.now(timezone.utc).isoformat()
        return saida

    def _agregar(self, sinais: dict[str, SignalResult]) -> int:
        score = sum(
            PESOS.get(n, 0) * _MULT_SEVERIDADE.get(s.severity, 0.5)
            for n, s in sinais.items() if s.active
        )
        return round(min(score, 100))


# ── Modulador de Kelly ──────────────────────────────────────────────────────

REDUCAO_POR_FLAG = 0.85
# Sinal não medido conta como MEIO flag ativo. É juízo, não calibração, e a
# escolha está no meio de propósito: tratar como inativo é o bug que este
# módulo corrige (cegueira virando permissão), e tratar como ativo faria uma
# falha de rede no FRED cortar posição como se houvesse choque de juros de
# verdade. Estado desconhecido fica entre os dois porque é onde ele está.
PESO_DA_CEGUEIRA = 0.5


def apply_macro_risk_modifier(kelly_fraction: float, macro_signals: dict) -> float:
    """Aplica o resultado de evaluate() sobre um Kelly já calculado.

    Reduz por flag ativo E por sinal cego. Um dia sem coleta é dia de posição
    menor -- não de posição normal."""
    sinais = {n: macro_signals.get(n) or {} for n in PESOS}
    ativos = [n for n, s in sinais.items() if s.get("active")]
    cegos = [n for n, s in sinais.items() if s.get("status") == SEM_DADO]

    if not ativos and not cegos:
        return kelly_fraction

    reducao = REDUCAO_POR_FLAG ** (len(ativos) + PESO_DA_CEGUEIRA * len(cegos))

    # Setor esticado amplifica os demais: o mesmo gatilho custa mais caro
    # quando todo mundo já está comprado no mesmo trade.
    esticado = sinais.get("OVEREXTENDED_SECTOR") or {}
    if esticado.get("active"):
        reducao **= esticado.get("multiplicador", MULTIPLICADOR_ESTICADO)

    return kelly_fraction * reducao
