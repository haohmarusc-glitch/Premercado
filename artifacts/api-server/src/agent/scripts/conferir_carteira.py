"""Confere a carteira do app contra o extrato da corretora.

Por que existe: nada no sistema detecta deriva entre as duas. Uma compra
feita na corretora e não lançada no app fica invisível para sempre -- o
agente não analisa o papel, não cria plano de saída, não dispara alerta, e
não há como ele saber que não sabe.

Achado na conferência manual de 17/09/2026, com oito posições:

    SNDK   US$ 238 na corretora, INEXISTENTE no app
    MRVL   US$ 200 no app, US$ 500 na corretora  (compras não lançadas)
    ARM    US$ 700 no app, US$ 600 na corretora  (venda não baixada)

Treze por cento da carteira fora do radar, e nenhum sintoma na tela.

Três fontes, não duas
---------------------
Compara o extrato com DUAS leituras do app, porque elas podem divergir
entre si:

  lotes      soma de `amount` dos lotes com `saleDate` nulo. É a fonte de
             verdade -- `recomputePosition` deriva tudo daqui.
  guardado   o campo `investedAmount` da posição. Deveria ser igual à soma
             acima, mas `PUT /portfolio/:id` edita campos direto, e campo
             editável à mão diverge do derivado eventualmente (§1 do
             playbook: foi assim que a MU ficou aparecendo ativa depois de
             vendida).

Divergência entre esses dois é achado próprio, independente da corretora.

Pela API e não pelo banco: é o mesmo caminho do carteira.py, não precisa de
credencial de Postgres, e passa pela lógica que o app realmente usa.

ATENÇÃO à lista da corretora: `GET /portfolio` devolve TAMBÉM as posições
totalmente vendidas (o frontend precisa delas para a seção "Ações
Vendidas"), então quem decide o que está aberto são os LOTES.

Uso
---
    export OPERATOR_API_KEY=...          # mesma chave do carteira.py
    python3 -m agent.scripts.conferir_carteira < extrato.txt

Formato do extrato, uma posição por linha:

    TICKER  <valor atual>  <resultado com sinal>

    NVDA  1434.52  +66.86
    ARM    521,52   -78,47      <- vírgula decimal também serve
    MRVL   496.18    -3.76

O SINAL do resultado é obrigatório: na tela da corretora ele é a COR
(verde/vermelho), e essa informação se perde ao copiar. Sem sinal, a linha
entra só como presença, e o relatório diz isso.

O custo sai de `valor - resultado`, que é o que se compara com o app.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8080/api")
API_KEY = os.environ.get("OPERATOR_API_KEY", "")

# Abaixo disto é arredondamento entre lotes e taxa de corretagem, não
# divergência. Calibrado na conferência de 17/09: NVDA saiu -1,34 sobre
# 1.369 (0,1%, seis lotes) e ADI -0,59 sobre 200 (0,3%) -- os dois ruído;
# ARM -100 (16,7%) e MRVL +300 (60%) -- os dois reais. Qualquer corte entre
# 1% e 15% separava os mesmos casos; 2% fica no meio, longe das duas bordas.
TOLERANCIA_ABS = 2.00
TOLERANCIA_REL = 0.02

_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,6}$")


def _numero(txt: str) -> float | None:
    """Aceita 1.434,52 (pt-BR), 1,434.52 (en-US) e 1434.52."""
    t = txt.strip().replace("US$", "").replace("$", "").replace(" ", "")
    t = t.replace("−", "-").replace("–", "-")  # menos unicode
    if not t or not re.search(r"\d", t):
        return None
    if "," in t and "." in t:
        # o separador decimal é o que aparece por ÚLTIMO
        t = (t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".")
             else t.replace(",", ""))
    elif "," in t:
        t = t.replace(",", ".")
    try:
        return float(t)
    except ValueError:
        return None


def ler_extrato(linhas) -> tuple[dict, list[str]]:
    """{ticker: {"valor", "resultado", "custo"}}, mais as linhas ignoradas."""
    saida, ignoradas = {}, []
    for bruta in linhas:
        linha = bruta.strip()
        if not linha or linha.startswith("#"):
            continue
        campos = linha.replace("\t", " ").split()
        if not campos or not _TICKER.match(campos[0].upper()):
            ignoradas.append(linha)
            continue
        ticker = campos[0].upper()
        nums = [n for n in (_numero(c) for c in campos[1:]) if n is not None]
        if not nums:
            ignoradas.append(linha)
            continue
        valor = nums[0]
        # Só conta como resultado se o campo trouxe sinal explícito -- a cor
        # da tela não sobrevive ao copiar, e adivinhar aqui inventaria custo.
        assinado = [c for c in campos[1:] if c.lstrip("US$ ").startswith(("+", "-", "−"))]
        resultado = nums[1] if (len(nums) > 1 and assinado) else None
        saida[ticker] = {
            "valor": valor,
            "resultado": resultado,
            "custo": None if resultado is None else round(valor - resultado, 2),
        }
    return saida, ignoradas


def _api(caminho: str):
    req = urllib.request.Request(
        f"{API_BASE}{caminho}", headers={"Authorization": f"Bearer {API_KEY}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def ler_app(api=_api) -> dict:
    """{ticker: {"lotes", "guardado", "abertos", "vendidos", "etf"}}."""
    posicoes = api("/portfolio")
    saida = {}
    for pos in posicoes:
        lotes = api(f"/portfolio/{pos['id']}/purchases")
        abertos = [x for x in lotes if not (x.get("saleDate") and x.get("salePrice"))]
        vendidos = len(lotes) - len(abertos)
        if not abertos:
            continue          # posição encerrada: some da carteira, fica o histórico
        soma = round(sum(float(x.get("amount") or 0) for x in abertos), 2)
        t = str(pos["ticker"]).upper()
        anterior = saida.get(t)
        if anterior:          # mesma ação em duas posições: junta
            anterior["lotes"] += soma
            anterior["abertos"] += len(abertos)
            anterior["vendidos"] += vendidos
            continue
        saida[t] = {
            "lotes": soma,
            "guardado": (None if pos.get("investedAmount") is None
                         else float(pos["investedAmount"])),
            "abertos": len(abertos),
            "vendidos": vendidos,
            "etf": bool(pos.get("isEtf")),
        }
    return saida


def divergente(a: float, b: float) -> bool:
    d = abs(a - b)
    return d > TOLERANCIA_ABS and d > TOLERANCIA_REL * max(abs(a), abs(b), 1.0)


def conferir(extrato: dict, app: dict) -> dict:
    achados = {"so_na_corretora": [], "so_no_app": [], "valor_diferente": [],
               "campo_dessincronizado": [], "ok": [], "sem_sinal": []}

    for t, e in sorted(extrato.items()):
        if t not in app:
            achados["so_na_corretora"].append((t, e))
            continue
        a = app[t]
        if e["custo"] is None:
            achados["sem_sinal"].append((t, e, a))
        elif divergente(e["custo"], a["lotes"]):
            achados["valor_diferente"].append((t, e, a))
        else:
            achados["ok"].append((t, e, a))

    for t, a in sorted(app.items()):
        if t not in extrato and not a["etf"]:
            achados["so_no_app"].append((t, a))
        if a["guardado"] is not None and divergente(a["guardado"], a["lotes"]):
            achados["campo_dessincronizado"].append((t, a))
    return achados


def _brl(v) -> str:
    return "—" if v is None else f"{v:>10,.2f}"


def relatar(achados: dict, extrato: dict, app: dict, ignoradas: list) -> int:
    print("=" * 78)
    print("Conferidor de carteira — extrato da corretora x app")
    print("=" * 78)
    if ignoradas:
        print(f"\n{len(ignoradas)} linha(s) do extrato ignoradas por não terem "
              "ticker e número reconhecíveis:")
        for x in ignoradas[:5]:
            print(f"    {x[:70]}")

    problemas = 0

    if achados["so_na_corretora"]:
        problemas += len(achados["so_na_corretora"])
        print("\n### AUSENTE NO APP — o agente não enxerga estas posições")
        print("    Sem análise, sem plano de saída, sem alerta.")
        for t, e in achados["so_na_corretora"]:
            print(f"    {t:<7} corretora {_brl(e['custo'] or e['valor'])}")

    if achados["valor_diferente"]:
        problemas += len(achados["valor_diferente"])
        print("\n### VALOR DIFERENTE — o app conhece o papel, com o tamanho errado")
        print(f"    {'':7}{'corretora':>11}{'app (lotes)':>13}{'diferença':>12}   lotes")
        for t, e, a in achados["valor_diferente"]:
            d = e["custo"] - a["lotes"]
            print(f"    {t:<7}{_brl(e['custo'])}{_brl(a['lotes'])}{d:>+12,.2f}"
                  f"   {a['abertos']} aberto(s), {a['vendidos']} vendido(s)")
            print(f"    {'':7}-> {'compra não lançada' if d > 0 else 'venda não baixada'}"
                  f" de ~{abs(d):,.2f}")

    if achados["so_no_app"]:
        problemas += len(achados["so_no_app"])
        print("\n### SÓ NO APP — posição aberta que não apareceu no extrato")
        print("    Pode ser venda não baixada, ou o extrato estar incompleto")
        print("    (a tela da corretora rola; confira se colou a lista inteira).")
        for t, a in achados["so_no_app"]:
            print(f"    {t:<7} app {_brl(a['lotes'])}  ({a['abertos']} lote(s) aberto(s))")

    if achados["campo_dessincronizado"]:
        problemas += len(achados["campo_dessincronizado"])
        print("\n### CAMPO DESSINCRONIZADO — achado interno, independe da corretora")
        print("    `investedAmount` guardado não bate com a soma dos lotes abertos.")
        print("    `recomputePosition` deveria manter os dois iguais; quando não")
        print("    mantém, quem lê o campo vê número diferente de quem soma os lotes.")
        for t, a in achados["campo_dessincronizado"]:
            print(f"    {t:<7} guardado {_brl(a['guardado'])}   lotes {_brl(a['lotes'])}")

    if achados["sem_sinal"]:
        print("\n### SEM SINAL NO RESULTADO — comparado só por presença")
        print("    A cor da tela (verde/vermelho) não sobrevive ao copiar. Sem o")
        print("    sinal não dá para derivar o custo, e comparar valor de hoje")
        print("    com valor investido acusaria diferença que é só a variação.")
        for t, e, a in achados["sem_sinal"]:
            print(f"    {t:<7} corretora valor {_brl(e['valor'])}   app {_brl(a['lotes'])}")

    print("\n" + "-" * 78)
    if achados["ok"]:
        print(f"Batem dentro da tolerância ({len(achados['ok'])}): "
              + ", ".join(t for t, _, _ in achados["ok"]))
    total_e = sum(v["custo"] for v in extrato.values() if v["custo"] is not None)
    total_a = sum(v["lotes"] for v in app.values() if not v["etf"])
    print(f"Custo total  — corretora {total_e:>12,.2f}   app {total_a:>12,.2f}"
          f"   diferença {total_e - total_a:>+12,.2f}")
    print("=" * 78)
    if problemas:
        print(f"{problemas} divergência(s). Corrija na tela de carteira do app.")
    else:
        print("Nenhuma divergência.")
    return problemas


def main() -> None:
    if not API_KEY:
        print("ERRO: exporte OPERATOR_API_KEY (a mesma do carteira.py).",
              file=sys.stderr)
        raise SystemExit(2)
    fonte = open(sys.argv[1], encoding="utf-8") if len(sys.argv) > 1 else sys.stdin
    with fonte:
        extrato, ignoradas = ler_extrato(fonte)
    if not extrato:
        print("ERRO: nenhuma linha reconhecida. Formato: TICKER valor ±resultado",
              file=sys.stderr)
        raise SystemExit(2)
    try:
        app = ler_app()
    except Exception as e:  # noqa: BLE001 — rede/API, mensagem é o produto
        print(f"ERRO: não consegui falar com {API_BASE}: {e}", file=sys.stderr)
        raise SystemExit(1) from e
    raise SystemExit(1 if relatar(conferir(extrato, app), extrato, app, ignoradas) else 0)


if __name__ == "__main__":
    main()
