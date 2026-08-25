"""
Tradução en->pt-BR das manchetes, em três camadas e SEM falha silenciosa.

O que existia: uma chamada ao endpoint gratuito do Google Translate dentro de
um `try/except: pass` que devolvia o texto ORIGINAL quando qualquer coisa
dava errado. Em produção (25/08/2026) o endpoint passou a responder
**HTTP 429** -- bloqueio de tráfego automatizado --, e o resultado foi a
bolinha de notícia do gráfico mostrando manchete em inglês, sem uma linha de
log dizendo por quê. É a armadilha nº 1 desta casa: o problema não foi a
tradução falhar, foi ela falhar em silêncio.

As três camadas, nesta ordem:

  1. CACHE em disco -- manchete repete entre telas, entre recargas e entre
     tickers do mesmo lote. Além de economizar, é o que evita martelar o
     endpoint gratuito e provocar o próprio 429.
  2. GOOGLE gratuito -- rápido e sem chave quando funciona; agora o motivo
     da falha (status HTTP, contagem de linhas) vai para o stderr.
  3. LLM da cadeia já existente -- o app já tem provedor, orçamento e a tela
     de Gastos com IA. Traduzir dez manchetes curtas é barato, e só roda
     sobre o que as camadas anteriores não resolveram.

Se as três falharem, o texto volta em inglês -- mas o chamador recebe a
ORIGEM de cada tradução e pode dizer isso na tela, em vez de o leitor
descobrir sozinho que aquilo devia estar em português.

As funções de rede são injetáveis (`google=`, `llm=`) justamente para a
suíte cobrir a lógica de camadas sem tocar a rede.
"""
import hashlib
import json
import os
import re
import sys

CACHE_PATH = os.environ.get("TRADUCAO_CACHE_PATH") or "/var/cache/premercado/traducoes.json"
# Teto do cache: manchete velha não volta, e arquivo sem limite vira problema
# de memória no boot. Ao estourar, mantém as MAIS RECENTES (dict do Python
# preserva ordem de inserção).
MAX_ENTRADAS = 3000
LIMITE_LOTE_CHARS = 3500


def _chave(texto: str) -> str:
    return hashlib.sha1(re.sub(r"\s+", " ", texto).strip().encode("utf-8")).hexdigest()[:16]


def carregar_cache(caminho: str = CACHE_PATH) -> dict:
    try:
        with open(caminho, "r", encoding="utf-8") as f:
            dados = json.load(f)
        return dados if isinstance(dados, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[traducao] cache ilegível ({caminho}): {e}", file=sys.stderr)
        return {}


def gravar_cache(cache: dict, caminho: str = CACHE_PATH) -> bool:
    """Escrita atômica (tmp + replace): um processo lendo no meio da escrita
    não pega arquivo pela metade. Falha aqui NÃO derruba a tradução --
    perder cache é degradação, não erro."""
    try:
        if len(cache) > MAX_ENTRADAS:
            cache = dict(list(cache.items())[-MAX_ENTRADAS:])
        os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)
        tmp = caminho + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False)
        os.replace(tmp, caminho)
        return True
    except Exception as e:
        print(f"[traducao] não consegui gravar o cache ({caminho}): {e}", file=sys.stderr)
        return False


# ── camada 2: Google gratuito ────────────────────────────────────────────────

