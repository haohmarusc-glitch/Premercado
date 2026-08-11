"""Recent news headlines per ticker (traduzidas p/ pt-BR) — standalone subprocess.

Input (stdin JSON):  {"tickers": ["NVDA"], "maxItems": 5, "translate": true}
Output (stdout JSON): {"items": [ {ticker, news:[{title, published, summary, source, url, relatedTickers}]}, ... ]}
"""
import sys, json, re
import yfinance as yf
from security import sanitize_ticker, friendly_error
from http_retry import SESSION

def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()

# ── Tradução via endpoint gratuito do Google Translate (sem API key) ──────────
def _translate_join(texts: list[str]) -> list[str]:
    """Traduz uma lista de textos (en->pt-BR) numa única requisição.
    Retorna os originais se algo falhar."""
    if not texts:
        return texts
    joined = "\n".join(texts)
    try:
        r = SESSION.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "pt-BR", "dt": "t", "q": joined},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        r.raise_for_status()
        data = r.json()
        translated = "".join(chunk[0] for chunk in data[0] if chunk and chunk[0])
        lines = translated.split("\n")
        if len(lines) == len(texts):
            return [ln.strip() for ln in lines]
    except Exception:
        pass
    return texts

def translate_all(texts: list[str]) -> list[str]:
    """Traduz em lotes respeitando limite de tamanho da URL."""
    out: list[str] = []
    batch: list[str] = []
    size = 0
    for t in texts:
        if size + len(t) > 3500 and batch:
            out.extend(_translate_join(batch))
            batch, size = [], 0
        batch.append(t)
        size += len(t) + 1
    if batch:
        out.extend(_translate_join(batch))
    return out

# ── Relevância: a matéria de fato fala deste ticker? ───────────────────────────
#
# Visto em produção: o feed da NVDA trazia resumo de teleconferência de
# resultados da Middleby Corporation e da Janus International -- itens que a
# própria Yahoo devolve como "preenchimento" do feed, sem relação nenhuma com
# o ticker pedido. A defesa: só mantém um item sob um ticker se ele
# genuinamente cita esse ticker (símbolo OU nome da empresa) no título/resumo.
#
# Símbolo sozinho não basta: uma manchete sobre "Supermicro" nunca escreve
# "SMCI" no texto. Por isso busca também o nome da empresa (via
# yf.Ticker(t).info, buscado uma vez só por ticker do lote inteiro) --
# comparado numa forma "compacta" (sem espaço/pontuação, minúsculo) pra unir
# grafias como "Supermicro" e "Super Micro" num só token comparável.

_CORP_SUFFIXES = {
    "inc", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
    "holdings", "holding", "group", "the", "technologies", "technology",
    "systems", "international", "sa",
}

def _base_symbol(ticker: str) -> str:
    """SKHY.SA -> SKHY, PETR4.SA -> PETR4."""
    return ticker.split(".")[0].upper()

def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())

def _name_tokens(name: str) -> list[str]:
    """'Super Micro Computer, Inc.' -> ['supermicrocomputer', 'supermicro'].

    Manchete abrevia o nome de formas inconsistentes -- "Supermicro" nunca
    aparece com "Computer" junto. Em vez de um token só, gera candidatos indo
    do nome completo (sem sufixo corporativo) até só a primeira palavra,
    removendo uma palavra do FIM por vez -- assim "SuperMicroComputer" e
    "SuperMicro" (o que a manchete de verdade usa) viram candidatos
    separados, e basta um bater. Token com menos de 5 caracteres é descartado
    (evita casar palavra genérica isolada tipo sufixo que sobrou sozinho)."""
    words = [w for w in re.findall(r"[A-Za-z0-9]+", name or "") if w.lower() not in _CORP_SUFFIXES]
    # Nunca trunca até sobrar 1 palavra isolada quando o nome original tinha
    # mais de uma -- "Super" (de "Super Micro Computer") é palavra comum
    # demais pra valer sozinha, mas "Marvell" (nome que já É uma palavra só)
    # continua válido.
    floor = 1 if len(words) <= 1 else 2
    tokens = []
    for end in range(len(words), floor - 1, -1):
        token = "".join(words[:end]).lower()
        if len(token) >= 5:
            tokens.append(token)
    return tokens

_WORD_RE_CACHE: dict[str, "re.Pattern"] = {}

def _word_re(base: str) -> "re.Pattern":
    pattern = _WORD_RE_CACHE.get(base)
    if pattern is None:
        pattern = re.compile(r"\b" + re.escape(base) + r"\b", re.IGNORECASE)
        _WORD_RE_CACHE[base] = pattern
    return pattern

