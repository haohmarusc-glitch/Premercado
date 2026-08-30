"""Busca de série diária passa pelo provider — e cada exceção diz por quê.

Por que existe um funil: `market_data_provider` é onde moram o cache em
disco, o disjuntor por fonte, o fallback para a Alpha Vantage e a limpeza da
barra sem Close. Quem vai direto ao yfinance não recebe nada disso, e a
diferença não aparece no número — ela aparece num dia de rate limit, ou numa
manhã antes da abertura, e sempre em silêncio.

O incidente que fundou a regra (18/08/2026): a barra do dia corrente chega
sem Close, vira a ÚLTIMA linha, e todo `Close.iloc[-1]` pega NaN. Dois
painéis no mesmo dia -- a Técnica devolvia `price=NaN` e, como NaN não é
JSON válido, a resposta INTEIRA morria no JSON.parse do Node; a Reação a
Earnings perdia R1/R2/S1/S2, que derivam do preço.

Este arquivo é o segundo turno da mesma ideia do
test_scripts_de_spawn_importam.py: lá o funil é de PROCESSO, aqui é de DADO.
Nos dois, o valor está em a lista de exceções ser explícita e justificada --
uma exceção sem motivo escrito é uma migração esquecida se passando por
decisão.
"""

import pathlib
import re

import pytest

_AGENT = pathlib.Path(__file__).resolve().parent.parent / "agent"

# `yf.Ticker(x).history(...)` e `yf.download(...)` em código de verdade.
_BUSCA_DIRETA = re.compile(r"yf\.Ticker\([^)]*\)\.history\(|yf\.download\(")

# Cada entrada é {arquivo: motivo}. O motivo não é decoração: é o que separa
# "decidimos manter" de "ninguém migrou ainda", e a distinção some em seis
# meses se não estiver escrita.
_EXCECOES: dict[str, str] = {
    # ── são o próprio funil ──────────────────────────────────────────────
    "market_data_provider.py":
        "é o provider: o yfinance mora aqui, é este o ponto único",
    "alpha_vantage_provider.py":
        "é a fonte externa do fallback; só aparece numa docstring",

    # ── intradiário: o provider é diário, não cobre ──────────────────────
    "get_technicals.py":
        "barras de 5m para RVOL e VWAP da sessão -- o provider só faz diária",
    "market_alerts.py":
        "duas leituras de 1m (spike e fade intradiário) mais UM caminho "
        "diário que já é fallback: o provider é tentado primeiro, e o "
        "yfinance direto só roda quando ele não devolveu nada",

    # ── faixa de datas: API que o provider não tem ───────────────────────
    "backtest.py":
        "start=/end= -- backtest pede janela histórica exata, e "
        "get_daily_history só aceita period=",
    "confluence_engine.py":
        "start=/end= no histórico do backtest de confluência; os dois "
        "tickers macro (juros e petróleo), que são period=, já migraram",
    "earnings_reaction_analysis.py":
        "start= a partir da data do balanço -- a janela nasce do evento, "
        "não de um period fixo",
    "get_historical_price.py":
        "start=/end= vindos do lote de compra da carteira",

    # ── intervalo variável: o chamador escolhe, e pode ser intradiário ───
    "risk_manager.py":
        "`interval` é PARÂMETRO da razão de hedge e pode ser intradiário; "
        "o lote do provider é diário-only. A correlação e as métricas de "
        "carteira, essas sim, já usam get_daily_closes_batch",
    "sector_contagion.py":
        "`interval` é parâmetro: o contágio roda em modo diário e em modo "
        "5m (pré-mercado), e a segunda forma o provider não cobre",

    # ── casos com motivo próprio ─────────────────────────────────────────
    "llm_runtime.py":
        "`_fetch_veredito_quote` é a verdade contra a qual o texto do LLM é "
        "conferido; servir cache vencido aqui validaria a prosa contra dado "
        "velho. Já aplica sem_barra_incompleta por conta própria",
}

# scripts/ são ferramentas de pesquisa rodadas à mão, fora do caminho servido
# ao usuário: não valem o acoplamento ao disjuntor e ao cache compartilhado.
_DIRETORIOS_FORA = {"scripts"}


def _modulos_com_busca_direta() -> dict[str, list[int]]:
    achados: dict[str, list[int]] = {}
    for p in sorted(_AGENT.rglob("*.py")):
        if set(p.relative_to(_AGENT).parts[:-1]) & _DIRETORIOS_FORA:
            continue
        linhas = [
            i for i, linha in enumerate(
                p.read_text(encoding="utf-8").splitlines(), 1)
            if _BUSCA_DIRETA.search(linha) and not linha.strip().startswith("#")
        ]
        if linhas:
            achados[p.name] = linhas
    return achados


def test_toda_busca_direta_esta_na_lista_de_excecoes():
    """A guarda. Um módulo novo que fale com o yfinance sem passar pelo
    provider reprova aqui, e o autor tem de escolher: migrar, ou escrever o
    motivo de não migrar."""
    fora = {
        nome: linhas for nome, linhas in _modulos_com_busca_direta().items()
        if nome not in _EXCECOES
    }
    assert not fora, (
        "busca direta ao yfinance fora do provider: "
        + "; ".join(f"{n} (linhas {ls})" for n, ls in sorted(fora.items()))
        + ". Use market_data_provider.get_daily_history/get_daily_closes_batch, "
        "ou acrescente o arquivo a _EXCECOES com o motivo."
    )


def test_a_lista_de_excecoes_nao_tem_entrada_morta():
    """O outro lado, que é o que faz listas assim envelhecerem bem: quem
    migra um módulo tem de tirar a exceção junto, senão ela vira permissão
    permanente para o próximo que passar por ali."""
    reais = _modulos_com_busca_direta()
    mortas = sorted(set(_EXCECOES) - set(reais))
    assert not mortas, (
        f"exceções que não correspondem a nenhuma busca direta: {mortas} -- "
        "o módulo migrou; tire a entrada de _EXCECOES")


@pytest.mark.parametrize("nome,motivo", sorted(_EXCECOES.items()))
def test_toda_excecao_tem_motivo_de_verdade(nome, motivo):
    """Motivo de uma palavra ("legado", "ok") não distingue decisão de
    esquecimento -- que é a única coisa que esta lista precisa distinguir."""
    assert len(motivo) >= 40, f"{nome}: motivo curto demais para servir"


def test_os_consumidores_que_migraram_nao_voltaram():
    """Os quatro do ponto 2, travados pelo nome. Sem isto, um `git revert`
    parcial ou um merge desatento devolve o bypass sem reprovar nada -- e o
    sintoma só apareceria num dia de rate limit."""
    migrados = {
        "atualizar_correlacoes.py": "market_data_provider.get_daily_closes_batch",
        "get_macro.py": "market_data_provider.get_daily_closes_batch",
        "padroes_estatisticos.py": "market_data_provider.get_daily_history",
        "confluence_engine.py": "market_data_provider.get_daily_history",
    }
    for nome, chamada in migrados.items():
        fonte = (_AGENT / nome).read_text(encoding="utf-8")
        assert chamada in fonte, f"{nome} parou de usar {chamada}"
