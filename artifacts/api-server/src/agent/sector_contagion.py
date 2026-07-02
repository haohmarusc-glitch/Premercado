"""
sector_contagion.py
-------------------
Detecção de contágio setorial para o agente de monitoramento.

Ideia central:
    - Os tickers são agrupados por CAMADA da cadeia de IA (memória, interconexão,
      energia, fundição). Nomes da mesma camada tendem a se mover em simpatia.
    - Quando um ticker "dispara" (movimento forte de preço e/ou volume), checamos
      os vizinhos do mesmo grupo:
          * se já estão se movendo junto  -> CONFIRMAÇÃO (o tema está ativo)
          * se ainda estão parados        -> CATCH-UP (candidato a seguir o líder)

O módulo emite alertas como dicts estruturados, pra você plugar no formato que o
seu alerts.py já usa (ex.: append numa lista de alertas, mandar pro Claude, salvar
no Supabase, etc.).

Depende de: yfinance
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1) Grupos por camada — edite à vontade conforme sua watchlist evolui
# ---------------------------------------------------------------------------
SECTOR_GROUPS: dict[str, dict] = {
    "memory_storage": {
        "label": "Memória/Armazenamento",
        "tickers": ["MU", "SNDK", "WDC"],
    },
    "interconnect": {
        "label": "Interconexão/Servidores",
        "tickers": ["SMCI", "ALAB", "CRDO", "ANET"],
    },
    "power_cooling": {
        "label": "Energia/Refrigeração",
        "tickers": ["VRT"],
    },
    "foundry_equipment": {
        "label": "Fundição/Equipamentos",
        "tickers": ["TSM", "ASML"],
    },
    "saude_us": {
        "label": "Saúde EUA (farma/managed care)",
        "tickers": ["LLY", "JNJ", "ABBV", "MRK", "PFE", "UNH"],
    },
    "saude_b3": {
        "label": "Saúde B3",
        "tickers": ["RADL3.SA", "HAPV3.SA", "RDOR3.SA", "FLRY3.SA", "ONCO3.SA"],
    },
}

# ---------------------------------------------------------------------------
# 2) Limiares — ajuste pela volatilidade que você quer capturar
# ---------------------------------------------------------------------------
TRIGGER_MOVE_PCT = 4.0      # movimento (em %) que caracteriza um "disparo"
SYMPATHY_MOVE_PCT = 1.5     # acima disto, o vizinho está "acompanhando"
VOLUME_SPIKE_RATIO = 1.8    # volume vs. média de 20 dias que conta como spike


# ---------------------------------------------------------------------------
# Estruturas de dados
# ---------------------------------------------------------------------------
@dataclass
class TickerSnapshot:
    ticker: str
    change_pct: Optional[float] = None       # variação % no período
    volume_ratio: Optional[float] = None     # volume / média 20d
    last_price: Optional[float] = None
    error: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.error is None and self.change_pct is not None


@dataclass
class ContagionAlert:
    group_key: str
    group_label: str
    leader: str                              # quem disparou
    leader_change_pct: float
    confirming: list[str] = field(default_factory=list)   # vizinhos acompanhando
    catch_up: list[str] = field(default_factory=list)     # vizinhos atrasados
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return {
            "type": "sector_contagion",
            "group": self.group_key,
            "group_label": self.group_label,
            "leader": self.leader,
            "leader_change_pct": round(self.leader_change_pct, 2),
            "confirming": self.confirming,
            "catch_up": self.catch_up,
            "timestamp": self.timestamp,
        }

    def to_message(self) -> str:
        partes = [
            f"[CONTÁGIO • {self.group_label}] "
            f"{self.leader} disparou {self.leader_change_pct:+.2f}%."
        ]
        if self.confirming:
            partes.append(f"Acompanhando: {', '.join(self.confirming)}.")
        if self.catch_up:
            partes.append(f"Atrasados (possível catch-up): {', '.join(self.catch_up)}.")
        return " ".join(partes)


# ---------------------------------------------------------------------------
# Coleta de dados
# ---------------------------------------------------------------------------
def fetch_snapshot(ticker: str, period: str = "5d", interval: str = "1d") -> TickerSnapshot:
    """
    Busca a variação % e o ratio de volume de um ticker.

    period="5d"/interval="1d"  -> contágio dia a dia (default)
    period="1d"/interval="5m"  -> contágio intradiário (pré-market/ao vivo)
    """
    try:
        hist = yf.Ticker(ticker).history(period=period, interval=interval)
        if hist.empty or len(hist) < 2:
            return TickerSnapshot(ticker=ticker, error="sem dados suficientes")

        last_close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2])
        change_pct = (last_close - prev_close) / prev_close * 100.0

        # ratio de volume vs. média do período disponível (proxy de spike)
        avg_vol = float(hist["Volume"].iloc[:-1].mean()) or 1.0
        volume_ratio = float(hist["Volume"].iloc[-1]) / avg_vol

        return TickerSnapshot(
            ticker=ticker,
            change_pct=change_pct,
            volume_ratio=volume_ratio,
            last_price=last_close,
        )
    except Exception as exc:  # rede, ticker inválido, etc.
        logger.warning("Falha ao buscar %s: %s", ticker, exc)
        return TickerSnapshot(ticker=ticker, error=str(exc))


# ---------------------------------------------------------------------------
# Lógica de contágio
# ---------------------------------------------------------------------------
def detect_contagion(
    period: str = "5d",
    interval: str = "1d",
    trigger_pct: float = TRIGGER_MOVE_PCT,
    sympathy_pct: float = SYMPATHY_MOVE_PCT,
) -> list[ContagionAlert]:
    """
    Varre todos os grupos e retorna uma lista de ContagionAlert.

    Para cada grupo:
      1. coleta snapshot de cada ticker
      2. acha o líder (maior movimento em módulo) que cruzou trigger_pct
      3. classifica os vizinhos em "confirming" (já se movendo) ou
         "catch_up" (ainda parados)
    """
    alerts: list[ContagionAlert] = []

    for group_key, cfg in SECTOR_GROUPS.items():
        snapshots = {t: fetch_snapshot(t, period, interval) for t in cfg["tickers"]}
        valid = {t: s for t, s in snapshots.items() if s.is_valid}
        if len(valid) < 2:
            # grupo com 1 ticker (ex.: power_cooling) não tem "vizinho" pra contágio
            continue

        # líder = maior movimento absoluto que cruzou o limiar de disparo
        leader, lead_snap = max(
            valid.items(), key=lambda kv: abs(kv[1].change_pct)
        )
        if abs(lead_snap.change_pct) < trigger_pct:
            continue  # ninguém disparou neste grupo

        confirming, catch_up = [], []
        lead_dir = 1 if lead_snap.change_pct > 0 else -1

        for t, snap in valid.items():
            if t == leader:
                continue
            same_dir = (snap.change_pct >= 0) == (lead_dir > 0)
            if same_dir and abs(snap.change_pct) >= sympathy_pct:
                confirming.append(f"{t} ({snap.change_pct:+.1f}%)")
            else:
                catch_up.append(f"{t} ({snap.change_pct:+.1f}%)")

        alerts.append(
            ContagionAlert(
                group_key=group_key,
                group_label=cfg["label"],
                leader=leader,
                leader_change_pct=lead_snap.change_pct,
                confirming=confirming,
                catch_up=catch_up,
            )
        )

    return alerts


# ---------------------------------------------------------------------------
# Uso direto / integração
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    # Modo dia a dia (default). Para pré-market/intradiário:
    #   detect_contagion(period="1d", interval="5m")
    found = detect_contagion()

    if not found:
        print("Nenhum contágio setorial detectado.")
    for alert in found:
        print(alert.to_message())
        # Para plugar no seu alerts.py / Supabase:
        # salvar_alerta(alert.to_dict())
