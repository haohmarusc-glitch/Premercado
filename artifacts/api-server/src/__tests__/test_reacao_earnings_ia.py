"""
A interpretação da cesta não pode gastar token para dizer o que já está na tela,
nem prometer mais do que 8 eventos por ticker sustentam.

Estes testes rodam SEM rede -- nenhum chama o LLM. O que eles fixam é o que
acontece ANTES da chamada (guardas de entrada, o que entra no prompt) e a
aritmética de orçamento, que atravessa duas linguagens.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_reacao_earnings_ia.py -v
"""
import json
import pathlib
import re

import pytest

from agent import reacao_earnings_ia as mod


def _resultado(ticker: str, **extra) -> dict:
    base = {
        "ticker": ticker,
        "summary": {"n_events": 8, "close_pct_abs_mean": 4.2,
                    "suggested_threshold_pct": 7.1, "current_price": 100.0},
        # A tela manda os eventos junto; o prompt não os quer.
        "events": [{"date": f"2026-0{i}-01", "gap_pct": 1.0 * i,
                    "trajetoria": [{"dia": d, "acum_pct": 0.5 * d} for d in range(1, 6)]}
                   for i in range(1, 9)],
    }
    base.update(extra)
    return base


# ── guardas antes de gastar token ───────────────────────────────────────────

def test_sem_resultados_nao_chama_o_llm():
    """Pedir interpretação antes de rodar a análise é erro de uso, não de
    provedor -- e chamar o LLM para ele dizer isso custaria tokens."""
    saida = mod.interpretar({})
    assert "error" in saida
    assert "Rode a análise" in saida["error"]


def test_lista_vazia_tambem():
    assert "error" in mod.interpretar({"results": []})


def test_results_de_tipo_errado_nao_estoura():
    """O corpo vem de requisição HTTP -- tipo errado é entrada, não bug."""
    assert "error" in mod.interpretar({"results": "NVDA,AMD"})


def test_cesta_inteira_com_erro_nao_chama_o_llm():
    """Sem nenhum `summary` não há o que comparar.

    Chamar o modelo para ele redigir "não há dados" produz a mesma frase que
    este guarda produz de graça -- e a tela já mostra o erro de cada papel.
    """
    saida = mod.interpretar({"results": [
        {"ticker": "NVDA", "error": "sem earnings passados na janela pedida"},
        {"ticker": "AMD", "error": "sem histórico de preço no período"},
    ]})
    assert "error" in saida
    assert "comparar" in saida["error"]


# ── o que entra no prompt ───────────────────────────────────────────────────

def test_events_ficam_de_fora_do_prompt():
    """8 eventos x 5 papéis, cada um com trajetória dia a dia, passam de 40k
    chars -- e a comparação usa `summary`, não o evento individual."""
    dados = json.loads(mod._compactar([_resultado("NVDA"), _resultado("AMD")]))
    # Pela ESTRUTURA, não por substring: `n_events` vive dentro de `summary` e
    # contém "events", então procurar a palavra no texto cru reprova o código
    # certo. Foi o que este teste fez na primeira versão.
    assert all("events" not in d for d in dados)
    assert all(d["summary"] for d in dados)


def test_erro_e_stale_sobrevivem_ao_enxugamento():
    """Os dois viram frase no texto final: ticker que não foi analisado tem de
    aparecer como ausente da comparação, e agenda de cache vencido tem de ir
    para as Ressalvas. Cortá-los aqui apagaria a ressalva na origem."""
    texto = mod._compactar([
        {"ticker": "NVDA", "error": "sem histórico"},
        _resultado("AMD", stale=True),
    ])
    dados = json.loads(texto)
    assert dados[0]["error"] == "sem histórico"
    assert dados[1]["stale"] is True


def test_entrada_lixo_na_lista_e_ignorada_sem_derrubar():
    texto = mod._compactar([None, "NVDA", 42, _resultado("AMD")])
    dados = json.loads(texto)
    assert [d["ticker"] for d in dados] == ["AMD"]


def test_o_prompt_tem_teto():
    """Payload anômalo não pode virar prompt gigante cobrado por token."""
    gigante = [_resultado(f"T{i}", summary={"n": "x" * 2000}) for i in range(100)]
    assert len(mod._compactar(gigante)) <= mod.MAX_DADOS_CHARS


# ── o que o SYSTEM tem de cobrar ────────────────────────────────────────────

