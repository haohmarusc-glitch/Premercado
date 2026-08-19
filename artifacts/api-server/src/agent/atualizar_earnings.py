#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
atualizar_earnings.py — refresh do calendário de earnings do Radar IA a
partir do EARNINGS_CALENDAR da Alpha Vantage.

O dicionário EARNINGS em radar_ia_2026.py era digitado à mão. Era o pedaço
mais PERECÍVEL do snapshot: correlação de 6 meses se move devagar, mas uma
data de earnings vira passado em dias, e empresa remarca. O sintoma disso
estava no próprio dado — quatro tickers carregavam nota de "fontes divergem"
porque a transcrição humana não tinha como decidir entre duas datas.

Mesmo desenho do atualizar_correlacoes.py: o script grava um OVERLAY JSON
que radar_ia_2026.py aplica por cima do embutido no import. Sem tabela no
Postgres (Python não acessa o banco neste repo), e falha em qualquer ponto
mantém o embutido valendo — data velha bem rotulada é melhor que tela sem
calendário.

## Por que a Alpha Vantage aqui, se as correlações fugiram dela

Fugiram do ANALYTICS_FIXED_WINDOW, que é premium e devolvia 403 com chave
free válida. O EARNINGS_CALENDAR não é: responde no plano gratuito, em CSV,
com `reportDate` e `timeOfTheDay`. E não existe alternativa — o yfinance,
que resolveu as correlações, não publica calendário confiável de terceiros.

## UMA chamada para todos os tickers, não uma por ticker

O endpoint aceita `symbol`, mas o universo do radar tem ~45 papéis e a cota
diária compartilhada com o feed de notícias é de 15 chamadas
(AGENT_ALPHAVANTAGE_MAX_DIA). Pedir por ticker esgotaria a cota e derrubaria
as notícias junto — exatamente a troca de "uma falha parcial por duas" que
alpha_vantage_provider.py documenta. Sem `symbol` o endpoint devolve o
calendário inteiro do horizonte num CSV, e filtrar aqui custa zero.

## 200 OK não significa dado

Mesma armadilha do resto da Alpha Vantage: quando a chave é inválida, a cota
estourou ou o endpoint virou premium, a resposta é 200 com um JSON de aviso
em vez do CSV. Aqui isso é detectado (corpo que começa com `{`) e vira erro
explícito, nunca um calendário vazio disfarçado de "nenhuma empresa reporta".

Uso (no VPS, dentro do container):
    python -m agent.atualizar_earnings            # coleta e grava
    python -m agent.atualizar_earnings --dry-run  # coleta e mostra, não grava
    python -m agent.atualizar_earnings --json     # ciclo completo p/ o checker

O overlay vai em RADAR_EARNINGS_OVERLAY (default
/var/cache/premercado/radar_earnings.json). ATENÇÃO: um rebuild da imagem
apaga esse cache — depois de `docker compose up -d --build`, rode o script de
novo (ou monte um volume pro diretório).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from datetime import date

# Import dos DOIS jeitos, mesmo padrão dos outros scripts do agente que
# rodam tanto standalone quanto como módulo do pacote.
try:
    from brt import today_brt
    from http_retry import SESSION
    from radar_ia_2026 import EARNINGS
    import provider_health
except ImportError:
    from agent.brt import today_brt
    from agent.http_retry import SESSION
    from agent.radar_ia_2026 import EARNINGS
    from agent import provider_health

_BASE_URL = "https://www.alphavantage.co/query"

OVERLAY_PATH_DEFAULT = "/var/cache/premercado/radar_earnings.json"

# Horizonte da consulta. 3month é o default da própria API; 6month cobre os
# tickers de tema (SNDK/STX/LRCX em out-nov) pela MESMA chamada de cota — o
# custo extra é só payload. Quem cair fora do horizonte simplesmente não vem
# na resposta e mantém a data embutida, que é a degradação correta.
HORIZONTE_DEFAULT = os.environ.get("RADAR_EARNINGS_HORIZONTE", "6month")

# A cota é compartilhada com o feed de notícias; o teto é o mesmo que
# alpha_vantage_provider.py respeita. Este script gasta 1 chamada por
# execução, mas contá-la é o ponto: orçamento que alguém não debita é
# orçamento que não protege ninguém.
_ORCAMENTO_DIARIO = int(os.environ.get("AGENT_ALPHAVANTAGE_MAX_DIA", "15"))

