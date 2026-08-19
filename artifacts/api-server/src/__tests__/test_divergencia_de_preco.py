"""
Dois preços incompatíveis não podem virar tese de alta.

Produção 18/08/2026. Toda análise daquele dia trazia dois preços: o dos painéis
ao vivo e US$ 225,01 vindo do painel de valuation (FMP). O que cada modelo fez
com a contradição foi MUITO diferente:

  anthropic   "a divergência entre os preços de referência é o ponto mais
              sensível deste relatório" -- e se recusou a concluir sobre upside
              antes de resolver qual preço vale.
  gemini      "Comparado ao preço atual de US$180,00, o DCF aponta um espaço
              ainda maior para valorização."

O gemini transformou dois números incompatíveis num argumento de compra. Não é
uma resposta faltando -- é uma conclusão confiante construída sobre dado ruim,
que é o modo de falhar mais caro numa tela de decisão.

## Por que a culpa não é do modelo

`_preco_canonico` existe justamente para isso: ele compara os painéis e, se
discordarem acima do limite, publica `divergenciaPct` e `porPainel`, e o SYSTEM
manda dizer isso em uma linha. A defesa é ESTRUTURAL -- não depende de o modelo
notar sozinho.

Só que a camada fundamental não estava entre os candidatos. O preço da
valuation, que foi exatamente o que divergiu, nunca era comparado com nada:
`divergenciaPct` não saía, a regra do SYSTEM não tinha o que disparar, e os dois
modelos ficaram por conta própria. Um acertou de sorte, o outro não.

Confiar no julgamento não assistido do modelo é a garantia mais fraca que existe
-- ela varia por provedor, e a cadeia de fallback troca de provedor sozinha.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_divergencia_de_preco.py -v
"""
import pytest

from agent import analise_rapida_ia as mod


def _dados(**painel) -> dict:
    return painel


def _com_valuation(preco_valuation, **painel) -> dict:
    return {**painel, "_fundamento": {"valuation": {"current_price": preco_valuation}}}


# ── o caso que passou batido ────────────────────────────────────────────────

def test_preco_da_valuation_entra_na_comparacao():
    """O caso exato da produção: painéis ao vivo em US$ 180 e a valuation em
    US$ 225,01. Antes de 18/08 isso não gerava aviso nenhum."""
    out = mod._preco_canonico(_com_valuation(225.01, snapshot={"price": 180.0}))

    assert out is not None
    assert "divergenciaPct" in out, (
        "a valuation divergiu 25% e o detector não viu -- o modelo fica por "
        "conta própria justamente no dado que mais engana"
    )
    assert out["porPainel"] == {"niveis": 180.0, "valuation": 225.01}


def test_a_valuation_nao_vira_o_preco_canonico():
    """Ela entra para SER COMPARADA, não para mandar. A FMP atualiza em ritmo
    próprio e é a menos indicada para responder 'onde o papel está agora'."""
    out = mod._preco_canonico(_com_valuation(225.01, snapshot={"price": 180.0}))
    assert out["fonte"] == "niveis"
    assert out["valor"] == 180.0


def test_valuation_sozinha_ainda_serve_de_preco():
    """Sem nenhum painel ao vivo, um preço defasado é melhor que nenhum -- o
    que não pode é ele passar na frente de quem foi buscado agora."""
    out = mod._preco_canonico(_com_valuation(225.01))
    assert out["fonte"] == "valuation"
    assert out["valor"] == 225.01


def test_valuation_alinhada_nao_gera_aviso():
    """Ruído vira cegueira: avisar de divergência quando não há treina o leitor
    (e o modelo) a ignorar o aviso quando ela é real."""
    out = mod._preco_canonico(_com_valuation(180.30, snapshot={"price": 180.0}))
    assert "divergenciaPct" not in out


def test_valuation_sem_preco_nao_atrapalha():
    """A FMP devolve 402 nesta conta desde 18/08 -- o campo simplesmente não
    vem, e isso não pode quebrar a detecção dos outros painéis."""
    dados = {"snapshot": {"price": 180.0}, "technicals": {"price": 195.0},
             "_fundamento": {"valuation": {"error": "402"}}}
    out = mod._preco_canonico(dados)
    assert out["valor"] == 180.0
    assert out["porPainel"] == {"niveis": 180.0, "tecnica": 195.0}


def test_sem_fundamento_nenhum():
    out = mod._preco_canonico(_dados(snapshot={"price": 180.0}))
    assert out == {"valor": 180.0, "fonte": "niveis"}


# ── as regras que o SYSTEM precisa carregar ─────────────────────────────────
#
# Detectar e não instruir seria meio conserto: o campo chega no JSON e o modelo
# não sabe o que fazer com ele.

def test_o_system_manda_expor_a_divergencia():
    assert "divergenciaPct" in mod.SYSTEM
    assert "porPainel" in mod.SYSTEM


def test_o_system_ancora_o_upside_do_DCF():
    """O erro do gemini em uma frase: ele leu `dcf_implied_upside_pct` como
    distância até o preço atual. Ele é calculado contra o preço do próprio
    painel de valuation (tools.py, get_fundamentals_valuation) -- quando os
    dois preços diferem, apresentá-lo sem a base é afirmar algo falso."""
    assert "dcf_implied_upside_pct" in mod.SYSTEM
    assert "valuation.current_price" in mod.SYSTEM


def test_o_upside_do_DCF_e_mesmo_ancorado_no_preco_da_valuation():
    """A regra do SYSTEM só é verdadeira enquanto o cálculo for esse. Se um dia
    o upside passar a usar o preço canônico, a instrução vira mentira -- e
    mentira no prompt é pior que instrução ausente."""
    import pathlib
    fonte = (pathlib.Path(mod.__file__).parent / "tools.py").read_text(encoding="utf-8")
    trecho = fonte.split("dcf_implied_upside_pct", 1)[0][-600:]
    assert "(dcf_value - stock_price) / stock_price" in trecho
