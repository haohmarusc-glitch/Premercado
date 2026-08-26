"""
Validador da leitura da cesta -- o texto pode afirmar só o que o número banca.

Por que este arquivo existe. Em 25/08/2026 a análise estatística de reação a
earnings foi auditada e três vícios de medição foram corrigidos. Os NÚMEROS
ficaram certos: a correlação do AVGO passou a sair com `p corrigido = 0,462` e
`corr_sobrevive = False`. Mas a prosa gerada em cima deles continuava livre, e
foi por ali que o erro chegou ao leitor -- a leitura chamou aquela mesma
correlação de "padrão estatisticamente relevante" e a transformou na
recomendação principal.

O que este módulo NÃO faz: reescrever o texto ou escondê-lo. Texto suprimido
vira tela vazia e ninguém aprende nada. Ele DECLARA o que não fecha, e quem
chama decide -- hoje: uma retentativa com os apontamentos na mão e, se
persistir, publicação COM os avisos ao lado.

O SYSTEM do gerador já proíbe quase tudo o que é checado aqui. A lição do dia
é justamente essa: regra no prompt sem conferência é sugestão.

REESCRITA DE 26/08/2026. Uma auditoria dos dois validadores reproduziu 39
defeitos, e a maioria era a mesma lição aplicada em um lugar e não no outro:
casar TOKEN em vez de AFIRMAÇÃO. Sete checagens desta tela apontavam contra o
modelo OBEDECENDO o SYSTEM -- "a correlação NÃO é um padrão relevante", "AVGO
nem sempre sobe", "os dois NÃO são o mesmo trade". As primitivas comuns (e a
antinegação) moraram em `validador_nucleo`; o que sobrou aqui é o que é
específico desta tela.

CONTRATO DE ENTRADA
  texto        prosa gerada pelo modelo.
  resultados   [{ticker, error?, summary:{n_events, runup:{...}}}]
  correlacoes  dicionário PLANO {"AVGO|SMCI": 0.51}. A chave nomeia o PAR e o
               valor é a correlação. Aninhado ({"AVGO": {"SMCI": .91}}) era
               lido como "nenhuma correlação medida" e virava ERRO -- por isso
               agora é achatado na entrada em vez de ignorado em silêncio.
"""
import re

from .validador_nucleo import (afirmacao_negada, booleano, caminho, cita_numero,
                               dic, frases, num_finito, sem_acento,
                               sem_blocos_de_codigo, texto_do_numero,
                               texto_utilizavel)
from .validador_nucleo import (avisos, bloco_de_correcao as _bloco,  # noqa: F401
                               erros, linha_de_log, resumo_legivel)

# Palavras que transformam observação em lei. Com ~8 eventos por ticker nada é
# lei. O SYSTEM já as proíbe nominalmente; ficam aqui porque são baratas e
# porque a próxima leitura pode ser menos cuidadosa.
_PALAVRAS_DE_LEI = (
    "sempre", "toda vez", "todas as vezes", "invariavelmente", "com certeza",
    "garantido", "garante", "certamente", "sem exceção", "nunca falha",
)

# "sempre que" é conjunção ("sempre que possível, declare o n"), não afirmação
# de invariância. Sem esta exceção, orientação metodológica virava ERRO.
_LEI_QUE_NAO_E_LEI = r"sempre\s+que\b"

# Uma palavra de lei só é ERRO quando PREDICA um papel ou um movimento. Numa
# frase sem ticker e sem verbo de mercado ("sempre que possível, declare o n")
# ela é discurso sobre o método, não afirmação sobre o ativo.
_VERBO_DE_MERCADO = (r"\b(?:sob\w+|cai\w*|caiu|desab\w+|dispar\w+|revert\w+|"
                     r"reag\w+|rea[cç][aã]o|fech\w+|abr\w+|salt\w+|recu\w+)")

# Afirmações que promovem correlação a padrão. É a família exata do incidente:
# "é um padrão estatisticamente relevante", "indicando um padrão de reversão".
# Afirmacoes INFERENCIAIS: dizem que o numero vale como regra, previsao ou
# achado com suporte. Sao o incidente original -- "indicando um padrao de
# reversao. E' um padrao estatisticamente relevante" com p corrigido 0,462.
_AFIRMA_SIGNIFICANCIA = (
    r"estatisticamente\s+relevante", r"estatisticamente\s+significativ\w*",
    r"padr[ãa]o\s+de\s+revers[ãa]o", r"padr[ãa]o\s+consistente",
    r"sinal\s+confi[áa]vel", r"rela[cç][ãa]o\s+robusta",
)

