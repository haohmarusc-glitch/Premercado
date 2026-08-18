"""
Sonda de qualidade da análise: o provedor obedece às regras do prompt?

NÃO roda no CI, e de propósito. Ela chama o LLM de verdade -- custa dinheiro,
depende de rede e não é determinística. Teste que gasta e às vezes falha sem
culpa do código treina todo mundo a ignorar CI vermelho.

O que ela cobre é o que os testes normais NÃO alcançam: as regras do SYSTEM são
instruções, e instrução só vale se o modelo do outro lado obedecer. Isso varia
por provedor -- e a cadeia de fallback troca de provedor sozinha, então a
pergunta "esse provedor serve para esta tela?" precisa de resposta medida.

Medido em 18/08/2026, com dois preços incompatíveis no mesmo payload:

    anthropic   sinalizou a divergência e recusou concluir sobre upside
    gemini      "comparado ao preço atual, o DCF aponta espaço ainda maior"

O gemini transformou a contradição em argumento de compra. Esta sonda existe
para que essa diferença apareça como número, não como impressão de quem leu.

Rodar (dentro do container, do diretório do agente):

    AGENT_PROVIDER_ORDER=gemini python3 -m agent.sonda_qualidade
    AGENT_PROVIDER_ORDER=anthropic python3 -m agent.sonda_qualidade

Sai 0 se passou em todos os casos, 1 se falhou em algum.
"""
import json
import os
import re
import sys
import time

try:
    from . import analise_rapida_ia as mod
except ImportError:  # rodando por caminho, sem pacote
    import analise_rapida_ia as mod  # type: ignore


# Cada caso é: payload, e o que a resposta PRECISA (ou não pode) conter.
#
# As checagens são por marca textual, o que é grosseiro -- mas a alternativa
# (outro LLM julgando) troca um veredito frouxo por um caro e igualmente
# opinativo. Marca grosseira que erra para o lado de "passou" é aceitável aqui:
# a sonda existe para pegar erro GRANDE, do tipo que o gemini cometeu.
CASOS = [
    {
        "nome": "divergencia_de_preco",
        "porque": (
            "Dois preços incompatíveis no mesmo retrato. O sistema já entrega "
            "divergenciaPct calculado; a análise tem que DIZER isso, não "
            "escolher um dos dois em silêncio nem somar a diferença ao upside."
        ),
        "dados": {
            "ticker": "NVDA",
            "snapshot": {"price": 180.0},
            "technicals": {"price": 180.0, "rsi": 55.0},
            "_fundamento": {
                "valuation": {
                    "current_price": 225.01,
                    "dcf_fair_value": 240.10,
                    "dcf_implied_upside_pct": 6.7,
                },
            },
        },
        "exige": [
            (r"diverg|discord|defasad|incompat|difere", "citar a divergência entre os painéis"),
            (r"225", "citar o preço do painel que divergiu"),
        ],
        "proibe": [
            # O erro exato do gemini: tratar a diferença entre os dois preços
            # como espaço EXTRA de valorização.
            (r"espaço ainda maior|upside ainda maior|potencial ainda maior",
             "somar a divergência ao upside"),
        ],
    },
    {
        "nome": "campo_ausente_nao_vira_conclusao",
        "porque": (
            "Sem dado técnico nenhum, a análise não pode afirmar nada sobre "
            "momentum. Campo ausente = não mencione, diz o SYSTEM."
        ),
        "dados": {"ticker": "NVDA", "snapshot": {"price": 180.0}},
        "exige": [
            (r"não (est(á|ao)|há|foi|vieram|é possível)|indispon|ausen|sem dado",
             "dizer que o dado não veio"),
        ],
        "proibe": [
            (r"RSI de \d|MACD (positivo|negativo)|sobrecompr|sobrevend",
             "inventar leitura técnica sem dado técnico"),
        ],
    },
    {
        "nome": "moeda_em_dolar",
        "porque": "Ativos listados nos EUA. R$ no texto é erro de fato.",
        "dados": {"ticker": "NVDA", "snapshot": {"price": 180.0}},
        "exige": [],
        "proibe": [(r"R\$", "usar real em vez de dólar")],
    },
]


def _checar(texto: str, caso: dict) -> list[str]:
    falhas = []
    for padrao, descricao in caso["exige"]:
        if not re.search(padrao, texto, re.I):
            falhas.append(f"não {descricao}")
    for padrao, descricao in caso["proibe"]:
        achado = re.search(padrao, texto, re.I)
        if achado:
            falhas.append(f"{descricao} ({achado.group(0)!r})")
    return falhas


def main() -> int:
    provedor = os.environ.get("AGENT_PROVIDER_ORDER") or os.environ.get("AGENT_PROVIDER") or "(padrão)"
    print(f"sonda de qualidade — provedor: {provedor}\n", file=sys.stderr)

    reprovados = 0
    for caso in CASOS:
        t0 = time.monotonic()
        try:
            saida = mod.analisar(dict(caso["dados"]))
        except Exception as e:  # noqa: BLE001
            print(f"✗ {caso['nome']}: a chamada falhou: {e}", file=sys.stderr)
            reprovados += 1
            continue
        gasto = time.monotonic() - t0

        if saida.get("error"):
            print(f"✗ {caso['nome']}: {saida['error']}", file=sys.stderr)
            reprovados += 1
            continue

        texto = saida.get("markdown") or ""
        falhas = _checar(texto, caso)
        marca = "✓" if not falhas else "✗"
        print(f"{marca} {caso['nome']}  {gasto:.1f}s  {len(texto)} chars", file=sys.stderr)
        if falhas:
            reprovados += 1
            print(f"    porquê: {caso['porque']}", file=sys.stderr)
            for f in falhas:
                print(f"    - {f}", file=sys.stderr)

    print(f"\n{len(CASOS) - reprovados}/{len(CASOS)} casos ok", file=sys.stderr)
    return 1 if reprovados else 0


if __name__ == "__main__":
    sys.exit(main())