# timeOfTheDay -> a convenção do radar ("BO" antes da abertura, "AC" depois
# do fechamento). Campo vazio é comum e vira None: "não sei" é um estado
# legítimo aqui, e chutar BO faria o consumidor tratar dado ausente como
# informação.
_QUANDO = {"pre-market": "BO", "post-market": "AC"}


def _overlay_path() -> str:
    return os.environ.get("RADAR_EARNINGS_OVERLAY") or OVERLAY_PATH_DEFAULT


def _api_key() -> str:
    return os.environ.get("ALPHAVANTAGE_API_KEY", "").strip()


def universo() -> list[str]:
    """Os tickers que o radar acompanha — o filtro aplicado ao CSV inteiro."""
    return sorted(EARNINGS)


def baixar_calendario(horizonte: str = HORIZONTE_DEFAULT) -> str:
    """Devolve o CSV cru do EARNINGS_CALENDAR. Levanta em qualquer falha —
    quem chama decide se isso vira erro de saída ou {"ok": false}."""
    chave = _api_key()
    if not chave:
        raise RuntimeError("ALPHAVANTAGE_API_KEY ausente")

    if not provider_health.consumir_orcamento_diario("alphavantage", _ORCAMENTO_DIARIO):
        raise RuntimeError(
            f"cota diária da Alpha Vantage ({_ORCAMENTO_DIARIO}) esgotada — "
            f"preservando a cota do feed de notícias")

    resp = SESSION.get(
        _BASE_URL,
        params={"function": "EARNINGS_CALENDAR", "horizon": horizonte, "apikey": chave},
        timeout=30,
    )
    resp.raise_for_status()
    texto = resp.text or ""

    # O aviso de cota/chave/premium vem como JSON com 200 OK. Sem esta
    # checagem o csv.DictReader leria o JSON como uma linha de dados
    # esquisita e devolveria zero eventos — indistinguível, para quem chama,
    # de "ninguém reporta nos próximos 6 meses".
    if texto.lstrip().startswith("{"):
        try:
            body = json.loads(texto)
            motivo = (body.get("Note") or body.get("Information")
                      or body.get("Error Message") or texto[:160])
        except ValueError:
            motivo = texto[:160]
        raise RuntimeError(f"Alpha Vantage respondeu aviso em vez de CSV: {str(motivo)[:200]}")

    if "reportDate" not in texto:
        raise RuntimeError(f"CSV sem a coluna reportDate: {texto[:160]!r}")

    provider_health.record_success("alphavantage")
    return texto


def eventos_do_csv(texto: str, tickers: list[str] | None = None) -> dict[str, dict]:
    """Filtra o CSV para o universo do radar.

    Devolve `{TICKER: {"data": ISO, "quando": "BO"|"AC"|None}}` com o evento
    MAIS PRÓXIMO de cada ticker. A API pode listar mais de um por papel
    (trimestres à frente) e a ordem do CSV não é garantida; o radar só fala
    do próximo, e pegar o errado adiantaria o calendário em três meses sem
    nada na tela denunciando.
    """
    alvo = set(tickers if tickers is not None else universo())
    out: dict[str, dict] = {}
    for linha in csv.DictReader(io.StringIO(texto)):
        t = (linha.get("symbol") or "").strip().upper()
        if t not in alvo:
            continue
        crua = (linha.get("reportDate") or "").strip()
        try:
            dia = date.fromisoformat(crua)
        except ValueError:
            continue  # data ilegível é dado ausente, não motivo pra derrubar o lote
        anterior = out.get(t)
        if anterior is not None and anterior["data"] <= dia.isoformat():
            continue
        out[t] = {
            "data": dia.isoformat(),
            "quando": _QUANDO.get((linha.get("timeOfTheDay") or "").strip().lower()),
        }
    return out


def diferencas(eventos: dict[str, dict]) -> dict:
    """Compara com o que o radar tem AGORA, para o log do checker.

    Nota sobre a base de comparação: EARNINGS já vem com o overlay anterior
    aplicado (radar_ia_2026 faz isso no import). Então na primeira execução
    isto compara contra o embutido, e da segunda em diante contra o refresh
    anterior — que é o que interessa a quem lê o log ("o que mudou desde a
    última vez?"). Mesma ressalva que resumo_mudancas() faz nas correlações.
    """
    mudaram, confirmados = [], 0
    for t, ev in sorted(eventos.items()):
        atual = EARNINGS.get(t, {})
        if atual.get("data") != ev["data"]:
            mudaram.append({"ticker": t, "de": atual.get("data"), "para": ev["data"]})
        else:
            confirmados += 1
    return {"mudaram": mudaram, "confirmados": confirmados,
            "ausentes": sorted(set(universo()) - set(eventos))}