# Afirmacao DESCRITIVA: "forte" qualifica a MAGNITUDE de r, nao a confianca
# nele. |r| > 0,7 e' "forte" em qualquer livro de estatistica -- dizer isso e'
# ler o numero, nao promove-lo.
#
# Duas geracoes da mesma tela, em 26/08/2026, com a MESMA afirmacao:
#
#   "correlacao positiva forte (0,92)"  -> passava
#   "forte correlacao positiva (0.92)"  -> ERRO
#
# So' a ordem do adjetivo mudava. Uma checagem cujo veredito depende de onde
# o adjetivo caiu na frase nao esta medindo sentido nenhum. As duas ordens
# agora casam igual -- e as duas so' caem quando a frase deixa o numero SOLTO.
#
# Porque a saida nao foi simplesmente apagar a familia: "NVDA tem forte
# correlacao entre run-up e reacao", sem dizer sobre quantos eventos, engana
# de verdade. O que desarma o engano e' o n. Com ele declarado, quem le tem o
# que precisa para calibrar sozinho -- e e' o que o proprio SYSTEM pede.
_MAGNITUDE_DA_CORRELACAO = (
    r"correla[cç][ãa]o\s+(?:\w+\s+)?forte", r"forte\s+(?:\w+\s+)?correla[cç][ãa]o",
)

# A frase ancora o numero na amostra (ou ja' se defende sozinha).
_DECLARA_AMOSTRA = (
    r"\b(?:apenas\s+)?\d+\s+(?:eventos?|balan[çc]os?|casos?|resultados?|"
    r"observa[çc][õo]es|amostras?|trimestres?)\b"
    r"|\bn\s*=\s*\d+"
    r"|\bamostra\s+(?:pequena|reduzida|limitada|curta)"
    r"|\bpoucos\s+eventos\b|\bind[íi]cio\b|\bn[ãa]o\s+[ée]\s+prova\b"
)

_PROMOVE_A_PADRAO = _AFIRMA_SIGNIFICANCIA + _MAGNITUDE_DA_CORRELACAO

# Limite acima do qual dois papéis são "na prática o mesmo trade" -- o mesmo
# número que o SYSTEM ensina ao modelo. Abaixo dele a frase é forte demais.
CORR_MESMO_TRADE = 0.70

# "praticamente o mesmo" sozinho é frouxo demais: "AVGO teve praticamente o
# mesmo desempenho da MÉDIA DO SETOR" não afirma co-movimento entre dois
# papéis da cesta. Por isso a checagem exige, além destas marcas, que a frase
# cite DOIS tickers analisados.
_MESMO_TRADE = (r"mesmo\s+trade", r"praticamente\s+(?:o\s+)?mesm\w+",
                r"na\s+pr[áa]tica\s+(?:o\s+)?mesm\w+", r"id[êe]nticos",
                r"perfeita\s+sincronia", r"perfeitamente\s+correlacionados",
                r"andam\s+sempre\s+juntos")

# Anáfora plural: "os dois são o mesmo trade" não nomeia ninguém, mas refere
# DUAS coisas sem ambiguidade -- exigir os tickers escritos aqui seria trocar
# um falso positivo por um falso negativo.
_ANAFORA_DE_PAR = r"\b(?:os\s+dois|as\s+duas|ambos|ambas|os\s+pares?)\b"

# Abaixo disso o SYSTEM manda declarar o n ao citar o papel.
N_EVENTOS_DECLARAR = 5

_ESTADOS = ("esticado", "descontado")