def test_o_system_proibe_repetir_a_leitura_por_ticker():
    """A razão de ser desta tela: `interpretResult` já diz, ao lado de cada
    papel, classe de volatilidade e viés. Sem esta regra o modelo redige em
    prosa o que está escrito na tabela, e a IA vira custo sem acréscimo."""
    assert "Compare" in mod.SYSTEM
    assert "JÁ mostra" in mod.SYSTEM


def test_o_system_cobra_a_forca_da_amostra():
    """~8 eventos por ticker. Nesse tamanho, 'sempre' e 'toda vez' são falsos
    por construção, e é o erro mais caro que este texto pode cometer."""
    assert "8 earnings" in mod.SYSTEM
    assert "nunca 'sempre'" in mod.SYSTEM


def test_o_system_separa_R1_de_resistencia():
    """R1/R2/S1/S2 são projeção da magnitude histórica sobre o preço atual, não
    estrutura de preço. Chamá-los de resistência transforma estatística
    descritiva em nível técnico que ninguém mediu."""
    assert "não são suporte e resistência" in mod.SYSTEM


def test_o_system_pede_o_primeiro_cabecalho_direto():
    """Preâmbulo antes da primeira seção apareceu em produção com o gemini
    ('Segue a análise de NVDA com base nos dados fornecidos:')."""
    assert "sem nenhuma frase de abertura" in mod.SYSTEM


# ── orçamento: a invariante que atravessa linguagens ────────────────────────

_ROTA = (pathlib.Path(__file__).resolve().parent.parent
         / "routes" / "earnings-reaction.ts").read_text(encoding="utf-8")


def _timeout_da_rota_s() -> float:
    m = re.search(r"const TIMEOUT_IA_MS = ([\d_]+);", _ROTA)
    assert m, "não achei TIMEOUT_IA_MS em routes/earnings-reaction.ts"
    return int(m.group(1).replace("_", "")) / 1000


def test_orcamento_do_python_cabe_no_timeout_do_node():
    """Playbook §3. Se quebrar, o Node mata o processo no meio e o usuário
    recebe um 500 genérico em vez de erro legível."""
    assert mod._ORCAMENTO_TOTAL_S < _timeout_da_rota_s(), (
        f"orçamento interno ({mod._ORCAMENTO_TOTAL_S}s) >= timeout do Node "
        f"({_timeout_da_rota_s()}s)"
    )


def test_duas_tentativas_de_provedor_cabem():
    """A cadeia precisa conseguir trocar de provedor ao menos uma vez, senão um
    provedor lento derruba a interpretação inteira -- que foi exatamente o que
    aconteceu na Análise Rápida em 19/08/2026."""
    assert 2 * mod._LLM_TIMEOUT_S <= mod._ORCAMENTO_TOTAL_S


def test_o_prazo_vai_para_o_cliente():
    """Ter o mecanismo e não usá-lo deixaria tudo como estava: `create()`
    percorre a cadeia por dentro, sem devolver o controle."""
    fonte = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    i_def = next(i for i, l in enumerate(codigo) if "definir_orcamento(" in l)
    i_create = next(i for i, l in enumerate(codigo) if "client.create(" in l)
    assert i_def < i_create


# ── a política de provedores é UMA só ───────────────────────────────────────

def test_usa_a_mesma_politica_da_analise_rapida():
    """Copiar a ordem aqui seria a terceira cópia da sequência no repo -- as
    outras duas (provider.py e agent-budget.ts) já divergiram uma vez e
    ganharam teste de sincronia por causa disso (playbook §10)."""
    fonte = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    assert "from agent.ordem_das_telas import" in fonte
    assert "deepseek" not in fonte.replace("# ", "")


def test_a_ordem_e_aplicada_antes_do_primeiro_cliente():
    """O FallbackClient lê AGENT_PROVIDER_ORDER no construtor; aplicar depois
    não move mais nada."""
    fonte = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    i_ordem = next(i for i, l in enumerate(codigo) if "_aplicar_ordem_na_env()" in l)
    i_client = next(i for i, l in enumerate(codigo) if "get_client()" in l)
    assert i_ordem < i_client


# ── um papel só não é cesta ─────────────────────────────────────────────────
#
# Medido em 19/08/2026 com WOLF sozinho: a seção "Quem se move junto" -- que é
# sobre co-movimento -- foi preenchida com a leitura individual (gap que atenua
# até o fechamento, trajetória pós-evento), que é exatamente o que o card do
# papel já mostra. O desenho degenerou no que ele existe para não fazer.

