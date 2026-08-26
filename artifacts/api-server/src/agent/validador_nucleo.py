"""
Núcleo comum dos validadores de prosa gerada por LLM.

Por que este arquivo existe. Havia três validadores (`veredito_validator`,
`reacao_earnings_validator`, `analise_rapida_validator`) com cópias próprias de
`_sem_acento`, `_sem_blocos_de_codigo`, `erros`, `bloco_de_correcao` e
`resumo_legivel`. A divergência não era hipótese: em 26/08/2026 a análise
rápida ganhou antinegação em bandas e recomendação, e a leitura da cesta ficou
sem -- então "R1 não é resistência" passava numa tela e "a correlação NÃO é um
padrão relevante" era recusada na outra. Uma auditoria dos dois arquivos
reproduziu 39 defeitos, e a maioria era a mesma lição aplicada em um lugar e
não no outro.

A LIÇÃO, escrita uma vez só. Um validador de prosa tem que casar AFIRMAÇÃO,
não TOKEN. As três formas do mesmo erro, todas vistas em produção:

  1. substring sem fronteira    -- "1" casava dentro de "1,38", "21" e "2026"
  2. co-ocorrência sem predicado -- "R1" + "resistência" na mesma frase virava
                                    erro, inclusive quando a frase os DISTINGUIA
  3. token sem negação          -- "não é um padrão relevante" era lido como
                                    afirmação de que é um padrão relevante

O custo de errar para cada lado NÃO é simétrico, e é isso que decide o desenho:
um falso NEGATIVO deixa passar um erro que o leitor talvez perceba; um falso
POSITIVO ensina o leitor a ignorar a caixa amarela, e aí todos os achados
seguintes morrem junto. Por isso as primitivas daqui são conservadoras na
acusação e explícitas na dúvida.
"""
import math
import re
import unicodedata

# ── normalização ────────────────────────────────────────────────────────────


def sem_acento(texto) -> str:
    """Minúsculas sem diacrítico, para casar 'reação' com 'reacao'.

    CUIDADO ao escolher entre esta e `minusculas`: sem acento "é" e "e" viram
    a mesma letra, e a diferença entre "R1 É a resistência" (identificação
    errada) e "R1 E a resistência" (lista de duas coisas) é justamente essa.
    Checagem que depende de cópula tem que rodar sobre `minusculas`."""
    return "".join(c for c in unicodedata.normalize("NFD", str(texto or ""))
                   if unicodedata.category(c) != "Mn").lower()


def minusculas(texto) -> str:
    """Minúsculas COM acento -- ver a ressalva em `sem_acento`."""
    return str(texto or "").lower()


def sem_blocos_de_codigo(texto) -> str:
    """Tira ```blocos``` e `spans` antes do lint.

    Número e palavra-chave dentro de bloco de código são dado CITADO, não
    afirmação -- e casar ali produziria apontamento contra o JSON que o próprio
    sistema imprimiu."""
    texto = re.sub(r"```.*?```", " ", str(texto or ""), flags=re.DOTALL)
    return re.sub(r"`[^`\n]*`", " ", texto)


def frases(texto: str) -> list:
    """Quebra em frases. A checagem é por FRASE em quase tudo: "AVGO reverte" e
    "SMCI é um padrão relevante" no mesmo parágrafo não podem contaminar um ao
    outro."""
    return [f for f in re.split(r"(?<=[.!?])\s+|\n+", str(texto or "")) if f.strip()]


# ── o texto chegou inteiro? ─────────────────────────────────────────────────

# Piso de referência para quem QUER checar truncamento. Não é aplicado
# automaticamente: ver a ressalva em `texto_utilizavel`.
MIN_CARACTERES_UTEIS = 200


def texto_utilizavel(texto, minimo: int = 0) -> tuple:
    """(ok, motivo). Falso quando a "análise" não é análise nenhuma.

    Este era o buraco mais perigoso dos dois validadores: resposta vazia,
    timeout convertido em string vazia ou resposta contendo APENAS um bloco de
    código devolviam lista vazia de achados -- que quem chama lê como "nada
    destoa". Falha de geração era publicada como texto aprovado.

    `minimo` é OPT-IN de propósito. Recusar-se a validar um texto curto é
    recusar-se a encontrar os erros que ele tem, e "curto demais" depende do
    que foi PEDIDO -- conhecimento do gerador, não do validador de prosa. Quem
    sabe o tamanho encomendado passa o piso; o validador, por si, só barra o
    que é lixo inequívoco."""
    if texto is None:
        return False, "o gerador não devolveu texto (None)"
    if not isinstance(texto, str):
        return False, f"o gerador devolveu {type(texto).__name__}, não texto"
    if not texto.strip():
        return False, "o gerador devolveu texto vazio"
    util = sem_blocos_de_codigo(texto).strip()
    if not util:
        return False, "a resposta tem só bloco de código, sem prosa nenhuma"
    if minimo and len(util) < minimo:
        return False, (f"a resposta tem {len(util)} caracteres de prosa (mínimo "
                       f"{minimo}) — é truncamento, não análise")
    return True, ""