# O rotulo so' CONTRADIZ o dado quando a frase o ATRIBUI ao papel. Duas frases
# reais de 26/08/2026 caíram como ERRO sem afirmar estado nenhum:
#
#   "...embora com apenas 1 evento em cada bucket de esticado/descontado."
#   "...com 3 dos 3 casos esticados reagindo negativamente em media -9,67%."
#
# Nas duas o rotulo nomeia um BALDE HISTORICO -- quantos balanços passados
# chegaram naquele estado --, não o estado de hoje. Pior: no mesmo texto o
# modelo dizia, corretamente, que NVDA e AVGO estão "neutro". Dois ERROs
# vermelhos num texto certo custam mais que o acerto que a checagem entrega:
# ensinam o leitor a ignorar a caixa amarela inteira.
#
# Por isso a checagem exige um ATRIBUIDOR colado ao rotulo -- copula, verbo de
# permanencia ou marca de classificação. "chegou/chega esticado" ficou DE FORA
# de proposito: e' a forma com que o proprio card descreve o balde histórico
# ("Padrão 'chegou esticado': em 0 de 1 balanços..."), e o modelo repete essa
# frase. Perder "ARM chega esticado ao balanço" e' um falso negativo barato.
_ATRIBUI_ESTADO = (
    r"est[áa]|est[ãa]o|estava|estavam|"
    r"continua|continuam|permanece|permanecem|segue|seguem|"
    r"encontra-se|encontram-se|fica|ficam|aparece|aparecem|"
    r"classificad[oa]s?\s+como|categorizad[oa]s?\s+como|"
    r"considerad[oa]s?\s+como|marcad[oa]s?\s+como|"
    r"na\s+categoria(?:\s+de)?|no\s+estado(?:\s+de)?|estado(?:\s+atual)?\s+de|"
    r"[\u2192>]"
)

# O vao entre o atribuidor e o rotulo NAO atravessa pontuacao de clausula --
# mesma regra de `afirmacao_negada`, pelo mesmo motivo. Em "AVGO esta neutro,
# longe de esticado ou descontado" a virgula corta a cadeia e "esticado" nao e'
# lido como atribuido. Aspas cabem no vao: 'na categoria "esticado"'.
#
# Dois-pontos ficam DE FORA da lista de cortes: em "Estado atual de NVDA:
# esticado" o ':' nao separa duas afirmacoes, ele introduz o valor atribuido --
# e' a forma canonica de rotular, nao uma fronteira.
_VAO_ATE_O_ROTULO = r"[^,;.!?\n]{0,24}?"


def _estado_atribuido(frase: str, rotulo: str) -> bool:
    """A frase ATRIBUI `rotulo` ao papel, em vez de so' mencionar a palavra."""
    return bool(re.search(rf"(?:{_ATRIBUI_ESTADO}){_VAO_ATE_O_ROTULO}{rotulo}",
                          frase, re.IGNORECASE))


def _trechos_do_ticker(texto: str, ticker: str) -> list:
    """As frases que citam o ticker. A checagem é por FRASE, não pelo texto
    inteiro: "AVGO reverte" e "SMCI é um padrão relevante" no mesmo parágrafo
    não podem contaminar um ao outro."""
    alvo = sem_acento(ticker)
    return [f for f in frases(sem_acento(texto))
            if re.search(rf"\b{re.escape(alvo)}\b", f)]


def _resumo_por_ticker(resultados) -> dict:
    saida = {}
    for r in resultados or []:
        if not isinstance(r, dict):
            continue
        tk = str(r.get("ticker") or "").strip().upper()
        if tk:
            saida[tk] = r
    return saida


def _correlacoes_planas(correlacoes) -> dict:
    """Aceita {"A|B": 0.5} e também {"A": {"B": 0.5}}, e devolve sempre a forma
    plana com valores finitos.

    O formato aninhado fazia `values()` devolver dicionários, nenhum passava em
    `isinstance(v, (int, float))`, e a checagem concluía "nenhuma correlação
    medida" -- ERRO inventado a partir de um payload que trazia o dado."""
    plano = {}
    for chave, valor in dic(correlacoes).items():
        if isinstance(valor, dict):
            for outro, v in valor.items():
                f = num_finito(v)
                if f is not None:
                    plano[f"{chave}|{outro}"] = f
        else:
            f = num_finito(valor)
            if f is not None:
                plano[str(chave)] = f
    return plano