def test_um_ticker_so_nao_chama_o_llm():
    saida = mod.interpretar({"results": [_resultado("WOLF")]})
    assert "error" in saida
    assert "COMPARA" in saida["error"]
    assert "WOLF" in saida["error"]


def test_um_com_dados_e_um_com_erro_tambem_recusa():
    """O que conta é quantos são COMPARÁVEIS, não quantos foram pedidos."""
    saida = mod.interpretar({"results": [
        _resultado("WOLF"),
        {"ticker": "NVDA", "error": "sem histórico"},
    ]})
    assert "error" in saida
    assert "COMPARA" in saida["error"]


def test_o_system_proibe_previsao_a_partir_de_bucket():
    """O modelo escreveu 'papel em deságio tende a sofrer reações mais
    severas' a partir de 4 eventos divididos em dois buckets. Descrever o que
    aconteceu é legítimo; dizer o que acontece, não."""
    assert "nunca sobre o que acontece" in mod.SYSTEM
    assert "abaixo de 6" in mod.SYSTEM


def test_o_system_probe_vies_direcional_abaixo_do_corte_do_card():
    """Auditoria de 27/08/2026: 'AVGO ... close_pct_mean positivo de 0.99%,
    indicando que tende a manter ou ampliar ganhos' -- o card do próprio
    AVGO chama 0,99% de 'sem viés direcional claro'. O corte (1pp) tem que
    estar no SYSTEM, não só no validador -- senão o modelo nunca teve
    chance de acertar sozinho."""
    assert "abaixo de 1" in mod.SYSTEM
    assert "sem viés direcional claro" in mod.SYSTEM


# ── correlações do radar no payload ─────────────────────────────────────────
#
# A seção "Quem se move junto" nasceu manca: só havia estatística POR PAPEL, e
# co-movimento é propriedade de PARES. Na primeira cesta real (20/08/2026) o
# modelo respondeu -- corretamente -- que não podia afirmar padrão conjunto.
# O dado já existia na matriz do radar; agora ele viaja no prompt.

def test_pares_da_cesta_vem_do_radar():
    c = mod._correlacoes_da_cesta(["NVDA", "AVGO", "SMCI"])
    assert c is not None
    assert "AVGO|NVDA" in c["pares"]          # chave em ordem alfabética
    assert 0 < c["pares"]["AVGO|NVDA"] <= 1
    # A janela viaja junto (convenção 17): correlação sem data-fim parece
    # medição de hoje mesmo vinda do snapshot embutido.
    assert len(c["janela_fim"]) == 10


def test_par_nao_medido_fica_fora_em_vez_de_virar_null():
    """null no prompt convida o modelo a citá-lo; ausente já tem regra própria
    no SYSTEM (não medido != zero)."""
    c = mod._correlacoes_da_cesta(["NVDA", "AVGO", "SKHY"])
    assert c is not None
    assert not any("SKHY" in k for k in c["pares"])


def test_cesta_sem_nenhum_par_vira_none():
    assert mod._correlacoes_da_cesta(["ULTA", "KO"]) is None
    assert mod._correlacoes_da_cesta([]) is None


def test_ticker_duplicado_ou_minusculo_nao_duplica_par():
    c = mod._correlacoes_da_cesta(["nvda", "NVDA", "avgo"])
    assert list(c["pares"]) == ["AVGO|NVDA"]


def test_o_system_ancora_o_comovimento_nas_correlacoes():
    """As três regras que impedem a seção de voltar a ser manca OU virar
    invenção: os limiares de leitura, ausente != zero, e o que dizer quando o
    bloco inteiro não vier."""
    assert "mesmo trade" in mod.SYSTEM
    assert "não medido" in mod.SYSTEM.lower() or "NÃO MEDIDA" in mod.SYSTEM
    assert "não pode ser afirmado" in mod.SYSTEM


def test_as_correlacoes_entram_antes_da_chamada():
    """Fonte, como os testes de ordem: o bloco tem que estar montado no
    conteúdo antes do client.create."""
    import pathlib
    fonte = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    i_corr = next(i for i, l in enumerate(codigo) if "_correlacoes_da_cesta(" in l and "def " not in l)
    i_create = next(i for i, l in enumerate(codigo) if "client.create(" in l)
    assert i_corr < i_create
