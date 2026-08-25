"""
Padrões estatísticos por ticker -- e a propriedade que dá sentido ao módulo:
RUÍDO NÃO PODE VIRAR PADRÃO.

Varrer 12 meses + 5 dias da semana + eventos macro produz ~21 testes. A 5%,
um em cada vinte "dá significativo" por acaso -- e o achado sempre vem com
uma história convincente depois de encontrado. O teste central aqui gera uma
série SEM nenhum padrão embutido e exige que o relatório diga isso; se
alguém afrouxar a correção de múltiplos testes, é este teste que cai.

Os demais fixam a aritmética (permutação, Holm, beta/R², bordas de amostra)
com fixtures sintéticas, sem rede.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_padroes_estatisticos.py -v
"""
import numpy as np
import pandas as pd
import pytest

from agent import padroes_estatisticos as pe


def _serie(valores, inicio="2021-01-04"):
    return pd.Series(valores, index=pd.bdate_range(inicio, periods=len(valores)), dtype=float)


def _ruido(n=760, semente=42, vol=0.02):
    rng = np.random.default_rng(semente)
    return _serie(rng.normal(0, vol, n))


# ── a propriedade central ────────────────────────────────────────────────────

def test_ruido_puro_nao_produz_nenhum_padrao():
    """Série sem padrão nenhum: o relatório TEM que dizer que nada sobrevive.
    Sem a correção de Holm, ~1 dos 17 testes passaria por acaso e viraria
    linha de tabela com cara de descoberta."""
    rel = pe.montar_relatorio("RUIDO", _ruido(), eventos={}, series_fatores={})
    assert rel["sobreviventes"] == 0
    assert rel["testados"] >= 15
    assert "Nenhum dos" in rel["veredito"]
    assert "ruído" in rel["veredito"]

def test_padrao_forte_de_verdade_sobrevive():
    """O contrapositivo: se houver um efeito grande e consistente, o módulo
    não pode ser cego a ele -- senão a correção viraria censura."""
    rng = np.random.default_rng(7)
    base = rng.normal(0, 0.01, 760)
    s = _serie(base)
    # Janeiro com +3% ao dia: absurdo de propósito, é o teste do detector.
    s[s.index.month == 1] += 0.03
    rel = pe.montar_relatorio("FAKE", s, eventos={}, series_fatores={})
    jan = next(p for p in rel["sazonalidade"] if p["rotulo"] == "jan")
    assert jan["sobrevive"] is True
    assert rel["sobreviventes"] >= 1
    assert "sobrevive" in rel["veredito"].lower()


# ── teste de permutação ──────────────────────────────────────────────────────

def test_permutacao_detecta_diferenca_real():
    grupo = np.full(40, 0.05)
    resto = np.full(200, -0.01)
    assert pe.teste_permutacao(grupo, resto, amostras=500) < 0.01

def test_permutacao_nao_ve_diferenca_onde_nao_ha():
    rng = np.random.default_rng(3)
    todos = rng.normal(0, 0.02, 300)
    assert pe.teste_permutacao(todos[:100], todos[100:], amostras=500) > 0.05

def test_p_valor_nunca_e_zero():
    """"Não vi em N sorteios" não é "impossível" -- p=0 num relatório vira
    certeza que a amostra não sustenta."""
    p = pe.teste_permutacao(np.full(30, 10.0), np.full(30, -10.0), amostras=200)
    assert p > 0

def test_amostra_pequena_nao_e_testada():
    assert pe.teste_permutacao(np.array([0.01, 0.02]), np.full(50, 0.0)) is None

def test_permutacao_e_reproduzivel():
    rng = np.random.default_rng(11)
    a, b = rng.normal(0, 0.02, 60), rng.normal(0.001, 0.02, 180)
    assert pe.teste_permutacao(a, b, amostras=300) == pe.teste_permutacao(a, b, amostras=300)


# ── Holm ─────────────────────────────────────────────────────────────────────

def test_holm_e_mais_rigoroso_que_o_alfa_cru():
    """p=0.04 passaria sozinho a 5%; entre 20 testes, não passa."""
    padroes = [{"p_valor": 0.04}] + [{"p_valor": 0.5} for _ in range(19)]
    pe.holm(padroes)
    assert padroes[0]["sobrevive"] is False

def test_holm_deixa_passar_o_que_e_forte():
    padroes = [{"p_valor": 0.0001}] + [{"p_valor": 0.6} for _ in range(19)]
    pe.holm(padroes)
    assert padroes[0]["sobrevive"] is True