# ── números ─────────────────────────────────────────────────────────────────


def num_finito(valor):
    """O float por trás do valor, ou None quando não dá para confiar.

    Recusa `bool` (True viraria 1.0 e entraria em max()), NaN e infinito. NaN
    era o caso silencioso: `max([nan]) < 0.70` é False, então a checagem de
    co-movimento simplesmente não apontava."""
    if isinstance(valor, bool) or valor is None:
        return None
    if isinstance(valor, (int, float)):
        f = float(valor)
        return f if math.isfinite(f) else None
    if isinstance(valor, str):
        # "1.234,56" (pt-BR) e "1,234.56" (en-US) chegam de payload humano.
        t = valor.strip().replace("US$", "").replace("$", "").replace("%", "").strip()
        if not t:
            return None
        if "," in t and "." in t:
            t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") \
                else t.replace(",", "")
        elif "," in t:
            t = t.replace(",", ".")
        try:
            f = float(t)
        except ValueError:
            return None
        return f if math.isfinite(f) else None
    return None


def grafias(valor: float, inteiro_ok: bool) -> list:
    """As grafias em que um número pode legitimamente aparecer no texto.

    O modelo escreve em pt-BR ("US$ 225,01"), o JSON traz 225.01 e um payload
    ocasional vem em en-US ("1,000.50") -- checar só uma das formas apontaria
    contra texto correto.

    `inteiro_ok` separa dois usos que NÃO podem compartilhar a mesma régua.
    Para PREÇO, "US$ 180" é escrita legítima de 180,00 e o inteiro precisa
    entrar. Para PERCENTUAL pequeno, não: arredondar 1,38% para "1" transforma
    a checagem em coringa -- foi o falso positivo do ADI (26/08/2026), em que
    qualquer frase com verbo de reação e um algarismo 1 virava apontamento."""
    v = num_finito(valor)
    if v is None:
        return []
    br = f"{v:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")
    saida = [f"{v:.2f}", f"{v:.2f}".replace(".", ","), br,
             f"{v:,.2f}",                      # en-US: 1,000.50
             f"{v:.1f}", f"{v:.1f}".replace(".", ",")]
    if inteiro_ok:
        saida.append(f"{int(round(v))}")
    vistos, unicas = set(), []
    for g in saida:
        if g not in vistos:
            vistos.add(g)
            unicas.append(g)
    return unicas


def cita_numero(texto: str, valor, *, inteiro_ok: bool = False) -> bool:
    """O texto cita este número, com FRONTEIRA DOS DOIS LADOS.

    Substring era o defeito de origem: "1" casava dentro de "1,38", de "21" e
    até de "2026". A primeira correção bloqueou dígito, ponto e vírgula só à
    ESQUERDA -- e com isso "180" ainda casava dentro de "180,75", fazendo a
    checagem de divergência concluir que o preço tinha sido declarado quando
    o texto trazia outro número. Agora os dois lados são simétricos."""
    if not texto:
        return False
    for g in grafias(valor, inteiro_ok):
        if re.search(rf"(?<![\d.,]){re.escape(g)}(?![\d.,]\d)(?!\d)", texto):
            return True
    return False


# ── negação: a primitiva que faltava em quase toda checagem ─────────────────

# Marcas de que a frase NEGA, DISTANCIA ou CITA A REGRA em vez de afirmar. O
# SYSTEM manda o modelo escrever várias dessas distinções ("R1 não é
# resistência", "isto não é recomendação"), então puni-las é punir obediência.
_NEGACAO = (r"n[ãa]o|nem|jamais|nunca|sem\s+que|longe\s+de|deixou\s+de|"
            r"deixaram\s+de|ao\s+contr[áa]rio|diferente\s+de|tampouco|"
            r"evite|evitar|nada|n\.?d\.?a")

# Quantas palavras cabem entre a negação e o alvo. Três, porque o alvo costuma
# ser o fim de um sintagma nominal -- "não é um padrão estatisticamente
# relevante" tem "é", "um" e "padrão" no meio. Janela larga viraria mordaça:
# bastaria um "não" em qualquer lugar da frase para calar a checagem inteira.
PALAVRAS_DE_DISTANCIA = 3

# O vão não atravessa PONTUAÇÃO. É o que segura a janela de três: em "não está
# neutro, está descontado" a vírgula corta a cadeia, então "descontado" segue
# AFIRMADO e continua caindo -- que é o comportamento certo, porque a frase
# nega um rótulo e afirma o outro.
_PALAVRA_SEM_PONTUACAO = r"[^\s,;:.!?]+"


