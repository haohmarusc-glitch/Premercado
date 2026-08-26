"""
Validador da leitura da cesta -- o texto pode afirmar só o que o número banca.

Por que este arquivo existe. Em 25/08/2026 a análise estatística de reação a
earnings foi auditada e três vícios de medição foram corrigidos. Os NÚMEROS
ficaram certos: a correlação do AVGO passou a sair com `p corrigido = 0,462` e
`corr_sobrevive = False`. Mas a prosa gerada em cima deles continuava livre, e
foi por ali que o erro chegou ao leitor -- a leitura chamou aquela mesma
correlação de "padrão estatisticamente relevante" e a transformou na
recomendação principal.

O Veredito já tinha esse anteparo (`veredito_validator.py`); esta tela não.
A diferença de resultado foi exatamente essa.

O que este módulo NÃO faz: reescrever o texto ou escondê-lo. Texto suprimido
vira tela vazia e ninguém aprende nada. Ele DECLARA o que não fecha, e quem
chama decide -- hoje: uma retentativa com os apontamentos na mão e, se
persistir, publicação COM os avisos ao lado.

O SYSTEM do gerador já proíbe quase tudo o que é checado aqui. A lição do dia
é justamente essa: regra no prompt sem conferência é sugestão.
"""
import re
import unicodedata

# Palavras que transformam observação em lei. O SYSTEM já as proíbe
# nominalmente ("nunca 'sempre', 'toda vez' ou 'o papel cai quando'"), e a
# leitura de 25/08 não as usou -- o erro dela foi mais sutil. Ficam aqui
# porque são baratas e porque a próxima leitura pode ser menos cuidadosa.
_PALAVRAS_DE_LEI = (
    "sempre", "toda vez", "todas as vezes", "invariavelmente", "com certeza",
    "garantido", "garante", "certamente", "sem exceção", "nunca falha",
)

# Afirmações que promovem correlação a padrão. É a família exata do incidente:
# "é um padrão estatisticamente relevante", "indicando um padrão de reversão".
_PROMOVE_A_PADRAO = (
    "estatisticamente relevante", "estatisticamente significativ",
    "padrao de reversao", "padrao de reversão", "padrão de reversão",
    "padrao consistente", "padrão consistente", "sinal confiavel",
    "sinal confiável", "relacao robusta", "relação robusta",
    "correlacao forte", "correlação forte", "forte correlacao",
    "forte correlação",
)

# Limite acima do qual dois papéis são "na prática o mesmo trade" -- o mesmo
# número que o SYSTEM ensina ao modelo. Abaixo dele a frase é forte demais.
CORR_MESMO_TRADE = 0.70

_MESMO_TRADE = ("mesmo trade", "praticamente o mesmo", "na pratica o mesmo",
                "na prática o mesmo", "identicos", "idênticos",
                "perfeita sincronia", "perfeitamente correlacionados")

# Abaixo disso o SYSTEM manda declarar o n ao citar o papel.
N_EVENTOS_DECLARAR = 5


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(texto or ""))
                   if unicodedata.category(c) != "Mn").lower()


def _sem_blocos_de_codigo(texto: str) -> str:
    """Tira ```blocos``` antes do lint de prosa.

    Mesma precaução do veredito_validator: número dentro de bloco de código é
    dado citado, não afirmação -- e casar palavra-chave lá dentro produziria
    apontamento sobre o que o próprio sistema imprimiu."""
    return re.sub(r"```.*?```", " ", str(texto or ""), flags=re.DOTALL)


def _trechos_do_ticker(texto: str, ticker: str) -> list:
    """As frases que citam o ticker. A checagem é por FRASE, não pelo texto
    inteiro: "AVGO reverte" e "SMCI é um padrão relevante" no mesmo parágrafo
    não podem contaminar um ao outro."""
    alvo = _sem_acento(ticker)
    frases = re.split(r"(?<=[.!?])\s+|\n+", _sem_acento(texto))
    return [f for f in frases if re.search(rf"\b{re.escape(alvo)}\b", f)]


def _resumo_por_ticker(resultados: list) -> dict:
    saida = {}
    for r in resultados or []:
        if not isinstance(r, dict):
            continue
        tk = str(r.get("ticker") or "").strip().upper()
        if tk:
            saida[tk] = r
    return saida


