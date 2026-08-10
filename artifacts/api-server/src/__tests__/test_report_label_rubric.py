"""
Testes da rubrica de RÓTULO POR ATIVO no prompt do relatório diário
(agent.py::_system_stable_full).

O relatório diário atribui um rótulo de cor (🟢/🟡/🔴) por ativo do Grupo A.
Antes desta rubrica o emoji não existia em lugar nenhum do prompt -- o modelo
inventava a semântica a cada execução, e o resultado foi rótulo verde em ativo
caindo no dia, com IV extrema, ou com o técnico defasado (visto no relatório
de 02/08: ARM 🟢 caindo 0,8% com RSI 32; SKHY 🟢 caindo 3,5% com IV 137%).

O que estes testes protegem:

1. A rubrica continua no prompt (alguém não a removeu num refactor).
2. Os NOMES DE CAMPO que os gates mandam o modelo comparar existem de verdade
   no retorno das ferramentas. Este é o teste que importa: durante a própria
   implementação o prompt apontou para `atm_iv` quando get_options_data
   devolve `atm_iv_pct`. Um gate que cita campo inexistente manda o modelo
   improvisar de novo -- que é exatamente o bug que a rubrica veio corrigir.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_report_label_rubric.py -v
(conftest.py no mesmo diretório já cuida do sys.path)
"""

import ast
import inspect

import pytest

from agent import tools
from agent.agent import _system_stable_full

# Campo citado no prompt -> ferramenta que precisa devolvê-lo.
# Se um destes for renomeado em tools.py sem atualizar o prompt, o teste quebra.
GATE_FIELDS = {
    "atm_iv_pct": "get_options_data",
    "atr_pct": "get_technical_indicators",
    "rsi_date": "get_technical_indicators",
    "days_until_earnings": "get_earnings_calendar",
}


@pytest.fixture(scope="module")
def prompt() -> str:
    return _system_stable_full()


@pytest.fixture(scope="module")
def fontes_das_tools() -> dict[str, str]:
    """Código-fonte de cada ferramenta, por nome.

    Via AST do módulo em vez de inspect.getsource(fn): as ferramentas são
    embrulhadas por @cached, que não usa functools.wraps nem seta __wrapped__
    -- pegar a fonte pela referência da função devolveria o wrapper genérico
    do cache, e o teste passaria/quebraria pelo motivo errado.
    """
    src = inspect.getsource(tools)
    arvore = ast.parse(src)
    return {
        no.name: ast.get_source_segment(src, no) or ""
        for no in ast.walk(arvore)
        if isinstance(no, ast.FunctionDef)
    }


def test_rubrica_presente(prompt):
    assert "RÓTULO POR ATIVO" in prompt
    for emoji in ("🟢", "🟡", "🔴"):
        assert emoji in prompt, f"rótulo {emoji} sumiu da rubrica"


def test_horizonte_separado_da_tese(prompt):
    """O rótulo é sobre 1-5 pregões; a tese longa tem linha própria.

    Sem essa separação o mesmo emoji acumulava as duas leituras, que foi
    como 'tese boa em 12 meses' virou verde num setup ruim de curto prazo.
    """
    assert "1–5 pregões" in prompt
    assert "Tese (6–12m):" in prompt


@pytest.mark.parametrize("campo", sorted(GATE_FIELDS))
def test_campo_do_gate_existe_na_ferramenta(prompt, fontes_das_tools, campo):
    """Todo campo citado nos gates precisa existir no retorno da ferramenta."""
    assert campo in prompt, f"gate deixou de citar {campo}"
    tool_name = GATE_FIELDS[campo]
    fonte = fontes_das_tools.get(tool_name)
    assert fonte, f"ferramenta {tool_name} não encontrada em tools.py"
    assert f'"{campo}"' in fonte, (
        f"prompt manda comparar `{campo}`, mas {tool_name} não devolve esse campo"
    )


def test_gates_bloqueiam_verde(prompt):
    """Os quatro gates precisam estar enunciados como bloqueio de 🟢."""
    for gate in ("variação do dia negativa", "days_until_earnings", "IV de evento",
                 "defasado", "pct_above_sma200", "risco binário"):
        assert gate in prompt, f"gate ausente: {gate}"


def test_limiar_de_iv_vem_fechado_em_32x(prompt):
    """O prompt precisa dar o multiplicador JÁ CALCULADO.

    A primeira versão pedia "compare atm_iv_pct com atr_pct × 16" e explicava
    o 2× em prosa; o relatório de 02/08 mostrou o modelo comparando contra 16×
    em NVDA, AVGO e ARM -- metade do limiar. Em ARM isso virou um gate alegado
    que não existia.
    """
    assert "32 × `atr_pct`" in prompt
    # e precisa avisar explicitamente contra a decomposição que deu errado
    assert "METADE do limiar" in prompt


def test_prompt_dita_quantos_gates_para_cada_rotulo(prompt):
    """Sem isso 🔴 vira julgamento livre, e o modelo inventa gate para chegar
    lá (visto em produção com ARM)."""
    assert "dois críticos" in prompt
    assert "três ativos" in prompt
    assert "não conte um gate" in prompt


def test_severidade_dos_gates_esta_no_prompt(prompt):
    """Sem peso, contagem simples colapsa: com 8 gates qualquer ativo em
    correção vira 🔴 todo dia e o rótulo para de discriminar."""
    assert "CRÍTICOS" in prompt
    assert "ATIVOS:" in prompt
    assert "INFORMATIVO" in prompt


def test_macro_e_tecnico_generico_ficam_fora_dos_gates(prompt):
    """Gate que não varia entre ativos no mesmo dia não carrega informação --
    o juro 10y foi verdadeiro em todos os relatórios revisados."""
    assert "NÃO são gates" in prompt
    assert "não varia entre ativos" in prompt


def test_gates_nao_se_apresentam_como_sinal_validado(prompt):
    """Convenção do repo: threshold não-backtestado não pode ser citado como
    sinal validado (mesma regra de get_global_market_snapshot e do
    ConfluenceEngine). Os gates governam o TEXTO, não a estratégia."""
    assert "não são sinal de" in prompt
    assert "backtest" in prompt


def test_relatorio_diario_so_cobre_carteira(prompt):
    """Relatório diário passou a cobrir só a carteira (Grupo A =
    PORTFOLIO_TICKERS) -- sem Grupo B (cotação rápida de fora da carteira) e
    sem expandir Grupo A com líderes/catch_up de contágio setorial."""
    assert "Grupo B" not in prompt
    assert 'Tickers marcados como "líder" ou "catch_up"' not in prompt


def test_regra_de_frescor_do_bloco_tecnico(prompt):
    """rsi_date data o bloco técnico INTEIRO (macd/sma/ema saem do mesmo
    histórico), não só o RSI -- o prompt precisa dizer isso, senão o modelo
    trata a defasagem como problema exclusivo do RSI."""
    assert "Frescor do dado técnico" in prompt
    assert "bloco técnico" in prompt
    for indicador in ("sma200", "macd"):
        assert indicador in prompt, f"frescor não menciona {indicador}"
