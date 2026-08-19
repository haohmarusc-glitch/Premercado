"""
Quanto pedir de max_tokens, contando o raciocínio quando o modelo pensa.

Saiu de dentro de `analise_rapida_ia.py` quando a interpretação da Reação a
Earnings passou a precisar do mesmo cálculo. Importar daquele módulo traria
junto os efeitos de import dele (mexe em os.environ, liga a sonda de boot), que
rodariam na hora errada para quem só quer um número.
"""
from __future__ import annotations

import re

# Teto de SEGURANÇA, não de projeto. Histórico: 2500 cortou (16/08, INTC);
# 4500 cortou de novo no mesmo ponto — porque o modelo escrevia até o teto,
# fosse ele qual fosse, ignorando as "400 a 700 palavras" enterradas no fim
# da lista de regras. A correção real foi o SYSTEM (limite por seção, no
# topo, com o motivo); este número só existe para o caso de o modelo
# desobedecer mesmo assim, e aí o corte vai MARCADO (truncado=true).
#
# Não subir mais sem antes checar o texto: teto alto com prompt fraco vira
# custo alto (4500 tokens de saída ≈ US$ 0,045 por análise, contra ~0,015
# de uma análise no tamanho pedido).
MAX_TOKENS = 6000

# Folga de raciocínio, para modelo que PENSA antes de responder.
#
# Em modelo thinking o max_tokens cobre raciocínio + resposta, não só a
# resposta. O mesmo 6000 significa coisas diferentes conforme o provedor, e
# ninguém percebeu porque o primeiro da cadeia (Anthropic) não gasta orçamento
# visível pensando.
#
# Medido em produção 18/08/2026, deepseek-v4-pro: 17.147 caracteres de
# raciocínio -- perto de 4.300 tokens -- e `stop_reason=length` com a resposta
# VAZIA. O modelo pensou até o teto e não sobrou espaço para escrever nada.
#
# 6000 de folga cobre aquele caso com margem. Não é generosidade: o teto de
# baixo continua valendo para a resposta, que é o que o usuário lê e o que
# custa em texto útil.
MAX_TOKENS_RACIOCINIO = 6000

# Heurística por nome, e o detector de truncamento é a rede de segurança.
#
# Uma lista de modelos envelhece -- por isso ela não é a única defesa: quando
# um modelo novo pensar sem estar aqui, o laço de retry ainda vai reconhecer
# `stop_reason=length` e trocar de provedor em vez de devolver texto vazio.
_MODELO_PENSA_RE = re.compile(r"deepseek-v4-pro|reasoner|thinking|-r1\b", re.I)


def teto_de_tokens(modelo: str) -> int:
    """Teto a pedir para ESTE modelo, já contando o raciocínio quando houver."""
    if _MODELO_PENSA_RE.search(modelo or ""):
        return MAX_TOKENS + MAX_TOKENS_RACIOCINIO
    return MAX_TOKENS