def validar_leitura(texto: str, resultados: list, correlacoes: dict | None = None) -> list:
    """[{nivel, codigo, mensagem, ticker}] -- vazio quando nada destoa.

    `nivel` é "ERRO" para afirmação que o dado CONTRADIZ e "AVISO" para
    afirmação que o dado não sustenta nem nega. A distinção importa porque só
    o primeiro justifica gastar uma retentativa de LLM."""
    achados = []
    prosa = _sem_blocos_de_codigo(texto or "")
    if not prosa.strip():
        return achados
    prosa_sa = _sem_acento(prosa)
    por_ticker = _resumo_por_ticker(resultados)

    def add(nivel, codigo, mensagem, ticker=None):
        achados.append({"nivel": nivel, "codigo": codigo,
                        "mensagem": mensagem, "ticker": ticker})

    # ── 1. lei absoluta a partir de ~8 eventos ──────────────────────────────
    for palavra in _PALAVRAS_DE_LEI:
        if re.search(rf"\b{re.escape(_sem_acento(palavra))}\b", prosa_sa):
            add("ERRO", "LEITURA_LEI_ABSOLUTA",
                f"usa '{palavra}' — com ~8 eventos por ticker nada é lei; "
                f"o SYSTEM pede 'tem tendido a'.")

    # ── 2. correlação promovida a padrão sem sobreviver ao Holm ─────────────
    #
    # O incidente, literalmente: "a correlação negativa de -0.60 para AVGO
    # (...) é um padrão estatisticamente relevante". p corrigido = 0,462.
    for tk, r in por_ticker.items():
        ru = ((r.get("summary") or {}).get("runup")) or {}
        if ru.get("corr_runup_reacao") is None:
            continue
        sobrevive = bool(ru.get("corr_sobrevive"))
        if sobrevive:
            continue
        for frase in _trechos_do_ticker(prosa, tk):
            if any(m in frase for m in (_sem_acento(x) for x in _PROMOVE_A_PADRAO)):
                pc = ru.get("corr_p_corrigido")
                add("ERRO", "LEITURA_CORRELACAO_SEM_SUPORTE",
                    f"trata a correlação de {tk} como padrão, mas ela não "
                    f"sobrevive à correção de múltiplos tickers"
                    + (f" (p corrigido {pc:.3f})" if pc is not None else "")
                    + ".", ticker=tk)
                break

    # ── 3. estado de run-up contradito pelo dado ────────────────────────────
    for tk, r in por_ticker.items():
        ru = ((r.get("summary") or {}).get("runup")) or {}
        estado = str(ru.get("estado_atual") or "")
        if not estado:
            continue
        for frase in _trechos_do_ticker(prosa, tk):
            for rotulo in ("esticado", "descontado"):
                if re.search(rf"\b{rotulo}", frase) and estado != rotulo:
                    add("ERRO", "LEITURA_ESTADO_CONTRADITO",
                        f"diz que {tk} está '{rotulo}', mas o dado do dia "
                        f"marca '{estado}'.", ticker=tk)
                    break

    # ── 4. co-movimento afirmado forte demais ───────────────────────────────
    if any(m in prosa_sa for m in (_sem_acento(x) for x in _MESMO_TRADE)):
        valores = [v for v in (correlacoes or {}).values()
                   if isinstance(v, (int, float))]
        if not valores:
            add("ERRO", "LEITURA_COMOVIMENTO_SEM_DADO",
                "afirma que papéis são o mesmo trade, mas não há correlação "
                "medida no dado desta tela.")
        elif max(valores) < CORR_MESMO_TRADE:
            add("ERRO", "LEITURA_COMOVIMENTO_FORTE_DEMAIS",
                f"afirma 'mesmo trade', mas a maior correlação da cesta é "
                f"{max(valores):.2f} (< {CORR_MESMO_TRADE:.2f}).")

    # ── 5. ticker sem estatística tratado como analisado ────────────────────
    for tk, r in por_ticker.items():
        if not r.get("error") or r.get("summary"):
            continue
        if _trechos_do_ticker(prosa, tk):
            # Citar é permitido -- o SYSTEM manda citar como não analisado. O
            # que não pode é citá-lo junto de número, que ele não tem.
            for frase in _trechos_do_ticker(prosa, tk):
                if re.search(r"\d+[,.]?\d*\s*%", frase):
                    add("ERRO", "LEITURA_TICKER_SEM_DADO",
                        f"cita percentual junto de {tk}, que não produziu "
                        f"estatística nesta rodada.", ticker=tk)
                    break

    # ── 6. amostra curta citada sem declarar o tamanho ──────────────────────
    for tk, r in por_ticker.items():
        s = r.get("summary") or {}
        n = s.get("n_events")
        if not isinstance(n, int) or n >= N_EVENTOS_DECLARAR:
            continue
        frases = _trechos_do_ticker(prosa, tk)
        if frases and not any(re.search(r"\b(evento|amostra|apenas|so |unico|"
                                        r"um unico|1 )", f) for f in frases):
            add("AVISO", "LEITURA_AMOSTRA_CURTA_OMITIDA",
                f"cita {tk} sem dizer que a amostra é de {n} evento(s) — o "
                f"SYSTEM pede a ressalva abaixo de {N_EVENTOS_DECLARAR}.",
                ticker=tk)

    return achados


def erros(achados: list) -> list:
    return [a for a in achados if a.get("nivel") == "ERRO"]


def bloco_de_correcao(achados: list) -> str:
    """O texto que volta ao modelo na retentativa.

    Só os ERROS: mandar o modelo corrigir um AVISO gastaria uma rodada de LLM
    para uma ressalva que o leitor pode ler sozinho no aviso publicado."""
    duros = erros(achados)
    if not duros:
        return ""
    linhas = [f"- {a['mensagem']}" for a in duros]
    return ("\n\nA versão anterior deste texto foi recusada pelo validador "
            "pelos motivos abaixo. Reescreva corrigindo CADA um, sem inventar "
            "número novo e mantendo o tamanho pedido:\n" + "\n".join(linhas))


def resumo_legivel(achados: list) -> list:
    """Uma linha por apontamento, para a tela e para o stderr."""
    return [f"[{a['nivel']}] {a['codigo']}"
            + (f" ({a['ticker']})" if a.get("ticker") else "")
            + f": {a['mensagem']}" for a in achados]