def afirmacao_negada(frase: str, alvo: str,
                     palavras: int = PALAVRAS_DE_DISTANCIA) -> bool:
    """A frase NEGA `alvo` (regex) em vez de afirmá-lo.

    Só vale COLADO e sem atravessar pontuação. É a diferença entre calar um
    falso positivo e criar um falso negativo: com janela ilimitada, "não
    recomendo olhar o gráfico, mas é hora de comprar" escaparia da checagem de
    recomendação."""
    if not frase or not alvo:
        return False
    vao = rf"(?:\s+{_PALAVRA_SEM_PONTUACAO}){{0,{palavras}}}"
    return bool(re.search(rf"(?:{_NEGACAO}){vao}\s+(?:{alvo})", frase, re.IGNORECASE))


def afirmado_sem_negacao(frase: str, alvo: str,
                         palavras: int = PALAVRAS_DE_DISTANCIA):
    """O match de `alvo` na frase, ou None quando ele não está lá ou está
    negado. É o par que quase toda checagem quer: achar a afirmação e já
    descartar quem a estava negando."""
    m = re.search(alvo, frase or "", re.IGNORECASE)
    if not m or afirmacao_negada(frase, alvo, palavras):
        return None
    return m


# ── acesso a payload que pode vir torto ─────────────────────────────────────


def dic(valor) -> dict:
    """O dicionário, ou um vazio. Encadear `.get()` sem isto estourava a
    validação inteira quando `summary` vinha como string -- e validador que
    morre com payload torto não protege publicação nenhuma."""
    return valor if isinstance(valor, dict) else {}


def caminho(raiz, *chaves) -> dict:
    """`caminho(dados, "reaction", "summary", "runup")` sem AttributeError em
    nenhum nível."""
    atual = dic(raiz)
    for c in chaves:
        atual = dic(atual.get(c))
    return atual


def booleano(valor) -> bool:
    """True só para booleano de verdade ou para as grafias textuais dele.

    `bool("false")` é True em Python, e era assim que uma correlação com
    `corr_sobrevive: "false"` no JSON passava por sobrevivente -- justamente a
    afirmação estatística que a checagem existe para barrar."""
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, str):
        return valor.strip().lower() in ("true", "1", "sim", "yes")
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return bool(valor)
    return False


def texto_do_numero(valor, casas: int = 3) -> str:
    """Formata para a mensagem sem estourar quando o payload manda string.
    `f"{pc:.3f}"` com `pc = "0.462"` levantava ValueError DENTRO do validador,
    no meio de reportar um achado."""
    f = num_finito(valor)
    return f"{f:.{casas}f}" if f is not None else str(valor)


# ── contrato de saída, compartilhado pelos validadores ──────────────────────


def erros(achados: list) -> list:
    """Só os ERROS. É o que justifica gastar uma retentativa de LLM."""
    return [a for a in (achados or []) if a.get("nivel") == "ERRO"]


def avisos(achados: list) -> list:
    return [a for a in (achados or []) if a.get("nivel") == "AVISO"]


def bloco_de_correcao(achados: list, contexto: str = "") -> str:
    """O texto que volta ao modelo na retentativa.

    Só os ERROS: mandar o modelo corrigir um AVISO gastaria uma rodada de LLM
    para uma ressalva que o leitor lê sozinho no aviso publicado.

    Quem chama passa isto como MENSAGEM SEPARADA, nunca concatenado ao payload
    de dados -- a primeira versão concatenava e estourava `MAX_DADOS_CHARS`."""
    duros = erros(achados)
    if not duros:
        return ""
    linhas = [f"- {a['mensagem']}" for a in duros]
    return ("\n\nA versão anterior deste texto foi recusada pelo validador "
            "pelos motivos abaixo. Reescreva corrigindo CADA um, sem inventar "
            "número novo" + (f" e {contexto}" if contexto else "") + ":\n"
            + "\n".join(linhas))


def resumo_legivel(achados: list) -> list:
    """Uma linha por apontamento, para a tela e para o stderr."""
    saida = []
    for a in achados or []:
        alvo = a.get("ticker")
        saida.append(f"[{a.get('nivel')}] {a.get('codigo')}"
                     + (f" ({alvo})" if alvo else "")
                     + f": {a.get('mensagem')}")
    return saida


def linha_de_log(nome: str, achados: list) -> str:
    """UMA linha, sempre -- inclusive com zero achados.

    Silêncio e "não rodou" eram indistinguíveis no log, e essa ambiguidade já
    custou duas rodadas de diagnóstico: sem linha nenhuma não dá para saber se
    o validador aprovou o texto ou se nem chegou a ser chamado."""
    n_erros, n_avisos = len(erros(achados)), len(avisos(achados))
    if not achados:
        return f"[validador:{nome}] limpo — nenhum apontamento"
    return (f"[validador:{nome}] {n_erros} erro(s), {n_avisos} aviso(s): "
            + "; ".join(a.get("codigo", "?") for a in achados))