def test_holm_para_na_primeira_falha():
    """Ordenado por p: se o 2º falha, o 3º não pode 'passar por sorte'."""
    padroes = [{"p_valor": 0.001}, {"p_valor": 0.30}, {"p_valor": 0.31}]
    pe.holm(padroes)
    assert [p["sobrevive"] for p in padroes] == [True, False, False]

def test_holm_ignora_padrao_sem_p_valor():
    padroes = [{"p_valor": None}, {"p_valor": 0.001}]
    pe.holm(padroes)
    assert padroes[0]["sobrevive"] is False and padroes[1]["sobrevive"] is True


# ── agrupamentos ─────────────────────────────────────────────────────────────

def test_sazonalidade_cobre_os_doze_meses_com_n_correto():
    s = _serie(np.zeros(500))
    linhas = pe.analisar_sazonalidade(s)
    assert [l["rotulo"] for l in linhas] == pe.MESES_PT
    assert sum(l["n"] for l in linhas) == len(s)

def test_dia_da_semana_ignora_fim_de_semana_e_soma_tudo():
    s = _serie(np.zeros(260))
    linhas = pe.analisar_dia_semana(s)
    assert [l["rotulo"] for l in linhas] == pe.DIAS_PT
    assert sum(l["n"] for l in linhas) == len(s)

def test_evento_macro_casa_pela_data_do_pregao():
    s = _serie(np.zeros(60))
    datas = [str(d)[:10] for d in s.index[:10]]
    linha = pe.analisar_eventos_macro(s, {"FOMC": datas})[0]
    assert linha["rotulo"] == "dias de FOMC"
    assert linha["n"] == 10

def test_linha_de_amostra_curta_explica_por_que_ficou_de_fora():
    # Começa no fim de fevereiro: o mês fica com 5 pregões, abaixo do mínimo.
    s = _serie(np.zeros(26), inicio="2021-02-22")
    curtas = [l for l in pe.analisar_sazonalidade(s) if l["n"] < pe.MIN_OBS and l["n"] > 0]
    assert curtas, "fixture precisa ter ao menos um mês curto"
    assert all("nota" in l and l["p_valor"] is None for l in curtas)


# ── fatores ──────────────────────────────────────────────────────────────────

def test_beta_e_r2_em_relacao_perfeita():
    x = np.linspace(-0.05, 0.05, 100)
    r = pe.beta_e_r2(2.0 * x, x)
    assert r["beta"] == pytest.approx(2.0, abs=0.001)
    assert r["r2"] == pytest.approx(1.0, abs=0.001)

def test_r2_baixo_denuncia_beta_que_nao_explica_nada():
    """O ponto do R²: beta existe, explicação não."""
    rng = np.random.default_rng(5)
    x = rng.normal(0, 0.02, 400)
    y = rng.normal(0, 0.02, 400)
    r = pe.beta_e_r2(y, x)
    assert r["r2"] < pe.R2_RELEVANTE

def test_fator_de_nivel_usa_variacao_nao_o_nivel():
    """Juros/VIX entram por diff: regredir retorno contra NÍVEL de taxa é
    espúrio (duas séries com tendência 'explicam' uma à outra)."""
    ret = _serie(np.full(200, 0.001))
    nivel = pd.Series(np.linspace(1.0, 5.0, 200), index=ret.index)  # só sobe
    saida = pe.sensibilidade_a_fatores(ret, {"Juros": (nivel, "variacao_nivel")})[0]
    assert saida["modo"] == "variacao_nivel"
    # retorno constante: variação do nível não explica nada
    assert saida["r2"] == pytest.approx(0.0, abs=0.01) or saida["r2"] is None

def test_fator_sem_sobreposicao_declara_em_vez_de_inventar():
    ret = _serie(np.zeros(50), inicio="2021-01-04")
    outra = pd.Series(np.zeros(50), index=pd.bdate_range("2015-01-05", periods=50))
    saida = pe.sensibilidade_a_fatores(ret, {"X": (outra, "retorno")})[0]
    assert saida["beta"] is None and "nota" in saida

def test_leitura_de_fatores_diz_quando_nada_explica():
    rng = np.random.default_rng(9)
    ret = _serie(rng.normal(0, 0.02, 300))
    fator = pd.Series(rng.normal(0, 0.02, 300), index=ret.index)
    rel = pe.montar_relatorio("X", ret, {}, {"Aleatório": (fator, "retorno")})
    assert "idiossincrático" in rel["leituraFatores"]
