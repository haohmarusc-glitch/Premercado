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


# ── por que o deepseek saiu do v4-pro ───────────────────────────────────────

def test_o_full_do_deepseek_nao_e_modelo_thinking():
    """Produção 18/08/2026: o v4-pro gastou 17.806 chars de raciocínio para
    devolver 0 char visível em 142s; depois que o teto de 55s passou a valer,
    virou timeout puro (3 tentativas medidas, 3 estouros, a última em 55,8s).

    Ele é o PRIMEIRO fallback da cadeia, então esses 55s saíam do orçamento de
    quem tem prazo -- a Análise com IA chegava a 110s dos 135s e o orçamento
    recusava o provedor seguinte por não caber outra tentativa."""
    from agent import analise_rapida_ia as mod

    full = prov.PROVIDERS["deepseek"]["models"]["full"]
    assert not mod._MODELO_PENSA_RE.search(full), (
        f"'{full}' conta raciocínio contra o max_tokens; como primeiro fallback "
        f"da cadeia ele queima o orçamento de quem tem prazo"
    )
    # E o teto pedido volta a ser o normal, sem a folga de raciocínio.
    assert mod.teto_de_tokens(full) == mod.MAX_TOKENS