def _company_names(tickers: list[str]) -> dict[str, str]:
    """Nome curto de cada ticker via yfinance, best-effort. Um nome ausente
    (falha de rede, ticker sem info) só significa que esse ticker cai de
    volta pra checagem por símbolo sozinho -- nunca derruba o lote inteiro."""
    names: dict[str, str] = {}
    for t in tickers:
        try:
            info = yf.Ticker(t).info or {}
            names[t] = info.get("shortName") or info.get("longName") or ""
        except Exception as e:
            print(f"[get_news_feed] nome de {t} indisponível: {e}", file=sys.stderr)
            names[t] = ""
    return names

def _extract_tagged_tickers(content: dict) -> set[str]:
    """Tickers que a própria Yahoo marcou como assunto da matéria, quando o
    payload traz essa metadata (schema varia entre versões da API; tenta os
    formatos conhecidos e nunca levanta erro se não achar nada)."""
    out: set[str] = set()
    finance = content.get("finance", {})
    if isinstance(finance, dict):
        for t in finance.get("stockTickers", []) or []:
            if isinstance(t, dict) and t.get("symbol"):
                out.add(_base_symbol(str(t["symbol"])))
    for key in ("relatedTickers", "tickers"):
        for t in content.get(key, []) or []:
            sym = t.get("symbol") if isinstance(t, dict) else t
            if sym:
                out.add(_base_symbol(str(sym)))
    return out

def _relevant_tickers(
    text: str, tagged: set[str], candidates: list[str], names: dict[str, str]
) -> set[str]:
    """Quais tickers (dentre `candidates`, a lista pedida nesta chamada) esta
    matéria de fato menciona -- tagueado pela Yahoo, símbolo citado como
    palavra inteira, ou nome da empresa presente no texto."""
    compact_text = _compact(text)
    out: set[str] = set()
    for t in candidates:
        base = _base_symbol(t)
        if base in tagged or _word_re(base).search(text):
            out.add(t)
            continue
        if any(token in compact_text for token in _name_tokens(names.get(t, ""))):
            out.add(t)
    return out

def for_ticker(ticker: str, max_items: int, all_tickers: list[str], names: dict[str, str]) -> dict:
    try:
        ticker = sanitize_ticker(ticker)
    except ValueError as e:
        return {"ticker": str(ticker), "error": str(e)}
    try:
        news = yf.Ticker(ticker).news or []
        out = []
        # Busca em mais itens que o pedido -- o filtro de relevância descarta
        # o que a Yahoo devolveu como "preenchimento" (item sem relação com o
        # ticker pedido), então max_items exatos nem sempre sobram.
        for item in news[: max_items * 4]:
            if len(out) >= max_items:
                break
            content = item.get("content", {}) if isinstance(item.get("content"), dict) else {}
            title = clean_text(content.get("title", item.get("title", "")))
            summary_raw = content.get("summary", item.get("summary", "")) or ""
            provider = content.get("provider", {})
            source = provider.get("displayName", "") if isinstance(provider, dict) else ""
            canonical = content.get("canonicalUrl", {})
            click_through = content.get("clickThroughUrl", {})
            url = (
                (canonical.get("url") if isinstance(canonical, dict) else "")
                or (click_through.get("url") if isinstance(click_through, dict) else "")
                or item.get("link", "")
            )

            tagged = _extract_tagged_tickers(content)
            relevant = _relevant_tickers(f"{title} {summary_raw}", tagged, all_tickers, names)
            if ticker not in relevant:
                continue
            related = sorted(t for t in relevant if t != ticker)

            out.append({
                "title": title,
                "published": content.get("pubDate", item.get("providerPublishTime", "")),
                "summary": clean_text(summary_raw[:280] + ("..." if len(summary_raw) > 280 else "")),
                "source": source,
                "url": url or None,
                "relatedTickers": related or None,
            })
        return {"ticker": ticker, "news": out}
    except Exception as e:
        print(f"[get_news_feed] {ticker}: {e}", file=sys.stderr)
        return {"ticker": ticker, "error": friendly_error(e)}

if __name__ == "__main__":
    args = json.loads(sys.stdin.read())
    max_items = int(args.get("maxItems", 5))
    do_translate = args.get("translate", True)
    tickers = args.get("tickers", [])
    names = _company_names(tickers)
    items = [for_ticker(t, max_items, tickers, names) for t in tickers]

    if do_translate:
        # Junta todos os textos (title + summary) numa lista, traduz em lote e devolve.
        refs = []  # (item_dict, field)
        texts = []
        for it in items:
            for n in it.get("news", []):
                for field in ("title", "summary"):
                    if n.get(field):
                        refs.append((n, field))
                        texts.append(n[field])
        if texts:
            translated = translate_all(texts)
            for (n, field), tr in zip(refs, translated):
                n[field] = tr

    print(json.dumps({"items": items}))