def _google(textos: list) -> list | None:
    """Traduz o lote inteiro numa requisição. None = falhou (com motivo no
    stderr). O endpoint junta por "\\n" e às vezes devolve número de linhas
    diferente do enviado -- quando isso acontece o lote inteiro é descartado,
    porque parear errado trocaria a manchete de uma notícia pela de outra."""
    import requests
    joined = "\n".join(textos)
    try:
        r = requests.get(
            "https://translate.googleapis.com/translate_a/single",
            params={"client": "gtx", "sl": "en", "tl": "pt-BR", "dt": "t", "q": joined},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        if r.status_code != 200:
            print(f"[traducao] google respondeu HTTP {r.status_code} "
                  f"({'bloqueio de trafego automatizado' if r.status_code == 429 else 'erro'})",
                  file=sys.stderr)
            return None
        data = r.json()
        traduzido = "".join(c[0] for c in data[0] if c and c[0])
        linhas = traduzido.split("\n")
        if len(linhas) != len(textos):
            print(f"[traducao] google devolveu {len(linhas)} linhas para {len(textos)} textos "
                  f"-- lote descartado para não parear manchete errada", file=sys.stderr)
            return None
        return [ln.strip() for ln in linhas]
    except Exception as e:
        print(f"[traducao] google indisponível: {type(e).__name__}: {e}", file=sys.stderr)
        return None


# ── camada 3: LLM da cadeia existente ────────────────────────────────────────

_SYSTEM_TRADUCAO = (
    "Você traduz manchetes e resumos financeiros de inglês para português do "
    "Brasil. Preserve tickers (NVDA, MU), nomes de empresa e números exatamente "
    "como estão. Não explique, não comente, não adicione nem remova itens. "
    "Responda APENAS um array JSON de strings, na MESMA ordem e com o MESMO "
    "número de itens que recebeu."
)


def _llm(textos: list) -> list | None:
    try:
        try:
            from provider import get_client, texto_da_resposta
        except ImportError:
            from agent.provider import get_client, texto_da_resposta
        client = get_client()
        modelo = client.models.get("flash") or next(iter(client.models.values()))
        resp = client.create(
            model=modelo,
            max_tokens=2000,
            system=_SYSTEM_TRADUCAO,
            tools=[],
            messages=[{"role": "user", "content": json.dumps(textos, ensure_ascii=False)}],
        )
        bruto = (texto_da_resposta(resp) or "").strip()
        # O modelo às vezes embrulha em ```json; pega do primeiro [ ao último ].
        ini, fim = bruto.find("["), bruto.rfind("]")
        if ini == -1 or fim <= ini:
            print("[traducao] LLM não devolveu array JSON", file=sys.stderr)
            return None
        saida = json.loads(bruto[ini:fim + 1])
        if not isinstance(saida, list) or len(saida) != len(textos):
            print(f"[traducao] LLM devolveu {len(saida) if isinstance(saida, list) else '?'} itens "
                  f"para {len(textos)} textos -- descartado", file=sys.stderr)
            return None
        return [str(s) for s in saida]
    except Exception as e:
        print(f"[traducao] LLM indisponível: {type(e).__name__}: {e}", file=sys.stderr)
        return None


# ── orquestração ─────────────────────────────────────────────────────────────

def _lotes(textos: list, limite: int = LIMITE_LOTE_CHARS) -> list:
    lotes, atual, tamanho = [], [], 0
    for t in textos:
        if atual and tamanho + len(t) > limite:
            lotes.append(atual)
            atual, tamanho = [], 0
        atual.append(t)
        tamanho += len(t) + 1
    if atual:
        lotes.append(atual)
    return lotes


def traduzir(textos: list, *, google=_google, llm=_llm,
             cache_path: str = CACHE_PATH, usar_llm: bool = True) -> tuple:
    """(traduzidos, origens) — origens[i] ∈ cache|google|llm|original|vazio.

    Preserva ordem e tamanho SEMPRE: o chamador pareia por índice, e um
    desalinhamento aqui trocaria a manchete de uma notícia pela de outra."""
    if not textos:
        return [], []

    cache = carregar_cache(cache_path)
    saida = list(textos)
    origens = ["original"] * len(textos)

    pendentes = []  # índices que ainda precisam de tradução
    for i, t in enumerate(textos):
        if not (t or "").strip():
            origens[i] = "vazio"
            continue
        k = _chave(t)
        if k in cache:
            saida[i], origens[i] = cache[k], "cache"
        else:
            pendentes.append(i)

    novos = {}
    for camada, fn in (("google", google), ("llm", llm if usar_llm else None)):
        if fn is None or not pendentes:
            continue
        restantes = []
        for lote_idx in _lotes_de_indices(pendentes, textos):
            traduzidos = fn([textos[i] for i in lote_idx])
            if traduzidos is None:
                restantes.extend(lote_idx)
                continue
            for i, tr in zip(lote_idx, traduzidos):
                saida[i], origens[i] = tr, camada
                novos[_chave(textos[i])] = tr
        pendentes = restantes

    if pendentes:
        print(f"[traducao] {len(pendentes)} texto(s) ficaram em inglês -- "
              f"todas as camadas falharam", file=sys.stderr)
    if novos:
        cache.update(novos)
        gravar_cache(cache, cache_path)
    return saida, origens


def _lotes_de_indices(indices: list, textos: list) -> list:
    """Mesma quebra por tamanho de _lotes, mas carregando os índices."""
    lotes, atual, tamanho = [], [], 0
    for i in indices:
        t = textos[i]
        if atual and tamanho + len(t) > LIMITE_LOTE_CHARS:
            lotes.append(atual)
            atual, tamanho = [], 0
        atual.append(i)
        tamanho += len(t) + 1
    if atual:
        lotes.append(atual)
    return lotes
