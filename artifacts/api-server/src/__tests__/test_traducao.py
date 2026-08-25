"""
Tradução en->pt-BR em camadas -- e a regra que o bug de 25/08/2026 violou:
FALHA NUNCA É SILENCIOSA.

O código antigo era uma chamada ao endpoint gratuito do Google dentro de um
`except: pass`. Quando ele passou a responder HTTP 429 (bloqueio de tráfego
automatizado), a manchete voltava em inglês e NADA no log dizia por quê --
a bolinha de notícia do gráfico ficou em inglês em produção sem deixar
rastro. Metade destes testes existe para garantir que cada modo de falha
grite no stderr; a outra metade cobre o pareamento por índice, que é onde
um erro trocaria a manchete de uma notícia pela de outra.

Sem rede: as camadas são injetadas (`google=`, `llm=`).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_traducao.py -v
"""
import json

from agent import traducao


def _cache(tmp_path):
    return str(tmp_path / "traducoes.json")


def _sempre(retorno):
    def _fn(textos):
        return list(retorno) if retorno is not None else None
    return _fn


def _falha(textos):
    return None


# ── camadas, em ordem ────────────────────────────────────────────────────────

def test_google_resolve_e_llm_nao_e_chamado(tmp_path):
    chamou_llm = []
    saida, origens = traducao.traduzir(
        ["Nvidia rises"], google=_sempre(["Nvidia sobe"]),
        llm=lambda t: chamou_llm.append(t) or ["nunca"], cache_path=_cache(tmp_path))
    assert saida == ["Nvidia sobe"] and origens == ["google"]
    assert chamou_llm == [], "LLM não pode custar dinheiro quando o grátis resolveu"

def test_llm_assume_quando_o_google_falha(tmp_path):
    saida, origens = traducao.traduzir(
        ["Chip demand strong"], google=_falha, llm=_sempre(["Demanda por chips forte"]),
        cache_path=_cache(tmp_path))
    assert saida == ["Demanda por chips forte"] and origens == ["llm"]

def test_todas_falham_devolve_original_marcado(tmp_path, capsys):
    saida, origens = traducao.traduzir(
        ["Nvidia rises"], google=_falha, llm=_falha, cache_path=_cache(tmp_path))
    assert saida == ["Nvidia rises"], "texto tem que voltar, nunca sumir"
    assert origens == ["original"], "a origem é o que deixa a tela ser honesta"
    assert "ficaram em inglês" in capsys.readouterr().err

def test_usar_llm_false_nao_gasta_token(tmp_path):
    chamou = []
    _s, origens = traducao.traduzir(
        ["x"], google=_falha, llm=lambda t: chamou.append(t) or ["y"],
        cache_path=_cache(tmp_path), usar_llm=False)
    assert chamou == [] and origens == ["original"]


# ── cache ────────────────────────────────────────────────────────────────────

def test_segunda_chamada_vem_do_cache_sem_rede(tmp_path):
    caminho = _cache(tmp_path)
    traducao.traduzir(["Nvidia rises"], google=_sempre(["Nvidia sobe"]),
                      llm=_falha, cache_path=caminho)
    saida, origens = traducao.traduzir(
        ["Nvidia rises"], google=_falha, llm=_falha, cache_path=caminho)
    assert saida == ["Nvidia sobe"] and origens == ["cache"]

def test_cache_ignora_diferenca_de_espaco_em_branco(tmp_path):
    caminho = _cache(tmp_path)
    traducao.traduzir(["Nvidia   rises"], google=_sempre(["Nvidia sobe"]),
                      llm=_falha, cache_path=caminho)
    _s, origens = traducao.traduzir(["Nvidia rises"], google=_falha, llm=_falha,
                                    cache_path=caminho)
    assert origens == ["cache"]

def test_so_o_que_falta_vai_para_a_rede(tmp_path):
    """Meia dúzia de manchetes com uma nova não pode retraduzir as cinco
    antigas -- é o que protege do 429 e do custo."""
    caminho = _cache(tmp_path)
    traducao.traduzir(["a", "b"], google=_sempre(["A", "B"]), llm=_falha, cache_path=caminho)
    enviados = []
    def _espia(textos):
        enviados.append(list(textos))
        return ["C"]
    saida, origens = traducao.traduzir(["a", "b", "c"], google=_espia, llm=_falha,
                                       cache_path=caminho)
    assert enviados == [["c"]]
    assert saida == ["A", "B", "C"]
    assert origens == ["cache", "cache", "google"]