def validar_leitura(texto, resultados, correlacoes=None) -> list:
    """[{nivel, codigo, mensagem, ticker}] -- vazio quando nada destoa.

    `nivel` é "ERRO" para afirmação que o dado CONTRADIZ e "AVISO" para
    afirmação que o dado não sustenta nem nega. A distinção importa porque só
    o primeiro justifica gastar uma retentativa de LLM."""
    achados = []

    def add(nivel, codigo, mensagem, ticker=None):
        achados.append({"nivel": nivel, "codigo": codigo,
                        "mensagem": mensagem, "ticker": ticker})

    # ── 0. o texto chegou inteiro? ──────────────────────────────────────────
    #
    # Antes desta checagem, resposta vazia devolvia [] -- que quem chama lê
    # como "nada destoa". Falha de geração era publicada como texto aprovado.
    ok, motivo = texto_utilizavel(texto)
    if not ok:
        add("ERRO", "LEITURA_TEXTO_VAZIO",
            f"{motivo} — não há leitura para publicar.")
        return achados

    prosa = sem_blocos_de_codigo(texto)
    prosa_sa = sem_acento(prosa)
    por_ticker = _resumo_por_ticker(resultados)
    tickers = set(por_ticker)
    pares = _correlacoes_planas(correlacoes)

    def _tickers_na_frase(frase_sa: str) -> set:
        return {tk for tk in tickers
                if re.search(rf"\b{re.escape(sem_acento(tk))}\b", frase_sa)}

    # ── 1. lei absoluta a partir de ~8 eventos ──────────────────────────────
    #
    # Um ERRO por texto, não um por palavra: três "sempre" numa retentativa
    # devolviam três apontamentos idênticos e enchiam o bloco de correção.
    for frase in frases(prosa_sa):
        if re.search(_LEI_QUE_NAO_E_LEI, frase):
            continue
        # Discurso sobre o método não é afirmação sobre o ativo.
        if not (_tickers_na_frase(frase) or re.search(_VERBO_DE_MERCADO, frase)):
            continue
        achou = None
        for palavra in _PALAVRAS_DE_LEI:
            alvo = rf"\b{re.escape(sem_acento(palavra))}\b"
            if re.search(alvo, frase) and not afirmacao_negada(frase, alvo):
                achou = palavra
                break
        if achou:
            add("ERRO", "LEITURA_LEI_ABSOLUTA",
                f"usa '{achou}' como lei — com ~8 eventos por ticker nada é "
                f"invariável; o SYSTEM pede 'tem tendido a'. "
                f"Trecho: “{frase.strip()[:120]}”.")
            break

    # ── 2. correlação promovida a padrão sem sobreviver ao Holm ─────────────
    #
    # O incidente, literalmente: "a correlação negativa de -0.60 para AVGO
    # (...) é um padrão estatisticamente relevante". p corrigido = 0,462.
    for tk, r in por_ticker.items():
        ru = caminho(r, "summary", "runup")
        if num_finito(ru.get("corr_runup_reacao")) is None:
            continue
        if booleano(ru.get("corr_sobrevive")):
            continue
        for frase in _trechos_do_ticker(prosa, tk):
            for alvo in _PROMOVE_A_PADRAO:
                if not re.search(alvo, frase):
                    continue
                # NEGAR a promoção é obediência ao SYSTEM, não erro.
                if afirmacao_negada(frase, alvo):
                    continue
                # Descrever a magnitude DIZENDO sobre quantos eventos não é
                # promover -- é o mesmo que o card faz na própria tela.
                if (alvo in _MAGNITUDE_DA_CORRELACAO
                        and re.search(_DECLARA_AMOSTRA, frase)):
                    continue
                pc = ru.get("corr_p_corrigido")
                add("ERRO", "LEITURA_CORRELACAO_SEM_SUPORTE",
                    f"trata a correlação de {tk} como padrão, mas ela não "
                    f"sobrevive à correção de múltiplos tickers"
                    + (f" (p corrigido {texto_do_numero(pc)})"
                       if pc is not None else "")
                    + f". Trecho: “{frase.strip()[:120]}”.", ticker=tk)
                break
            else:
                continue
            break

    # ── 3. estado de run-up contradito pelo dado ────────────────────────────
    for tk, r in por_ticker.items():
        ru = caminho(r, "summary", "runup")
        estado = sem_acento(ru.get("estado_atual") or "").strip()
        if not estado:
            continue
        for frase in _trechos_do_ticker(prosa, tk):
            for rotulo in _ESTADOS:
                if estado == rotulo or not re.search(rf"\b{rotulo}", frase):
                    continue
                if afirmacao_negada(frase, rotulo):
                    continue  # o texto está NEGANDO o rótulo -- é obediência
                if not _estado_atribuido(frase, rotulo):
                    continue  # a palavra nomeia um balde histórico, não o papel
                add("ERRO", "LEITURA_ESTADO_CONTRADITO",
                    f"diz que {tk} está '{rotulo}', mas o dado do dia "
                    f"marca '{estado}'.", ticker=tk)
                break

    # ── 4. co-movimento afirmado forte demais ───────────────────────────────
    #
    # Exige DOIS tickers da cesta na mesma frase: "AVGO teve praticamente o
    # mesmo desempenho da média do setor" não afirma co-movimento entre papéis.
    for frase in frases(prosa_sa):
        na_frase = _tickers_na_frase(frase)
        if len(na_frase) < 2 and not re.search(_ANAFORA_DE_PAR, frase):
            continue
        marca = next((a for a in _MESMO_TRADE
                      if re.search(a, frase) and not afirmacao_negada(frase, a)),
                     None)
        if not marca:
            continue
        do_par = [v for chave, v in pares.items()
                  if len(na_frase & {p.strip().upper()
                                     for p in re.split(r"[|/,\s-]+", chave)}) >= 2]
        relevantes = do_par or list(pares.values())
        quem = " e ".join(sorted(na_frase)) or "os papéis citados"
        if not relevantes:
            add("ERRO", "LEITURA_COMOVIMENTO_SEM_DADO",
                f"afirma que {quem} são o mesmo trade, mas não há correlação "
                f"medida no dado desta tela.")
        elif max(relevantes) < CORR_MESMO_TRADE:
            add("ERRO", "LEITURA_COMOVIMENTO_FORTE_DEMAIS",
                f"afirma 'mesmo trade' para {quem}, mas a maior correlação "
                f"disponível é {max(relevantes):.2f} "
                f"(< {CORR_MESMO_TRADE:.2f}).")
        break

    # ── 5. ticker sem estatística tratado como analisado ────────────────────
    #
    # Citar é PERMITIDO -- o SYSTEM manda citar como não analisado. O que não
    # pode é citá-lo junto de número que ele não tem. Duas guardas contra o
    # falso positivo: a frase não pode citar outro ticker (o percentual seria
    # dele) e não pode ser justamente a ressalva de que não houve análise.
    for tk, r in por_ticker.items():
        if not r.get("error") or caminho(r, "summary"):
            continue
        for frase in _trechos_do_ticker(prosa, tk):
            if _tickers_na_frase(frase) - {tk}:
                continue
            if re.search(r"n[ãa]o\s+(?:foi|foram|produziu|houve|teve)|"
                         r"sem\s+(?:hist[óo]rico|dado|estat[íi]stica)|"
                         r"n[ãa]o\s+anali[sz]ad", frase):
                continue
            if re.search(r"\d+(?:[,.]\d+)?%", frase):
                add("ERRO", "LEITURA_TICKER_SEM_DADO",
                    f"cita percentual junto de {tk}, que não produziu "
                    f"estatística nesta rodada. "
                    f"Trecho: “{frase.strip()[:120]}”.", ticker=tk)
                break

    # ── 6. amostra curta citada sem declarar o tamanho ──────────────────────
    #
    # A regra do SYSTEM é declarar o N, não dizer uma palavra qualquer. A
    # versão anterior aceitava qualquer ocorrência de "evento" -- e
    # "AVGO teve comportamento diferente no evento anterior" passava sem
    # informar que a amostra era de três.
    for tk, r in por_ticker.items():
        n = caminho(r, "summary").get("n_events")
        if not isinstance(n, int) or isinstance(n, bool) or n >= N_EVENTOS_DECLARAR:
            continue
        trechos = _trechos_do_ticker(prosa, tk)
        if not trechos:
            continue
        declarou = any(
            cita_numero(f, n, inteiro_ok=True)
            and re.search(r"\b(?:evento|observa|ocasi|caso|amostra|result|"
                          r"balan[cç]o|trimestre)", f)
            or (n == 1 and re.search(r"\b(?:um\s+[úu]nico|[úu]nico|uma\s+[úu]nica|"
                                     r"apenas\s+um)\b", f))
            for f in trechos)
        if not declarou:
            add("AVISO", "LEITURA_AMOSTRA_CURTA_OMITIDA",
                f"cita {tk} sem declarar que a amostra é de {n} evento(s) — o "
                f"SYSTEM pede o número abaixo de {N_EVENTOS_DECLARAR}.",
                ticker=tk)

    return achados


def bloco_de_correcao(achados: list) -> str:
    return _bloco(achados, "mantendo o tamanho pedido")
