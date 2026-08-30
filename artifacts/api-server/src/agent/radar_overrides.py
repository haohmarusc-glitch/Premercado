"""
dados/radar_overrides.json — leitor único.

Este JSON guarda o que NÃO tem API: EVR e move implícito vêm de coleta humana
no OptionSlam (alguém abre o site e transcreve). A auditoria de 17/08/2026
mostrou o custo de deixar esse tipo de número embutido no .py, indistinguível
do dado vivo: ele envelhece em silêncio e segue sendo servido como se fosse de
hoje. Por isso `coletado_em` é obrigatório e a idade acompanha todo consumidor.

Por que módulo próprio: dois consumidores leem o mesmo arquivo -- o
radar_ia_2026 (relatório e blob de /radar) e o earnings_window (fallback do
move implícito quando a cadeia de opções não responde). Duas cópias da leitura
divergiriam na primeira mudança de formato, e "duas implementações da mesma
coisa" é exatamente o padrão de bug que o playbook §2b registra.

Falha aberta em toda a superfície: sem o arquivo, sem a chave ou com JSON
quebrado, o consumidor segue funcionando sem EVR/move implícito. Relatório
parcial vale mais que relatório nenhum -- e o aviso vai para o stderr, não
para o stdout (que é do JSON final).
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date

from agent.brt import today_brt

CAMINHO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "dados", "radar_overrides.json")


def carregar() -> tuple[dict, str | None, str | None]:
    """(reacao_earnings, coletado_em, fonte)."""
    try:
        with open(CAMINHO, "r", encoding="utf-8") as f:
            blob = json.load(f)
        return (blob.get("reacao_earnings") or {},
                blob.get("coletado_em"), blob.get("fonte"))
    except Exception as e:  # noqa: BLE001 — ver "falha aberta" no topo
        print(f"[radar_overrides] indisponíveis ({e}); seguindo sem EVR/move implícito",
              file=sys.stderr, flush=True)
        return {}, None, None


def idade_dias(coletado_em: str | None, ref: date | None = None) -> int | None:
    """Há quantos dias a coleta manual foi feita. None sem carimbo legível.

    `ref` injetável para o teste não depender do relógio -- e today_brt em vez
    de date.today() porque perto da meia-noite BRT o dia do processo (UTC nos
    containers) já virou e a idade sairia 1 dia adiantada.
    """
    if not coletado_em:
        return None
    try:
        return ((ref or today_brt()) - date.fromisoformat(coletado_em)).days
    except ValueError:
        return None
