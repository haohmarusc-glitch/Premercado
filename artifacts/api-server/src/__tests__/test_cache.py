"""
Testes de cache.py — TTL, falha aberta (cache corrompido nunca quebra a tool),
e a regra de não cachear respostas de erro.

Cada teste isola o estado do módulo via monkeypatch direto em _CACHE_PATH/_mem/
_loaded (em vez de recarregar o módulo — ver comentário na fixture abaixo).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_cache.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import os
import time

import pytest


@pytest.fixture
def cache_module(tmp_path, monkeypatch):
    """
    Usa o módulo agent.cache já carregado (import único por sessão de teste) e
    isola cada teste via monkeypatch direto nos atributos internos: caminho do
    arquivo de cache, dict em memória e flag de habilitação. Evitar reload de
    módulo aqui de propósito — recarregar agent.cache/agent.config no meio de
    uma sessão pytest cria múltiplas instâncias do mesmo módulo coexistindo
    (uma referenciada por imports anteriores, outra pelo teste atual), o que
    contamina o estado entre testes de forma sutil.
    """
    from agent import cache as _cache

    cache_file = tmp_path / "test_cache.json"
    monkeypatch.setattr(_cache, "_CACHE_PATH", str(cache_file))
    monkeypatch.setattr(_cache, "_mem", {})
    monkeypatch.setattr(_cache, "_loaded", True)
    monkeypatch.setattr(_cache.config, "CACHE_ENABLED", True)
    monkeypatch.setattr(_cache.config, "CACHE_TTL_SECONDS", 300)
    yield _cache


class TestCachedDecorator:
    def test_second_call_returns_cached_value_without_calling_fn_again(
        self, cache_module
    ):
        calls = []

        @cache_module.cached("greet:{0}", ttl=300)
        def greet(name):
            calls.append(name)
            return f"olá, {name}"

        assert greet("Jefferson") == "olá, Jefferson"
        assert greet("Jefferson") == "olá, Jefferson"
        assert calls == ["Jefferson"]

    def test_different_args_produce_different_cache_entries(self, cache_module):
        calls = []

        @cache_module.cached("stock:{0}", ttl=300)
        def get_price(ticker):
            calls.append(ticker)
            return f"preço de {ticker}"

        get_price("NVDA")
        get_price("MU")
        get_price("NVDA")
        assert calls == ["NVDA", "MU"]

    def test_expired_entry_calls_fn_again(self, cache_module, monkeypatch):
        calls = []

        @cache_module.cached("x:{0}", ttl=1)
        def fn(arg):
            calls.append(arg)
            return arg

        fn("a")
        assert calls == ["a"]
        time.sleep(1.1)
        fn("a")
        assert calls == ["a", "a"]

    def test_error_dict_result_is_not_cached(self, cache_module):
        calls = []

        @cache_module.cached("x:{0}", ttl=300)
        def fn(arg):
            calls.append(arg)
            return {"error": "falhou"}

        fn("a")
        fn("a")
        assert calls == ["a", "a"]

    def test_error_list_result_is_not_cached(self, cache_module):
        calls = []

        @cache_module.cached("x:{0}", ttl=300)
        def fn(arg):
            calls.append(arg)
            return [{"error": "falhou"}]

        fn("a")
        fn("a")
        assert calls == ["a", "a"]

    def test_error_string_result_is_not_cached(self, cache_module):
        calls = []

        @cache_module.cached("x:{0}", ttl=300)
        def fn(arg):
            calls.append(arg)
            return "[erro ao ler filing: timeout]"

        fn("a")
        fn("a")
        assert calls == ["a", "a"]

    def test_successful_dict_without_error_key_is_cached(self, cache_module):
        calls = []

        @cache_module.cached("x:{0}", ttl=300)
        def fn(arg):
            calls.append(arg)
            return {"ticker": arg, "price": 100}

        fn("a")
        fn("a")
        assert calls == ["a"]

    def test_cache_disabled_always_calls_fn(self, cache_module, monkeypatch):
        monkeypatch.setattr(cache_module.config, "CACHE_ENABLED", False)
        calls = []

        @cache_module.cached("x:{0}", ttl=300)
        def fn(arg):
            calls.append(arg)
            return arg

        fn("a")
        fn("a")
        assert calls == ["a", "a"]

    def test_key_without_placeholders_falls_back_to_literal_key(self, cache_module):
        calls = []

        @cache_module.cached("fear_greed")
        def fn():
            calls.append(1)
            return {"score": 50}

        fn()
        fn()
        assert calls == [1]

    def test_wrapper_preserves_function_name_and_docstring(self, cache_module):
        @cache_module.cached("x:{0}")
        def my_func(arg):
            """Minha docstring."""
            return arg

        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "Minha docstring."

    def test_persists_to_disk_and_a_fresh_load_reads_it_back(
        self, cache_module, tmp_path, monkeypatch
    ):
        """O cache é em disco — _load() lendo o arquivo deve recuperar o que _flush() gravou."""

        @cache_module.cached("x:{0}", ttl=300)
        def fn(arg):
            return {"value": arg}

        fn("persisted")
        cache_path = cache_module._CACHE_PATH
        assert os.path.exists(cache_path)

        monkeypatch.setattr(cache_module, "_mem", {})
        monkeypatch.setattr(cache_module, "_loaded", False)

        calls = []

        @cache_module.cached("x:{0}", ttl=300)
        def fn2(arg):
            calls.append(arg)
            return {"value": "deveria nao chamar"}

        result = fn2("persisted")
        assert calls == []
        assert result == {"value": "persisted"}


class TestCacheFailsOpen:
    def test_corrupted_cache_file_does_not_crash(self, tmp_path, monkeypatch):
        from agent import cache as _cache

        cache_file = tmp_path / "corrupted.json"
        cache_file.write_text("isto não é json válido {{{")
        monkeypatch.setattr(_cache, "_CACHE_PATH", str(cache_file))
        monkeypatch.setattr(_cache, "_mem", {})
        monkeypatch.setattr(_cache, "_loaded", False)
        monkeypatch.setattr(_cache.config, "CACHE_ENABLED", True)

        @_cache.cached("x:{0}", ttl=300)
        def fn(arg):
            return {"ok": True}

        result = fn("a")
        assert result == {"ok": True}

    def test_unwritable_cache_path_does_not_crash(self, tmp_path, monkeypatch):
        from agent import cache as _cache

        bad_path = tmp_path / "this_is_a_dir"
        bad_path.mkdir()
        monkeypatch.setattr(_cache, "_CACHE_PATH", str(bad_path))
        monkeypatch.setattr(_cache, "_mem", {})
        monkeypatch.setattr(_cache, "_loaded", True)
        monkeypatch.setattr(_cache.config, "CACHE_ENABLED", True)

        @_cache.cached("x:{0}", ttl=300)
        def fn(arg):
            return {"ok": True}

        result = fn("a")
        assert result == {"ok": True}
