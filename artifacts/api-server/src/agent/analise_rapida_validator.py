"""
Validador da Análise Rápida -- confere a SAÍDA, não o prompt.

Por que este arquivo existe. O `SYSTEM` de analise_rapida_ia.py declara 18
regras, e `test_analise_rapida_ia.py` verifica que elas continuam ESCRITAS lá
("a string ainda contém 'nunca R$'"). Isso protege contra alguém apagar a
regra ao consolidar o prompt -- e não protege contra o modelo desobedecê-la.
Era a mesma forma exata que a leitura da cesta tinha até 25/08/2026, quando um
texto chamou de "padrão estatisticamente relevante" uma correlação com p
corrigido de 0,462. Regra no prompt sem conferência é sugestão.

O que este módulo NÃO faz: reescrever ou esconder o texto. Ele DECLARA o que
não fecha; quem chama decide (hoje: uma retentativa com os apontamentos e, se
persistir, publicação COM os avisos ao lado).

Sete checagens, escolhidas por RISCO DE ALARME FALSO, não por importância.
Duas regras do prompt ficaram deliberadamente de fora -- "volatilidade chega
como fração" e "todo número vem do JSON" -- porque só dariam para checar por
heurística, e validador que grita à toa ensina o leitor a ignorar o bloco
amarelo. Aí ele fica pior que nenhum.
"""
import re
import unicodedata

# As seções que o prompt pede "EXATAMENTE". A Síntese é a que mais importa
# checar: o próprio SYSTEM avisa que ela "é a primeira a se perder quando o
# texto estica", o que é uma previsão de falha esperando conferência.
SECOES_OBRIGATORIAS = (
    "Quadro geral", "Fundamento e valuation", "Leitura técnica",
    "Níveis que importam", "Earnings e volatilidade", "Síntese",
)

# Recomendação explícita. O prompt: "NÃO recomende comprar ou vender. Descreva
# cenários e níveis de invalidação; a decisão é do leitor."
_RECOMENDA = (
    r"recomend\w+\s+(?:a\s+)?(?:compra|venda|comprar|vender)",
    r"sugiro\s+(?:comprar|vender)",
    r"vale\s+(?:a\s+pena\s+)?(?:comprar|vender)",
    r"hora\s+de\s+(?:comprar|vender)",
    r"deve[-\s]?se\s+(?:comprar|vender)",
    r"aconselho\s+(?:comprar|vender)",
)
# Meta-frase: o modelo dizendo que NÃO vai recomendar é obediência, não
# violação. Sem esta exceção o validador puniria justamente quem acertou.
_META_RECUSA = (r"n[ãa]o\s+(?:vou\s+|cabe\s+|é\s+papel\s+)?recomend",
                r"sem\s+recomenda")

# R1/R2/S1/S2 são bandas estatísticas (preço ± reação média a earnings), não
# suporte/resistência do gráfico. O prompt proíbe chamá-los de "piso" ou "zona
# de defesa" -- e é a mesma ressalva que o relatório de earnings imprime em
# toda seção.
_BANDAS = r"\b[RS][12]\b"
_TERMOS_TECNICOS = ("suporte", "resistencia", "piso", "zona de defesa",
                    "teto tecnico", "fundo tecnico")


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(texto or ""))
                   if unicodedata.category(c) != "Mn").lower()


def _sem_blocos_de_codigo(texto: str) -> str:
    """Bloco ```...``` é dado citado, não afirmação -- mesma precaução dos
    outros dois validadores."""
    return re.sub(r"```.*?```", " ", str(texto or ""), flags=re.DOTALL)


def _frases(texto: str) -> list:
    return re.split(r"(?<=[.!?])\s+|\n+", texto)


def _num_br(valor: float) -> list:
    """As grafias em que um preço pode legitimamente aparecer no texto.

    O modelo escreve em pt-BR ("US$ 225,01"), mas o JSON traz 225.01 -- checar
    só uma das formas produziria apontamento contra texto correto."""
    inteiro = f"{valor:.2f}"
    return [inteiro, inteiro.replace(".", ","),
            f"{valor:,.2f}".replace(",", "@").replace(".", ",").replace("@", "."),
            f"{valor:.1f}", f"{valor:.1f}".replace(".", ","),
            f"{int(round(valor))}"]