def test_cache_corrompido_nao_derruba_a_traducao(tmp_path, capsys):
    caminho = _cache(tmp_path)
    open(caminho, "w").write("{isso não é json")
    saida, _o = traducao.traduzir(["x"], google=_sempre(["X"]), llm=_falha, cache_path=caminho)
    assert saida == ["X"]
    assert "cache ilegível" in capsys.readouterr().err

def test_cache_nao_gravavel_degrada_sem_quebrar(tmp_path, capsys):
    saida, origens = traducao.traduzir(
        ["x"], google=_sempre(["X"]), llm=_falha,
        cache_path="/proc/impossivel/traducoes.json")
    assert saida == ["X"] and origens == ["google"]
    assert "não consegui gravar o cache" in capsys.readouterr().err

def test_cache_respeita_o_teto_mantendo_os_recentes(tmp_path):
    caminho = _cache(tmp_path)
    cheio = {f"k{i}": f"v{i}" for i in range(traducao.MAX_ENTRADAS + 50)}
    traducao.gravar_cache(cheio, caminho)
    lido = traducao.carregar_cache(caminho)
    assert len(lido) == traducao.MAX_ENTRADAS
    assert f"k{traducao.MAX_ENTRADAS + 49}" in lido  # o mais recente sobrevive
    assert "k0" not in lido


# ── pareamento por índice (onde um erro troca manchetes) ─────────────────────

def test_ordem_e_tamanho_sao_preservados(tmp_path):
    entrada = ["um", "dois", "tres"]
    saida, origens = traducao.traduzir(entrada, google=_sempre(["1", "2", "3"]),
                                       llm=_falha, cache_path=_cache(tmp_path))
    assert saida == ["1", "2", "3"] and len(origens) == len(entrada)

def test_texto_vazio_nao_vai_para_traducao(tmp_path):
    enviados = []
    def _espia(textos):
        enviados.append(list(textos))
        return [t.upper() for t in textos]
    saida, origens = traducao.traduzir(["", "ok", "   "], google=_espia, llm=_falha,
                                       cache_path=_cache(tmp_path))
    assert enviados == [["ok"]]
    assert saida == ["", "OK", "   "]
    assert origens == ["vazio", "google", "vazio"]

def test_lista_vazia_nao_estoura(tmp_path):
    assert traducao.traduzir([], cache_path=_cache(tmp_path)) == ([], [])

def test_lote_grande_e_quebrado_por_tamanho(tmp_path):
    textos = ["x" * 1000 for _ in range(10)]
    lotes = []
    def _espia(t):
        lotes.append(len(t))
        return [s.upper() for s in t]
    traducao.traduzir(textos, google=_espia, llm=_falha, cache_path=_cache(tmp_path))
    assert len(lotes) > 1, "10 KB numa requisição só estoura o limite da URL"
    assert sum(lotes) == 10


# ── os motivos de falha aparecem no log ──────────────────────────────────────

def test_google_429_diz_que_e_bloqueio(tmp_path, capsys, monkeypatch):
    class _Resp:
        status_code = 429
    monkeypatch.setattr(traducao, "_google", traducao._google)
    import types
    fake = types.SimpleNamespace(get=lambda *a, **k: _Resp())
    monkeypatch.setitem(__import__("sys").modules, "requests", fake)
    assert traducao._google(["x"]) is None
    err = capsys.readouterr().err
    assert "HTTP 429" in err and "bloqueio" in err

def test_google_com_contagem_de_linhas_errada_descarta_o_lote(tmp_path, capsys, monkeypatch):
    """Parear 3 traduções com 2 textos trocaria a manchete de uma notícia
    pela de outra -- pior que não traduzir."""
    class _Resp:
        status_code = 200
        def json(self):
            return [[["um\ndois\ntres", ""]]]
    import types
    monkeypatch.setitem(__import__("sys").modules, "requests",
                        types.SimpleNamespace(get=lambda *a, **k: _Resp()))
    assert traducao._google(["a", "b"]) is None
    assert "lote descartado" in capsys.readouterr().err

