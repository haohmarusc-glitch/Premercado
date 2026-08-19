"""
Trocar de provedor não pode rebaixar o modelo em silêncio.

`FallbackClient.create` recebe um NOME de modelo e precisa descobrir o tier
para pedir o equivalente no provedor seguinte. Isso é feito por `_TIER_MAP`,
que inverte a tabela: nome -> tier.

A inversão tem um empate embutido. Um mesmo modelo costuma servir vários
tiers -- gemini-2.5-flash é full, flash E chat; o llama do openrouter idem; e
o deepseek-v4-flash passou a ser os três em 18/08/2026, quando o v4-pro saiu do
tier full por gastar 55s do orçamento sem entregar resposta.

Sem precedência, o último tier escrito vencia. Como os dicts listam full,
flash, chat nessa ordem, o vencedor era sempre `chat` -- e pedir o `full` do
gemini, caindo para o anthropic, trazia o haiku onde a chamada pedia o sonnet.

O que torna isso feio é o modo de falhar: a resposta VEM. Não há erro, não há
aviso, só uma análise pior do que a pedida. Estava valendo para gemini e
openrouter em produção antes de alguém notar.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_tier_do_fallback.py -v
"""
import pytest

from agent import provider as prov


# ── a inversão ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("nome", sorted(prov.PROVIDERS))
def test_o_modelo_full_de_todo_provedor_resolve_como_full(nome):
    """A invariante que importa: seja qual for o provedor de origem, pedir o
    `full` dele e cair para outro tem que continuar pedindo `full`."""
    full = prov.PROVIDERS[nome]["models"]["full"]
    assert prov._resolve_tier(full) == "full", (
        f"{nome}: o modelo '{full}' serve mais de um tier e o desempate o "
        f"rebaixou -- a troca de provedor entregaria modelo mais fraco que o pedido"
    )


def test_o_desempate_sobe_e_nao_desce():
    """Diante de um nome ambíguo, errar para cima custa centavos; errar para
    baixo entrega análise pior sem avisar."""
    # gemini-2.5-flash serve full, flash e chat ao mesmo tempo.
    assert prov.PROVIDERS["gemini"]["models"]["chat"] == "gemini-2.5-flash"
    assert prov._resolve_tier("gemini-2.5-flash") == "full"


def test_modelo_que_serve_flash_e_chat_resolve_flash():
    """Precedência completa, não só um caso especial para o full."""
    assert prov.PROVIDERS["anthropic"]["models"]["chat"] == "claude-haiku-4-5"
    assert prov._resolve_tier("claude-haiku-4-5") == "flash"


def test_modelo_desconhecido_nao_resolve():
    """Sem tier, `create` usa o nome cru -- que é o comportamento certo para um
    modelo passado à mão."""
    assert prov._resolve_tier("modelo-que-nao-existe") is None


# ── o efeito na cadeia ──────────────────────────────────────────────────────

def test_a_troca_de_provedor_pede_o_equivalente_full(monkeypatch):
    """O teste de ponta a ponta do rebaixamento: começa no gemini, o gemini
    falha, e o anthropic tem que receber o sonnet -- não o haiku."""
    monkeypatch.setattr(prov, "_provider_order", lambda: ["gemini", "anthropic"])
    monkeypatch.setattr(prov, "_has_key", lambda _p: True)
    cadeia = prov.FallbackClient()

    pedidos: list[tuple[str, str]] = []

    class _Cliente:
        def __init__(self, nome):
            self.models = prov.PROVIDERS[nome]["models"]
            self._nome = nome

        def create(self, *, model, **_kw):
            pedidos.append((self._nome, model))
            if self._nome == "gemini":
                raise RuntimeError("gemini fora do ar")
            return "ok"

    monkeypatch.setattr(cadeia, "_get_client", _Cliente)

    cadeia.create(model=cadeia.models["full"], max_tokens=10, system="s",
                  tools=[], messages=[{"role": "user", "content": "oi"}])

    assert pedidos == [
        ("gemini", "gemini-2.5-flash"),
        ("anthropic", "claude-sonnet-5"),   # e NÃO claude-haiku-4-5
    ]


# ── a ordem da cadeia ───────────────────────────────────────────────────────
#
# A versão anterior deste bloco afirmava que o v4-flash "não é modelo thinking"
# e que por isso não queimaria o teto raciocinando. Era falso, e a medição de
# 18/08/2026 desmentiu: os DOIS modelos V4 raciocinam, e o flash esgotou os
# 6.000 tokens em 54,2s devolvendo 0 char. A troca de modelo não resolveu nada
# -- o conserto foi na ordem.

def test_o_gemini_vem_antes_do_deepseek():
    """Medido com cada provedor isolado, mesmo prompt:

        gemini     16,4s   3.567 chars   ok
        deepseek   54,2s   0 chars       esgota o teto raciocinando

    A ordem da cadeia é por TEMPO ATÉ RESPOSTA ÚTIL, não por qualidade: quem
    tem prazo paga cada tentativa que falha antes da que funciona. Com o
    deepseek na 2ª posição e o anthropic falhando antes dele, a Análise com IA
    chegava a ~110s dos 135s e o orçamento recusava justamente o gemini -- o
    provedor mais rápido barrado pelo tempo que o mais lento já queimou."""
    ordem = prov._DEFAULT_ORDER
    assert ordem.index("gemini") < ordem.index("deepseek")


def test_o_primeiro_fallback_e_um_provedor_que_responde():
    """O 2º da ordem é o que herda o pedido quando o principal cai, e é o mais
    caro de errar: o 3º só é tentado se sobrar orçamento depois dele."""
    assert prov._DEFAULT_ORDER[1] == "gemini"


def test_todo_provedor_da_ordem_existe_na_tabela():
    """Nome fora de PROVIDERS não falha alto -- `_has_key` devolve False e ele
    some da cadeia em silêncio, encurtando o fallback sem avisar ninguém."""
    faltando = [p for p in prov._DEFAULT_ORDER if p not in prov.PROVIDERS]
    assert faltando == [], f"provedores na ordem sem configuração: {faltando}"