def validar_analise(texto: str, dados: dict | None = None) -> list:
    """[{nivel, codigo, mensagem}] -- vazio quando nada destoa.

    "ERRO" é afirmação que o dado CONTRADIZ ou regra dura desobedecida;
    "AVISO" é o que falta declarar. Só ERRO justifica gastar uma retentativa
    de LLM."""
    achados = []
    prosa = _sem_blocos_de_codigo(texto or "")
    if not prosa.strip():
        return achados
    prosa_sa = _sem_acento(prosa)
    dados = dados or {}

    def add(nivel, codigo, mensagem):
        achados.append({"nivel": nivel, "codigo": codigo, "mensagem": mensagem})

    # ── 1. as seis seções ───────────────────────────────────────────────────
    faltando = [s for s in SECOES_OBRIGATORIAS
                if f"## {_sem_acento(s)}" not in prosa_sa]
    if faltando:
        add("ERRO", "ANALISE_SECAO_FALTANDO",
            "faltam as seções: " + ", ".join(faltando)
            + (" — a Síntese é a primeira a se perder quando o texto estica."
               if "Síntese" in faltando else "."))

    # ── 2. moeda ────────────────────────────────────────────────────────────
    if re.search(r"R\$", prosa):
        add("ERRO", "ANALISE_MOEDA_ERRADA",
            "usa R$ — os ativos são listados nos EUA e o prompt manda não "
            "converter; escreva US$.")

    # ── 3. momentum anualizado descrito como período ────────────────────────
    #
    # Número certo virando afirmação falsa: `momentumAnnualPct` é taxa
    # ANUALIZADA extrapolada de `lookbackDays`. "106% em 90 dias" é o mesmo
    # número dizendo outra coisa. A checagem casa o VALOR do campo seguido de
    # "em N dias/pregões" -- targeted de propósito, porque "caiu 5% em 3 dias"
    # é descrição legítima de preço e não pode ser apontada.
    momentum = ((dados.get("technicals") or {}).get("momentumAnnualPct")
                or (dados.get("snapshot") or {}).get("momentumAnnualPct"))
    if isinstance(momentum, (int, float)):
        for grafia in _num_br(abs(float(momentum))):
            # Radicais, não palavras inteiras: "pregão"/"pregões" chegam aqui
            # já sem acento ("pregao"/"pregoes") e a forma plural não casaria
            # com a singular. As variantes acentuadas seriam letra morta --
            # `prosa_sa` já passou por _sem_acento.
            if re.search(rf"{re.escape(grafia)}\s*%\s*(?:em|nos?|durante)\s+\d+\s*"
                         rf"(?:dia|preg|seman|mes)\w*", prosa_sa):
                add("ERRO", "ANALISE_MOMENTUM_COMO_PERIODO",
                    f"apresenta o momentum de {momentum}% como variação de um "
                    f"período — o campo é taxa ANUALIZADA extrapolada da "
                    f"janela, não o que o papel fez nela.")
                break

    # ── 4. divergência de preço não declarada ───────────────────────────────
    preco = dados.get("precoAtual") or {}
    por_painel = preco.get("porPainel") or {}
    if preco.get("divergenciaPct") and len(por_painel) >= 2:
        valores = sorted(float(v) for v in por_painel.values())
        extremos = (valores[0], valores[-1])
        ausentes = [v for v in extremos
                    if not any(g in prosa for g in _num_br(v))]
        if ausentes:
            add("ERRO", "ANALISE_DIVERGENCIA_OMITIDA",
                f"os painéis divergem {preco['divergenciaPct']}% e o texto não "
                f"traz os dois preços (US$ {extremos[0]:.2f} e "
                f"US$ {extremos[1]:.2f}) — sem eles o leitor não sabe qual "
                f"indicador está apoiado em qual preço.")

    # ── 5. recomendação de compra/venda ─────────────────────────────────────
    for padrao in _RECOMENDA:
        m = re.search(padrao, prosa_sa)
        if not m:
            continue
        janela = prosa_sa[max(0, m.start() - 40):m.end()]
        if any(re.search(neg, janela) for neg in _META_RECUSA):
            continue  # o modelo dizendo que não recomenda é obediência
        add("ERRO", "ANALISE_RECOMENDACAO",
            "recomenda comprar ou vender — o prompt pede cenários e níveis de "
            "invalidação; a decisão é do leitor.")
        break

    # ── 6. bandas estatísticas tratadas como nível técnico ──────────────────
    for frase in _frases(prosa_sa):
        if not re.search(_BANDAS, frase, re.IGNORECASE):
            continue
        if any(t in frase for t in _TERMOS_TECNICOS):
            add("ERRO", "ANALISE_BANDA_COMO_NIVEL_TECNICO",
                "trata R1/R2/S1/S2 como suporte ou resistência do gráfico — "
                "são bandas de volatilidade (preço ± reação média a earnings).")
            break

    # ── 7. balanço já ocorrido escrito no futuro ────────────────────────────
    #
    # Incidente real (NBIS, 17/08/2026): a janela de run-up engolia o pregão de
    # reação e o texto dizia que o papel "chega esticado ao balanço" -- de um
    # balanço que já tinha acontecido.
    runup = (((dados.get("reaction") or {}).get("summary") or {}).get("runup")) or {}
    if runup.get("janela_contem_earnings"):
        if re.search(r"cheg\w+\s+esticad|vai\s+cheg\w+\s+esticad|"
                     r"chega\w*\s+ao\s+balan[cç]o", prosa_sa):
            add("ERRO", "ANALISE_BALANCO_NO_FUTURO",
                "escreve no futuro sobre um balanço que JÁ ocorreu há "
                f"{runup.get('pregoes_desde_earnings', '?')} pregão(ões) — o "
                f"run-up bruto inclui o próprio salto do evento.")

    return achados


def erros(achados: list) -> list:
    return [a for a in achados if a.get("nivel") == "ERRO"]


def bloco_de_correcao(achados: list) -> str:
    """O texto que volta ao modelo na retentativa -- só os ERROS."""
    duros = erros(achados)
    if not duros:
        return ""
    linhas = [f"- {a['mensagem']}" for a in duros]
    return ("\n\nA versão anterior desta análise foi recusada pelo validador "
            "pelos motivos abaixo. Reescreva corrigindo CADA um, sem inventar "
            "número novo e mantendo as seções e o tamanho pedidos:\n"
            + "\n".join(linhas))


def resumo_legivel(achados: list) -> list:
    return [f"[{a['nivel']}] {a['codigo']}: {a['mensagem']}" for a in achados]