def test_llm_com_numero_errado_de_itens_e_descartado(tmp_path, capsys, monkeypatch):
    import types
    fake_provider = types.SimpleNamespace(
        get_client=lambda: types.SimpleNamespace(
            models={"flash": "m"},
            create=lambda **k: "resp"),
        texto_da_resposta=lambda r: json.dumps(["so um"]),
    )
    monkeypatch.setitem(__import__("sys").modules, "provider", fake_provider)
    assert traducao._llm(["a", "b"]) is None
    assert "descartado" in capsys.readouterr().err

def test_llm_aceita_array_embrulhado_em_markdown(tmp_path, monkeypatch):
    import types
    fake_provider = types.SimpleNamespace(
        get_client=lambda: types.SimpleNamespace(models={"flash": "m"}, create=lambda **k: "r"),
        texto_da_resposta=lambda r: '```json\n["um", "dois"]\n```',
    )
    monkeypatch.setitem(__import__("sys").modules, "provider", fake_provider)
    assert traducao._llm(["a", "b"]) == ["um", "dois"]


# ── o ponto único é único mesmo ──────────────────────────────────────────────

def test_so_traducao_py_fala_com_o_google():
    """O conserto de 25/08/2026 criou este módulo e migrou get_news_feed.py --
    mas get_trend.py e get_market_alerts_snapshot.py tinham CÓPIAS da mesma
    chamada, e seguiram devolvendo inglês em silêncio quando o endpoint
    passou a responder 429. Copiar o padrão é fácil; achar todas as cópias
    depois, não. Este teste faz a procura no lugar de quem vier depois."""
    import pathlib
    from agent import traducao
    agente = pathlib.Path(traducao.__file__).parent
    culpados = [py.name for py in agente.rglob("*.py")
                if py.name != "traducao.py"
                and "translate.googleapis.com" in py.read_text(encoding="utf-8")]
    assert culpados == [], (
        f"tradução fora do ponto único em {culpados} -- quando o Google cai, "
        f"essas telas voltam ao inglês sem avisar")


# ── lote que falha não pode derrubar o que traduziria ────────────────────────

def test_lote_ruim_e_dividido_em_vez_de_perdido(tmp_path):
    """Tudo-ou-nada devolvia ao inglês manchetes que traduziriam sem
    problema: basta UMA resposta malformada no lote."""
    def _llm_chato(textos):
        # Falha em qualquer lote que contenha o texto problemático.
        if any("veneno" in t for t in textos):
            return None
        return [f"[pt] {t}" for t in textos]

    entrada = ["bom um", "bom dois", "veneno", "bom três"]
    saida, origens = traducao.traduzir(
        entrada, google=lambda t: None, llm=_llm_chato,
        cache_path=str(tmp_path / "cache.json"))
    assert saida[0] == "[pt] bom um" and saida[3] == "[pt] bom três"
    assert saida[2] == "veneno", "o item ruim fica no original"
    assert origens == ["llm", "llm", "original", "llm"]


def test_divisao_nao_recursiona_sem_fim(tmp_path):
    chamadas = []

    def _sempre_falha(textos):
        chamadas.append(len(textos))
        return None

    entrada = [f"texto {i}" for i in range(8)]
    saida, origens = traducao.traduzir(
        entrada, google=lambda t: None, llm=_sempre_falha,
        cache_path=str(tmp_path / "cache.json"))
    assert saida == entrada and set(origens) == {"original"}
    assert len(chamadas) < 40, f"divisão descontrolada: {len(chamadas)} chamadas"


def test_teto_de_tokens_acompanha_o_tamanho_do_lote():
    """max_tokens fixo em 2000 raspava o teto num lote cheio: a resposta vinha
    truncada, o array não fechava e o lote inteiro era descartado. O português
    sai mais comprido que o inglês, e o JSON ainda cobra aspas e vírgulas."""
    vistos = {}

    class _Resp:
        content = [type("B", (), {"type": "text", "text": '["a"]'})()]

    class _Cliente:
        models = {"flash": "m"}

        def create(self, **kw):
            vistos.update(kw)
            return _Resp()

    import sys as _sys
    import types
    _sys.modules["provider"] = types.SimpleNamespace(
        get_client=lambda: _Cliente(),
        texto_da_resposta=lambda r: '["a"]')
    try:
        traducao._llm(["x" * 3500])
        assert vistos["max_tokens"] > 2000, (
            f"teto de {vistos['max_tokens']} para 3500 chars volta a truncar")
    finally:
        _sys.modules.pop("provider", None)
