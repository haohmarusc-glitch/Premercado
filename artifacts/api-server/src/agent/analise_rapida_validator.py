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

O CRITÉRIO das checagens é RISCO DE ALARME FALSO, não importância. Duas regras
do prompt ficaram deliberadamente de fora -- "volatilidade chega como fração" e
"todo número vem do JSON" -- porque só dariam para checar por heurística, e
validador que grita à toa ensina o leitor a ignorar o bloco amarelo. Aí ele
fica pior que nenhum.

REESCRITA DE 26/08/2026. Três rodadas reais seguidas produziram três falsos
positivos (WOLF, ADI, ADI), e uma auditoria dos dois validadores reproduziu 39
defeitos. Praticamente todos eram a MESMA falha em roupas diferentes: casar
TOKEN em vez de AFIRMAÇÃO. As primitivas comuns (fronteira numérica,
antinegação, leitura defensiva de payload) moraram em `validador_nucleo`, e
cada checagem daqui passou a exigir o predicado, não a co-ocorrência.
"""
import re

from .validador_nucleo import (afirmacao_negada, caminho, cita_numero, frases,
                               grafias, minusculas, num_finito, sem_acento,
                               sem_blocos_de_codigo, texto_utilizavel)
from .validador_nucleo import (avisos, bloco_de_correcao as _bloco,  # noqa: F401
                               erros, linha_de_log, resumo_legivel)

# Reexportado com o nome antigo: a fronteira numérica nasceu aqui (falso
# positivo do ADI) e migrou para o núcleo quando os três validadores passaram
# a compartilhá-la. Quem já importava daqui continua funcionando.
_cita_numero = cita_numero

# As seções que o prompt pede "EXATAMENTE". A Síntese é a que mais importa
# checar: o próprio SYSTEM avisa que ela "é a primeira a se perder quando o
# texto estica", o que é uma previsão de falha esperando conferência.
SECOES_OBRIGATORIAS = (
    "Quadro geral", "Fundamento e valuation", "Leitura técnica",
    "Níveis que importam", "Earnings e volatilidade", "Síntese",
)

# Recomendação explícita. O prompt: "NÃO recomende comprar ou vender. Descreva
# cenários e níveis de invalidação; a decisão é do leitor."
#
# A lista antiga cobria seis construções e deixava passar as mais comuns em
# pt-BR: o imperativo ("Compre WOLF"), a forma nominal com preposição
# ("recomendação DE compra"), o condicional ("eu compraria") e o rótulo de
# casa de análise ("BUY"). Numa regra que o prompt trata como dura, cobrir
# pouco é pior que não checar -- dá sensação de anteparo sem o anteparo.
_RECOMENDA = (
    r"recomend\w*\s+(?:a\s+|de\s+|da\s+)?(?:compra|venda|comprar|vender)",
    r"(?:minha|nossa)\s+(?:recomenda[cç][ãa]o|indica[cç][ãa]o|sugest[ãa]o)",
    r"sugiro\s+(?:que\s+)?(?:comprar|vender|compre|venda)",
    r"vale\s+(?:a\s+pena\s+)?(?:comprar|vender)",
    r"hora\s+de\s+(?:comprar|vender)",
    r"momento\s+de\s+(?:comprar|vender)",
    r"deve[-\s]?se\s+(?:comprar|vender)",
    r"(?:o\s+)?investidor\s+deveria\s+(?:comprar|vender|montar|entrar|sair)",
    r"aconselho\s+(?:a\s+)?(?:comprar|vender)",
    # Imperativo: "Compre WOLF agora", "Venda antes do balanço".
    r"\b(?:compre|venda|vendam|comprem)\b(?!\s+(?:de|em|no|na)\b)",
    # Condicional de primeira pessoa: "eu compraria", "eu abriria posição".
    r"\beu\s+(?:compraria|venderia|abriria|montaria|entraria|sairia)\b",
    r"\b(?:compraria|venderia)\b",
    r"melhor\s+(?:comprar|vender|entrar|sair)\b",
    r"(?:boa|[óo]tima)\s+(?:oportunidade|hora)\s+de\s+(?:compra|entrada)",
    r"\b(?:buy|sell|strong\s+buy|strong\s+sell)\b",
    r"montar\s+posi[cç][ãa]o\s+(?:agora|j[áa])",
)
# Meta-frase: o modelo dizendo que NÃO vai recomendar é obediência, não
# violação. Sem esta exceção o validador puniria justamente quem acertou.
# A checagem usa a FRASE inteira como janela: a versão anterior olhava 40
# caracteres antes do match e perdia a negação em frases longas ("Dada a
# análise atual e todos os indicadores, não é hora de comprar").
_META_RECUSA = (r"n[ãa]o\s+(?:vou\s+|cabe\s+|[ée]\s+papel\s+)?recomend",
                r"sem\s+recomenda", r"n[ãa]o\s+[ée]\s+recomenda[cç][ãa]o",
                r"a\s+decis[ãa]o\s+[ée]\s+d[oe]\s+leitor")

# R1/R2/S1/S2 são bandas estatísticas (preço ± reação média a earnings), não
# suporte/resistência do gráfico. Estes padrões rodam sobre o texto em
# minúsculas COM acento, porque sem acento "é" e "e" viram a mesma letra -- e a
# diferença entre "R1 É a resistência" (identificação errada) e "R1 E a
# resistência" (lista de duas coisas) é justamente essa.
_BANDA = r"\b[rs][12]\b"
_NIVEL = (r"\b(?:suportes?|resist[êe]ncias?|pisos?|zonas?\s+de\s+defesa"
          r"|tetos?\s+t[ée]cnicos?|fundos?\s+t[ée]cnicos?)\b")
_LIGA = (r"(?:é|são|era|eram|vira|viram|virou|funciona\w*\s+como"
         r"|atua\w*\s+como|configura\w*[-\s]se\s+como|constitu\w+|form\w+"
         r"|aparec\w+\s+como|serve\w*\s+(?:de|como)|representa\w*"
         r"|equivale\w*\s+a|marca\w*)")
# O vão passou de 40 para 90: em produção o modelo escreve o valor e a fonte
# entre a banda e o predicado ("o S1 (banda de reação) em US$ 357,14 e a MM200
# (US$ 343,15) configuram-se como suportes"), e 40 caracteres não alcançavam.
# `[^.;]` continua barrando a travessia de frase.
_BANDA_VIRA_NIVEL = (rf"{_BANDA}[^.;]{{0,90}}?\s{_LIGA}\s+"
                     rf"(?:o|a|os|as|um|uma|de|do|da)?\s*{_NIVEL}")
_NIVEL_VIRA_BANDA = rf"{_NIVEL}\s+(?:em|de|do|da|no|na)?\s*\(?{_BANDA}"
# A banda ARROLADA entre os níveis: "as resistências imediatas INCLUEM a MM50
# e o R1". O verbo é restrito a "incluir/compreender" de propósito -- com um
# `ser` genérico aqui, "a resistência é a máxima de julho e R1 fica acima"
# (frase que DISTINGUE os dois) viraria apontamento.
_NIVEL_ARROLA_BANDA = (rf"{_NIVEL}[^.;]{{0,30}}?\s(?:inclu\w+|compreend\w+|"
                       rf"abrang\w+|contempl\w+)\s[^.;]{{0,60}}?{_BANDA}")
# Cópula elíptica: "R2 é o teto E S2 O PISO" -- o verbo aparece uma vez só e
# a segunda banda fica colada ao artigo. Aqui NÃO cabe vão nenhum: com o
# `[^.;]{0,40}?` do padrão acima, um artigo solto casaria com qualquer
# "resistência" que aparecesse depois na frase.
_BANDA_ELIPSE = rf"{_BANDA}\s+(?:o|a|os|as)\s+{_NIVEL}"

# O texto que NOMEIA a natureza da banda na mesma frase já disse ao leitor o
# que ela é. "o S1 (banda de reação) ... configura-se como suporte" usa a
# palavra proibida, mas não engana sobre a origem do número -- e acusá-lo
# seria o quarto alarme falso desta checagem em quatro rodadas reais.
_ROTULA_A_BANDA = (r"banda\s+(?:de\s+rea[cç][ãa]o|estat[íi]stica|"
                   r"de\s+volatilidade)|rea[cç][ãa]o\s+m[ée]dia|"
                   r"desvio\s+t[íi]pico")

# Moeda: os ativos são listados nos EUA e o prompt manda não converter. Mas o
# modelo ECOANDO a regra ("não converter para R$") ou citando o câmbio não
# está desobedecendo -- é a mesma armadilha de token-em-vez-de-afirmação.
_R_CIFRAO = r"R\$"
_MOEDA_LEGITIMA = (r"n[ãa]o\s+(?:converter|converta|use|usar)\b",
                   r"nunca\s+(?:converter|converta|use|usar)\b",
                   r"c[âa]mbio", r"d[óo]lar\s+(?:est[áa]|a|em|cotado)",
                   r"USD/BRL", r"em\s+vez\s+de\s+R\$", r"jamais\s+em\s+R\$")

# Níveis de referência que o texto compara com o PREÇO, e como o modelo os
# escreve. O valor vem do mesmo campo que alimenta a lista de níveis do prompt.
_REFERENCIAS = (
    ("MM20",  r"mm\s?20|m[ée]dia\s+(?:m[óo]vel\s+)?de\s+20",
     (("technicals", "sma20"),)),
    ("MM50",  r"mm\s?50|m[ée]dia\s+(?:m[óo]vel\s+)?de\s+50",
     (("snapshot", "sma50"), ("technicals", "sma50"))),
    ("MM200", r"mm\s?200|m[ée]dia\s+(?:m[óo]vel\s+)?de\s+200",
     (("snapshot", "sma200"), ("technicals", "sma200"))),
    ("VWAP",  r"vwap", (("technicals", "vwap"),)),
)

# "está 0,72% ACIMA da MM20" -- a direção afirmada, com o número ao lado.
_DIRECAO = r"(\d+(?:[.,]\d+)?)\s*%\s*(acima|abaixo)\s+d[ao]s?\s+(?:sua\s+)?"

# Verbos que ATRIBUEM um movimento ao balanço. É o que separa "reagiu com X%"
# (atribuição) de "a reação ocorreu em 2026-08-19" (circunstância).
_ATRIBUI_REACAO = r"(?:reag\w+|rea[cç][ãa]o\s+(?:foi|de|veio|saiu|ficou))"

# O nome do run-up escrito por extenso. Quando ele aparece na frase, o texto
# está DISTINGUINDO os dois conceitos, e citar o número do run-up ali é a
# redação que o SYSTEM pede -- não o erro que a checagem procura.
_NOMEIA_O_RUNUP = r"run[-\s]?up|corrida|acumulad\w+\s+antes|antes\s+do\s+balan[cç]o"


def _secao_presente(prosa: str, secao: str) -> bool:
    """O título tem que ser uma LINHA de cabeçalho, não uma substring.

    A versão anterior procurava `"## sintese" in prosa`, o que aceitava
    "## Síntese preliminar descartada" como se fosse a seção pedida e recusava
    "**Síntese**". Aqui o título pode vir com qualquer nível de `#`, em
    negrito, ou numerado -- mas tem que terminar ali."""
    alvo = re.escape(sem_acento(secao))
    padrao = (rf"(?m)^\s*(?:#{{1,6}}\s*|\*\*\s*)?(?:\d+[.)]\s*)?"
              rf"{alvo}\s*(?:\*\*)?\s*:?\s*$")
    return bool(re.search(padrao, sem_acento(prosa)))


def _primeiro_valor(*valores):
    """O primeiro numérico da lista, distinguindo 0.0 de ausente.

    `technicals or snapshot` tratava momentum 0.0 como ausente e caía para o
    outro painel -- checando o texto contra um número que não era o do painel
    técnico. Zero é valor válido de momentum."""
    for v in valores:
        if num_finito(v) is not None:
            return num_finito(v)
    return None


def validar_analise(texto, dados=None) -> list:
    """[{nivel, codigo, mensagem}] -- vazio quando nada destoa.

    "ERRO" é afirmação que o dado CONTRADIZ ou regra dura desobedecida;
    "AVISO" é o que falta declarar. Só ERRO justifica gastar uma retentativa
    de LLM."""
    achados = []

    def add(nivel, codigo, mensagem):
        achados.append({"nivel": nivel, "codigo": codigo, "mensagem": mensagem})

    # ── 0. o texto chegou inteiro? ──────────────────────────────────────────
    #
    # Resposta vazia devolvia [] -- que quem chama lê como "nada destoa".
    # Falha de geração era publicada como análise conferida.
    ok, motivo = texto_utilizavel(texto)
    if not ok:
        add("ERRO", "ANALISE_TEXTO_VAZIO",
            f"{motivo} — não há análise para publicar.")
        return achados

    prosa = sem_blocos_de_codigo(texto)
    prosa_sa = sem_acento(prosa)
    prosa_min = minusculas(prosa)

    # ── 1. as seis seções ───────────────────────────────────────────────────
    faltando = [s for s in SECOES_OBRIGATORIAS if not _secao_presente(prosa, s)]
    if faltando:
        add("ERRO", "ANALISE_SECAO_FALTANDO",
            "faltam as seções: " + ", ".join(faltando)
            + (" — a Síntese é a primeira a se perder quando o texto estica."
               if "Síntese" in faltando else "."))

    # ── 2. moeda ────────────────────────────────────────────────────────────
    #
    # Por FRASE e com antinegação: o modelo escrevendo "não converter para R$"
    # está ecoando a regra, e citar o câmbio para contextualizar é legítimo.
    # Sobre `prosa`, não `prosa_sa`: a versão sem acento é MINÚSCULA e "R$"
    # vira "r$", que o padrão nunca casaria — a checagem ficaria morta.
    for frase in frases(prosa):
        if not re.search(_R_CIFRAO, frase):
            continue
        if any(re.search(p, frase, re.IGNORECASE) for p in _MOEDA_LEGITIMA):
            continue
        add("ERRO", "ANALISE_MOEDA_ERRADA",
            "usa R$ para o preço do ativo — os papéis são listados nos EUA e o "
            f"prompt manda não converter; escreva US$. "
            f"Trecho: “{frase.strip()[:120]}”.")
        break

    # ── 3. momentum anualizado descrito como período ────────────────────────
    #
    # Número certo virando afirmação falsa: `momentumAnnualPct` é taxa
    # ANUALIZADA extrapolada de `lookbackDays`. "106% em 90 dias" é o mesmo
    # número dizendo outra coisa.
    #
    # A frase tem que NOMEAR o momentum. Sem isso, "caiu 106,5% em 3 dias" --
    # descrição legítima de preço que por acaso bate com o valor do campo --
    # virava ERRO. Coincidência numérica não é afirmação errada.
    momentum = _primeiro_valor(caminho(dados, "technicals").get("momentumAnnualPct"),
                               caminho(dados, "snapshot").get("momentumAnnualPct"))
    if momentum is not None:
        for frase in frases(prosa_sa):
            # O texto que JÁ declara a taxa como anualizada está obedecendo.
            if re.search(r"anualizad\w*|ao\s+ano|a\.?a\.?\b", frase):
                continue
            if not cita_numero(frase, abs(momentum), inteiro_ok=True):
                continue
            if re.search(r"\d\s*%\s*(?:em|nos?|durante)\s+\d+\s*"
                         r"(?:dia|preg|seman|m[êe]s|mes)\w*", frase):
                add("ERRO", "ANALISE_MOMENTUM_COMO_PERIODO",
                    f"apresenta o momentum de {momentum}% como variação de um "
                    f"período — o campo é taxa ANUALIZADA extrapolada da "
                    f"janela, não o que o papel fez nela. "
                    f"Trecho: “{frase.strip()[:120]}”.")
                break

    # ── 4. divergência de preço não declarada ───────────────────────────────
    preco = caminho(dados, "precoAtual")
    por_painel = caminho(preco, "porPainel")
    valores = sorted(v for v in (num_finito(x) for x in por_painel.values())
                     if v is not None)
    if num_finito(preco.get("divergenciaPct")) and len(valores) >= 2:
        extremos = (valores[0], valores[-1])
        ausentes = [v for v in extremos
                    if not cita_numero(prosa, v, inteiro_ok=True)]
        if ausentes:
            add("ERRO", "ANALISE_DIVERGENCIA_OMITIDA",
                f"os painéis divergem {preco['divergenciaPct']}% e o texto não "
                f"traz os dois preços (US$ {extremos[0]:.2f} e "
                f"US$ {extremos[1]:.2f}) — sem eles o leitor não sabe qual "
                f"indicador está apoiado em qual preço.")

    # ── 5. recomendação de compra/venda ─────────────────────────────────────
    #
    # A janela de negação é a FRASE inteira, não 40 caracteres: "Dada a análise
    # atual e todos os indicadores, não é hora de comprar" tinha o "não" fora
    # da janela e virava ERRO.
    for frase in frases(prosa_sa):
        if any(re.search(neg, frase) for neg in _META_RECUSA):
            continue
        alvo = next((p for p in _RECOMENDA
                     if re.search(p, frase) and not afirmacao_negada(frase, p)),
                    None)
        if alvo:
            add("ERRO", "ANALISE_RECOMENDACAO",
                "recomenda comprar ou vender — o prompt pede cenários e níveis "
                "de invalidação; a decisão é do leitor. "
                f"Trecho: “{frase.strip()[:120]}”.")
            break

    # ── 6. bandas estatísticas tratadas como nível técnico ──────────────────
    #
    # A primeira versão exigia só CO-OCORRÊNCIA de "R1" e "resistência" na
    # mesma frase -- e o SYSTEM manda o modelo escrever exatamente a distinção,
    # então obedecer e errar davam o mesmo apontamento. A segunda olhava a
    # negação apenas DENTRO do trecho casado, e "não é o suporte R1" casava
    # como "suporte r1", sem o "não". Agora a negação é avaliada na FRASE.
    for frase in frases(prosa_min):
        achou = None
        # A negação é avaliada contra `_NIVEL`, não contra o padrão inteiro:
        # em "S1 e S2 NÃO SÃO suporte" o "não" está no meio da construção, e
        # procurá-lo antes do padrão (que começa na banda) nunca o acharia.
        if afirmacao_negada(frase, _NIVEL) or re.search(_ROTULA_A_BANDA, frase):
            continue
        for padrao in (_BANDA_VIRA_NIVEL, _BANDA_ELIPSE, _NIVEL_VIRA_BANDA,
                       _NIVEL_ARROLA_BANDA):
            m = re.search(padrao, frase)
            if m:
                achou = m
                break
        if achou:
            add("ERRO", "ANALISE_BANDA_COMO_NIVEL_TECNICO",
                "trata R1/R2/S1/S2 como suporte ou resistência do gráfico — "
                "são bandas de volatilidade (preço ± reação média a earnings). "
                f"Trecho: “{achou.group(0).strip()}”.")
            break

    # ── 7. balanço já ocorrido escrito no futuro ────────────────────────────
    #
    # Incidente real (NBIS, 17/08/2026): a janela de run-up engolia o pregão de
    # reação e o texto dizia que o papel "chega esticado ao balanço" -- de um
    # balanço que já tinha acontecido.
    runup = caminho(dados, "reaction", "summary", "runup")
    if runup.get("janela_contem_earnings"):
        pregoes = runup.get("pregoes_desde_earnings", "?")

        # PRESENTE ou FUTURO sobre um balanço que já passou. O lookahead
        # exclui as formas de PASSADO -- "chegou/chegava/chegara esticado"
        # descrevendo eventos HISTÓRICOS é a redação correta, e a primeira
        # versão apontava contra ela (falso positivo do WOLF, 26/08/2026).
        # Sobre `prosa_min` (COM acento) de propósito: sem ele "chegará"
        # (futuro, erro) e "chegara" (mais-que-perfeito, correto) viram a
        # mesma palavra, e excluir uma apagaria a outra.
        if re.search(r"\bcheg(?!ou\b|aram\b|ado\b|ada\b|ados\b|adas\b|ava\b|"
                     r"avam\b|ara\b|aras\b)\w*\s+esticad|"
                     r"\bchega\w*\s+ao\s+balan[çc]o", prosa_min):
            add("ERRO", "ANALISE_BALANCO_NO_FUTURO",
                f"escreve no presente ou futuro sobre um balanço que JÁ ocorreu "
                f"há {pregoes} pregão(ões) — o run-up bruto inclui o próprio "
                f"salto do evento.")

        # O esticamento atribuído ao PRÓXIMO balanço. Foi assim que o erro
        # real escapou em WOLF: "o preço atual está esticado em relação ao
        # PRÓXIMO balanço, pois nos 4 pregões desde o último evento...".
        #
        # As duas metades têm que estar LIGADAS na mesma frase. O braço solto
        # `pr[oó]ximo\s+balan[cç]o` transformava "o próximo balanço sai em
        # novembro" -- frase correta e informativa -- em ERRO.
        for frase in frases(prosa_sa):
            if not re.search(r"pr[oó]xim\w+\s+(?:balan[cç]o|resultado|evento)|"
                             r"balan[cç]o\s+que\s+vem", frase):
                continue
            if re.search(r"esticad\w*|run[-\s]?up|descontad\w*", frase):
                add("ERRO", "ANALISE_ESTICAMENTO_NO_PROXIMO_BALANCO",
                    f"pendura o esticamento no PRÓXIMO balanço, mas ele vem do "
                    f"que ocorreu há {pregoes} pregão(ões) — use "
                    f"`runup_atual_ex_evento_pct` e escreva no passado. "
                    f"Trecho: “{frase.strip()[:120]}”.")
                break

    # ── 8. direção invertida contra média móvel ─────────────────────────────
    #
    # Incidente real (ADI, 26/08/2026), publicado sem nenhum apontamento: "o
    # preço de US$ 373,66 está apenas 0,72% ACIMA da MM20 (US$ 376,36)". A
    # magnitude estava certa e o sinal invertido -- 373,66 é MENOR que 376,36.
    # A frase até se contradizia sozinha ("acima da MM20, MAS abaixo da MM50",
    # como se fosse contraste), e ainda assim passou: nenhuma checagem olhava
    # a DIREÇÃO, só os números.
    #
    # A guarda contra o falso positivo é a magnitude. Só aponta quando o
    # percentual citado BATE com a distância real entre preço e nível — o que
    # confirma que a frase fala dessa comparação e não de outra. "A MM50 está
    # 12% acima da MM200" não casa, porque 12 não é a distância do PREÇO à
    # MM200, e é assim que o sujeito da frase fica implicitamente checado.
    preco_atual = _primeiro_valor(caminho(dados, "precoAtual").get("valor"),
                                  caminho(dados, "snapshot").get("price"))
    if preco_atual:
        for frase in frases(prosa_sa):
            achado = _direcao_contradita(frase, preco_atual, dados)
            if achado:
                rotulo, citado, dito, valor, real = achado
                add("ERRO", "ANALISE_DIRECAO_INVERTIDA",
                    f"diz que o preço está {citado:.2f}% {dito} da {rotulo} "
                    f"(US$ {valor:.2f}), mas US$ {preco_atual:.2f} está "
                    f"{'acima' if real > 0 else 'abaixo'} dela — magnitude "
                    f"certa, sinal trocado. "
                    f"Trecho: “{frase.strip()[:120]}”.")
                break

    # ── 9. run-up apresentado como reação ───────────────────────────────────
    #
    # O pior erro da rodada de WOLF, e o que nenhum validador pegava: "o papel
    # reagiu com 14,92% de alta", quando 14,92% é o run-up EX-EVENTO e a
    # reação do dia foi -7,53%. Sinal invertido, com cara de fato apurado.
    #
    # Três guardas contra o falso positivo, todas vindas de rodadas reais:
    #   - o número tem que vir com % (senão "a reação foi medida 14,92 horas
    #     depois" casava);
    #   - o verbo tem que ATRIBUIR a reação, não só mencioná-la ("a reação
    #     ocorreu em 2026-08-19" é circunstância);
    #   - a frase não pode NOMEAR o run-up -- "Reação foi -7,53% após run-up de
    #     14,92%" é exatamente a redação que o SYSTEM pede.
    ex_evento = num_finito(runup.get("runup_atual_ex_evento_pct"))
    if ex_evento is not None:
        for frase in frases(prosa_sa):
            if not re.search(_ATRIBUI_REACAO, frase):
                continue
            if re.search(_NOMEIA_O_RUNUP, frase):
                continue
            if not re.search(r"\d\s*%", frase):
                continue
            if _cita_percentual(frase, abs(ex_evento)):
                add("ERRO", "ANALISE_RUNUP_COMO_REACAO",
                    f"apresenta o run-up de {ex_evento}% como se fosse a REAÇÃO "
                    f"ao balanço — são coisas opostas: o run-up é o que veio "
                    f"ANTES, e citá-lo como reação pode inverter o sinal do que "
                    f"aconteceu. Trecho: “{frase.strip()[:120]}”.")
                break

    return achados


# Folga entre o percentual escrito e o calculado. O modelo às vezes usa o
# PREÇO como base e às vezes o nível ("3,55% abaixo da MM50" contra os "-3,42%"
# do painel são a mesma distância medida de pontos diferentes), então as duas
# bases contam como acerto de magnitude.
_FOLGA_DE_MAGNITUDE = 0.06


def _bate_a_magnitude(citado: float, preco: float, nivel: float) -> bool:
    for base in (nivel, preco):
        real = abs(preco - nivel) / base * 100
        if abs(citado - real) <= max(_FOLGA_DE_MAGNITUDE, real * 0.02):
            return True
    return False


def _direcao_contradita(frase: str, preco: float, dados) -> tuple | None:
    """(rótulo, citado, direção_dita, valor_do_nível, distância_real) ou None.

    Só devolve quando a magnitude bate E a direção não — ver a nota da
    checagem 8 sobre por que a magnitude é a guarda do sujeito da frase."""
    for rotulo, escrita, campos in _REFERENCIAS:
        for m in re.finditer(_DIRECAO + rf"(?:{escrita})", frase):
            citado = num_finito(m.group(1))
            nivel = _primeiro_valor(*(caminho(dados, secao).get(campo)
                                      for secao, campo in campos))
            if citado is None or not nivel:
                continue
            if not _bate_a_magnitude(citado, preco, nivel):
                continue
            real = preco - nivel
            dito_acima = m.group(2) == "acima"
            if dito_acima != (real > 0):
                return rotulo, citado, m.group(2), nivel, real
    return None


def _cita_percentual(frase: str, valor: float) -> bool:
    """O número aparece COMO PERCENTUAL, não como qualquer algarismo solto.

    "a reação foi medida 14,92 horas depois" citava 14,92 e caía; exigir o `%`
    colado separa o dado da coincidência."""
    for g in grafias(valor, inteiro_ok=False):
        if re.search(rf"(?<![\d.,]){re.escape(g)}\s*%", frase):
            return True
    return False


def bloco_de_correcao(achados: list) -> str:
    return _bloco(achados, "mantendo as seções e o tamanho pedidos")
