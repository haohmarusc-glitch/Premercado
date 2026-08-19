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

NÃO REPRODUZ MAIS (19/08/2026). Medido de novo, 3/3 nos dois provedores:

    gemini/gemini-2.5-flash      21,4s e 17,6s
    anthropic/claude-sonnet-5    37,3s e 34,5s

Duas coisas mudaram entre uma medição e outra, e a segunda desqualifica em
parte a primeira: o SYSTEM foi consolidado (as regras de divergência ficaram
explícitas), e ESTA SONDA ESTAVA QUEBRADA -- o `_fundamento` fabricado nunca
chegava ao modelo, então o payload de 18/08 não era o que o caso descrevia.
O texto que o gemini produziu naquele dia foi real; as condições em que ele
o produziu, não eram as documentadas acima.

Mantido o registro em vez de apagado: é o histórico de por que a exclusão foi
considerada, e a lição de que uma sonda quebrada produz evidência contra o
provedor errado.

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
        # SEM `_fundamento` fabricado, e isso não é descuido.
        #
        # `analisar()` sobrescreve incondicionalmente o `_fundamento` recebido
        # pelo que busca ao vivo (ver a chamada de _buscar_fundamento). Qualquer
        # valuation montado aqui é DESCARTADO -- deixá-lo no caso mentiria sobre
        # o que o teste exercita.
        #
        # A divergência acontece sozinha: o preço de 180 é fabricado e o do
        # painel de valuation vem da FMP, então os dois discordam por
        # construção. É um caso real, não simulado.
        "dados": {
            "ticker": "NVDA",
            "snapshot": {"price": 180.0},
            "technicals": {"price": 180.0, "rsi": 55.0},
        },
        # Nenhum número VIVO no `exige`.
        #
        # A versão anterior cobrava o literal "225", que era o preço de
        # valuation que eu tinha inventado -- e que nunca chegava ao modelo. Ele
        # passou por coincidência enquanto o preço real rondava aquele valor, e
        # reprovou quando o mercado andou. A sonda estava medindo a cotação do
        # dia, não a obediência do modelo, e me levou a "consertar" o prompt
        # duas vezes atrás de uma regressão que não existia.
        #
        # O que se cobra agora é o COMPORTAMENTO: dizer que divergem, citar o
        # preço que este teste controla, e nomear o painel de onde vem o outro.
        "exige": [
            (r"diverg|discord|defasad|incompat|difere", "citar a divergência entre os painéis"),
            (r"180", "citar o preço que o caso controla"),
            (r"valuation", "nomear o painel de onde vem o outro preço"),
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
            # Largo de propósito. A primeira versão cobrava "não está/não há/
            # não foi" e reprovava "NENHUM indicador técnico foi calculado, o
            # que IMPEDE qualquer leitura" -- que é a frase certa, escrita pelo
            # anthropic. Cobrar uma forma de dizer em vez do conteúdo dito é
            # transformar a sonda em corretor de estilo.
            (r"nenhum|indispon|ausen|sem dado|impede|"
             r"não (est|h|foi|for|vier|é|consta|dispon)",
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


# Marcas de negação. Um `proibe` casa a PALAVRA, e a mesma palavra aparece nas
# duas frases opostas:
#
#   "O RSI mostra o papel em SOBRECOMPRA."               <- erro real
#   "Não é possível avaliar momentum ou SOBRECOMPRA."    <- texto certo
#
# A segunda não é hipotética: o anthropic escreveu exatamente isso em 18/08/2026,
# e teria reprovado pelo motivo errado. Reprovar texto correto é pior que deixar
# passar um errado -- sonda que dá alarme falso é desligada, e aí ela não pega
# mais nada.
_NEGACAO_RE = re.compile(
    r"\b(não|nao|sem|ausen|indispon|impede|impossív|impossiv|nenhum|faltam?|"
    r"não há|inexist)\w*", re.I,
)


def _frase(texto: str, pos: int) -> str:
    """A sentença ao redor do achado. É a evidência do veredito: sem ela, quem
    lê o ✗ não tem como saber se o modelo errou ou se a regex é burra -- e
    sonda que não deixa auditar o próprio veredito não serve para decidir."""
    ini = max(texto.rfind(".", 0, pos), texto.rfind("\n", 0, pos)) + 1
    fim = texto.find(".", pos)
    fim = len(texto) if fim < 0 else fim + 1
    return " ".join(texto[ini:fim].split())


def _checar(texto: str, caso: dict) -> tuple[list[str], list[str]]:
    """Devolve (falhas, ignorados). O segundo existe para que a decisão de
    NÃO reprovar também apareça: descarte silencioso é como uma sonda começa a
    aprovar tudo sem ninguém notar."""
    falhas, ignorados = [], []
    for padrao, descricao in caso["exige"]:
        if not re.search(padrao, texto, re.I):
            falhas.append(f"não {descricao}")
    for padrao, descricao in caso["proibe"]:
        for achado in re.finditer(padrao, texto, re.I):
            frase = _frase(texto, achado.start())
            if _NEGACAO_RE.search(frase):
                ignorados.append(f"{descricao}? negado na frase: \u201c{frase}\u201d")
                continue
            falhas.append(f"{descricao}: \u201c{frase}\u201d")
    return falhas, ignorados


def main() -> int:
    provedor = os.environ.get("AGENT_PROVIDER_ORDER") or os.environ.get("AGENT_PROVIDER") or "(padrão)"
    print(f"sonda de qualidade — provedor: {provedor}\n", file=sys.stderr)

    reprovados = 0
    for caso in CASOS:
        # `_INICIO` é constante de MÓDULO, fixada no import -- correto em
        # produção, onde cada análise é um processo novo. A sonda roda vários
        # casos no mesmo processo, então sem zerar aqui o orçamento acumula:
        # medido em 18/08/2026, a "coleta" do 3º caso apareceu como 75,2s dos
        # 135s (a real foi 2s), e um 4º caso teria abortado com uma mensagem
        # falsa sobre uma lentidão que não existiu.
        mod._INICIO = time.monotonic()
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
        falhas, ignorados = _checar(texto, caso)
        marca = "✓" if not falhas else "✗"
        print(f"{marca} {caso['nome']}  {gasto:.1f}s  {len(texto)} chars", file=sys.stderr)
        if falhas:
            reprovados += 1
            print(f"    porquê: {caso['porque']}", file=sys.stderr)
            for f in falhas:
                print(f"    - {f}", file=sys.stderr)
        for i in ignorados:
            print(f"    ~ {i}", file=sys.stderr)

    print(f"\n{len(CASOS) - reprovados}/{len(CASOS)} casos ok", file=sys.stderr)
    return 1 if reprovados else 0


if __name__ == "__main__":
    sys.exit(main())
