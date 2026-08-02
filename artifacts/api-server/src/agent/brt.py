"""
brt.py — data/hora em horário de Brasília, fonte única.

Brasília (America/Sao_Paulo) não observa mais horário de verão desde 2019, então
o offset é fixo UTC-3 (mesma convenção do backend TS, ver lib/timezone.ts).

`datetime.date.today()`/`datetime.datetime.now()` sozinhos usam o fuso do
PROCESSO (UTC nos containers), fazendo o dia virar 3h cedo demais para quem está
em BRT. Use estes helpers em qualquer lugar que informe "hoje"/"agora" ao
usuário ou ao agente, e em qualquer contagem de dias que o usuário vá ler
(dias até earnings, prazo de plano de saída) — perto da meia-noite BRT esses
números saem 1 dia errados com o `today()` cru.

Este módulo existe separado de agent.py porque tools.py também precisa dos
helpers, e tools.py não pode importar agent.py (agent.py importa tools.py —
seria circular). agent.py continua expondo `_now_brt`/`_today_brt_str`/
`_now_brt_str` como fachada para não quebrar quem já os importa de lá.
"""

from __future__ import annotations

import datetime

BRT_OFFSET = datetime.timedelta(hours=3)


def now_brt(now_utc: datetime.datetime | None = None) -> datetime.datetime:
    return (now_utc if now_utc is not None else datetime.datetime.utcnow()) - BRT_OFFSET


def today_brt(now_utc: datetime.datetime | None = None) -> datetime.date:
    """Data de hoje em BRT — substitui `datetime.date.today()`."""
    return now_brt(now_utc).date()


def today_brt_str(now_utc: datetime.datetime | None = None) -> str:
    return now_brt(now_utc).strftime("%d/%m/%Y")


def now_brt_str(now_utc: datetime.datetime | None = None) -> str:
    return now_brt(now_utc).strftime("%H:%M")