def gravar_overlay(eventos: dict[str, dict], horizonte: str = HORIZONTE_DEFAULT,
                   path: str | None = None) -> str:
    """Escrita atômica (tmp + rename) pra um leitor concorrente nunca ver
    JSON pela metade — mesmo cuidado do overlay de correlações."""
    destino = path or _overlay_path()
    hoje = today_brt()
    blob = {
        "atualizado_em": hoje.isoformat(),
        "horizonte": horizonte,
        "fonte": "alphavantage_earnings_calendar",
        "earnings": dict(sorted(eventos.items())),
    }
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    tmp = destino + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(blob, f, ensure_ascii=False, indent=1)
    os.replace(tmp, destino)
    return destino


def atualizar_e_gravar(horizonte: str = HORIZONTE_DEFAULT) -> dict:
    """Ciclo completo (baixar -> filtrar -> gravar) numa chamada, pro checker
    diário (lib/radar-earnings-checker.ts) consumir como JSON.

    Nunca levanta: devolve {"ok": false, "erro": ...} pra qualquer falha, no
    mesmo espírito dos outros scripts chamados por checker — um refresh que
    não deu certo deixa o overlay anterior (ou o embutido) valendo, que é
    sempre melhor que derrubar o ciclo de background."""
    try:
        texto = baixar_calendario(horizonte)
        eventos = eventos_do_csv(texto)
        if not eventos:
            # Zero eventos com CSV válido não é "ninguém reporta": é filtro
            # que não casou nada, e gravar isso apagaria o calendário inteiro.
            return {"ok": False, "erro": "nenhum ticker do radar veio no calendário"}
        dif = diferencas(eventos)
        destino = gravar_overlay(eventos, horizonte)
        return {
            "ok": True,
            "tickers": len(eventos),
            "confirmados": dif["confirmados"],
            "mudaram": dif["mudaram"],
            "ausentes": dif["ausentes"],
            "overlay": destino,
        }
    except Exception as e:
        provider_health.record_failure("alphavantage")
        return {"ok": False, "erro": f"{type(e).__name__}: {e}"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description="Atualiza o calendário de earnings do Radar IA (Alpha Vantage)")
    p.add_argument("--dry-run", action="store_true",
                   help="coleta e mostra, não grava o overlay")
    p.add_argument("--horizonte", default=HORIZONTE_DEFAULT, metavar="H",
                   help=f"3month|6month|12month (default {HORIZONTE_DEFAULT})")
    p.add_argument("--json", action="store_true",
                   help="ciclo completo, saída JSON (usado pelo checker diário)")
    args = p.parse_args(argv)

    if args.json:
        # stdout fica EXCLUSIVO do JSON (o Node dá JSON.parse nele); todo o
        # progresso humano vai pra stderr, mesmo padrão dos outros scripts.
        saida = atualizar_e_gravar(args.horizonte)
        print(json.dumps(saida, ensure_ascii=False))
        return 0 if saida.get("ok") else 1

    print(f"baixando calendário ({args.horizonte})...", file=sys.stderr)
    try:
        texto = baixar_calendario(args.horizonte)
    except Exception as e:
        print(f"falhou: {e}\noverlay NÃO gravado (o embutido continua valendo)",
              file=sys.stderr)
        return 1

    eventos = eventos_do_csv(texto)
    if not eventos:
        print("nenhum ticker do radar veio no calendário -- overlay NÃO gravado",
              file=sys.stderr)
        return 1

    dif = diferencas(eventos)
    print(f"\n{len(eventos)} tickers no calendário "
          f"({dif['confirmados']} confirmam a data atual)")
    if dif["mudaram"]:
        print(f"\nMUDARAM ({len(dif['mudaram'])}):")
        for m in dif["mudaram"]:
            print(f"  {m['ticker']:<6} {m['de'] or '--':>10} -> {m['para']}")
    if dif["ausentes"]:
        print(f"\nfora do horizonte (mantêm a data embutida): "
              f"{', '.join(dif['ausentes'])}")

    if args.dry_run:
        print("\n--dry-run: overlay não gravado.")
        return 0
    destino = gravar_overlay(eventos, args.horizonte)
    print(f"\noverlay gravado em {destino} -- novos processos do radar já leem daqui.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
