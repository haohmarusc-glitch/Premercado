"""
Qual provedor atende as telas interativas de análise, e em que ordem.

Vive fora de `analise_rapida_ia.py` desde que a segunda tela (interpretação da
Reação a Earnings) passou a precisar da mesma política. Copiar seria a TERCEIRA
cópia da sequência de provedores no repo -- as outras duas, provider.py e
agent-budget.ts, já divergiram uma vez (#327) e ganharam um teste de sincronia
por causa disso. A regra do playbook §10 vale aqui inteira.

Módulo sem efeito colateral de import de propósito: `analise_rapida_ia` mexe em
os.environ e liga a sonda de boot no import, e importar aquilo de outro script
rodaria as duas coisas na hora errada.
"""
from __future__ import annotations

import os

try:
    from provider import _DEFAULT_ORDER as ORDEM_PADRAO
except ImportError:
    from agent.provider import _DEFAULT_ORDER as ORDEM_PADRAO

# Provedores que não CONVERGEM em redigir análise longa -- não que estejam fora
# do ar.
#
# O deepseek (v4-pro e v4-flash) gasta o max_tokens inteiro raciocinando e nunca
# chega à resposta. Medido quatro vezes em 18-19/08/2026, com duas versões de
# modelo e duas versões do prompt:
#
#   v4-pro    teto 12.000   142,2s   0 chars   (17.806 chars de raciocínio)
#   v4-flash  teto  6.000    54,2s   0 chars   (esgotou os 6.000 tokens)
#   v4-flash  teto  6.000    52,7s   0 chars   (com o prompt 27% menor)
#
# Num prompt trivial ele responde em 1s. O problema é a TAREFA: redigir texto
# longo e estruturado. Dobrar o teto dobrou o tempo sem produzir texto -- o
# raciocínio se expande para preencher o que houver.
#
# Fica FORA daqui e DENTRO da cadeia global: o v4-flash é forte em tool-calling,
# que é o formato do agente diário, e esse uso nunca falhou. Excluí-lo lá
# puniria um caminho que funciona por causa de outro que não.
SEM_CONVERGENCIA = {"deepseek"}

# Escotilha de saída, estreita e nomeada. Reabilitar um provedor excluído exige
# pedir POR ELE -- um AGENT_PROVIDER_ORDER genérico não serve.
#
# A primeira versão desta exclusão só agia quando AGENT_PROVIDER_ORDER estava
# vazia, e isso deixava uma porta dos fundos: bastaria alguém definir a ordem no
# compose por outro motivo para o deepseek voltar à cadeia sem ninguém notar.
PERMITIR_ENV = "ANALISE_IA_PERMITIR"

# Quem abre a fila nestas telas, contra o anthropic da ordem global.
#
# O que decidiu não foi o preço (embora o gemini custe ~12x menos por token),
# foi o FORMATO DA FALHA. Na cascata de 19/08/2026 o anthropic queimou 55,1s
# sendo cortado no teto e o gemini falhou em 11,1s com um 503 de capacidade.
# Quem falha barato na frente deixa orçamento para o próximo; quem falha caro
# não deixa.
#
# Só nas telas: o agente diário usa a cadeia em formato de tool-calling, onde a
# medição acima não vale, e mexer em _DEFAULT_ORDER mudaria os dois.
PRIMEIRO = "gemini"


def ordem_desta_tela(bruta: str = "", permitidos: str = "") -> list[str]:
    """Ordem efetiva: sem quem não converge, e com o mais rápido na frente.

    A promoção só vale quando a ordem veio do DEFAULT. Ordem explícita em
    AGENT_PROVIDER_ORDER é decisão de quem operou, e reordená-la por trás
    tornaria a variável mentirosa -- ao contrário da exclusão do deepseek, que
    vale para qualquer origem porque é regra de correção (ele não entrega texto
    nenhum aqui), não preferência de velocidade.
    """
    explicita = [p.strip() for p in bruta.split(",") if p.strip()]
    origem = explicita or list(ORDEM_PADRAO)
    liberados = {p.strip() for p in permitidos.split(",") if p.strip()}
    ordem = [p for p in origem if p not in (SEM_CONVERGENCIA - liberados)]
    if not explicita and PRIMEIRO in ordem:
        ordem.insert(0, ordem.pop(ordem.index(PRIMEIRO)))
    return ordem


def aplicar_na_env() -> list[str]:
    """Grava a ordem desta tela em AGENT_PROVIDER_ORDER e devolve o que gravou.

    Tem de rodar ANTES do primeiro `get_client()`: o FallbackClient lê a
    variável no construtor, e depois disso mudá-la não move mais nada.
    """
    ordem = ordem_desta_tela(
        os.environ.get("AGENT_PROVIDER_ORDER", ""),
        os.environ.get(PERMITIR_ENV, ""),
    )
    os.environ["AGENT_PROVIDER_ORDER"] = ",".join(ordem)
    return ordem
