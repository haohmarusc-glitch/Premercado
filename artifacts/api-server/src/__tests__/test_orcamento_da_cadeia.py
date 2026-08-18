"""
A cadeia de fallback anda POR DENTRO de um create() — e precisa de prazo.

Produção 18/08/2026, Análise com IA:

    [provider] anthropic failed: Request timed out or interrupted.
    [provider] trying deepseek...
    analise_rapida_ia: estourou o orçamento de tempo   (stdoutParcial: 0)

O processo foi morto pelo Node aos 150s, sem análise e sem erro legível.

## O erro de contagem

O orçamento do analise_rapida_ia era escrito assim:

    teto_fundamento + 2 x _LLM_TIMEOUT_S <= _ORCAMENTO_TOTAL_S
             25     +      2 x 55        =  135

E havia um teste fixando essa conta. Ele passava. Mas a conta descrevia um
mundo em que UMA chamada é UMA tentativa, e nunca foi esse: `create()`
percorre a cadeia inteira por dentro, sem devolver o controle. Com seis
provedores configurados, uma única chamada pode custar 6 x 55s = 330s contra
135s de orçamento.

Contar tentativas de fora era o erro. Quem tem o prazo é o chamador, e é ele
que precisa passá-lo para dentro -- por isso `definir_orcamento`.

## O default importa tanto quanto o limite

Sem prazo definido, a cadeia percorre tudo, como sempre fez. O agente diário
roda em janela de 10 minutos e prefere gastar tempo a voltar sem resposta;
impor limite a ele "de brinde" seria trocar um bug por outro, do outro lado.

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_orcamento_da_cadeia.py -v
"""
import time

import pytest

from agent import provider as prov


@pytest.fixture
def cadeia(monkeypatch):
    """Três provedores, sem rede e sem chaves — mesmo dublê de
    test_pular_provedor.py."""
    monkeypatch.setattr(prov, "_provider_order", lambda: ["anthropic", "gemini", "openrouter"])
    monkeypatch.setattr(prov, "_has_key", lambda _p: True)
    return prov.FallbackClient()


def _fazer_falhar(cadeia, monkeypatch, tentados: list):
    """Todo provedor levanta. Registra a ordem em que foram tentados."""
    class _ClienteQueFalha:
        def __init__(self, nome):
            self.models = {"full": f"modelo-{nome}"}
            self._nome = nome

        def create(self, **_kw):
            tentados.append(self._nome)
            raise RuntimeError(f"{self._nome} indisponível")

    monkeypatch.setattr(cadeia, "_get_client", lambda nome: _ClienteQueFalha(nome))


def _chamar(cadeia):
    return cadeia.create(model="modelo-full", max_tokens=10, system="s",
                         tools=[], messages=[{"role": "user", "content": "oi"}])


# ── sem prazo: comportamento de sempre ──────────────────────────────────────

def test_sem_prazo_a_cadeia_percorre_tudo(cadeia, monkeypatch):
    """O default protege o agente diário, que roda em janela de 10 min e
    prefere gastar tempo a voltar sem resposta."""
    tentados: list[str] = []
    _fazer_falhar(cadeia, monkeypatch, tentados)

    with pytest.raises(RuntimeError, match="All providers exhausted"):
        _chamar(cadeia)

    assert tentados == ["anthropic", "gemini", "openrouter"]


# ── com prazo ───────────────────────────────────────────────────────────────

def test_prazo_vencido_para_a_cadeia_no_primeiro_fallback(cadeia, monkeypatch):
    """O caso da produção: o primeiro provedor consome o orçamento e não há
    tempo para o segundo. Antes, a cadeia tentava assim mesmo e o processo era
    morto de fora, perdendo até o erro."""
    tentados: list[str] = []
    _fazer_falhar(cadeia, monkeypatch, tentados)
    cadeia.definir_orcamento(time.monotonic() - 1, 55)   # prazo já vencido

    with pytest.raises(RuntimeError, match="Orçamento de tempo esgotado"):
        _chamar(cadeia)

    # O PRIMEIRO foi tentado -- o chamador já tinha orçado essa tentativa.
    # Barrá-la seria recusar trabalho que ele decidiu que cabia.
    assert tentados == ["anthropic"]


def test_prazo_folgado_nao_atrapalha(cadeia, monkeypatch):
    tentados: list[str] = []
    _fazer_falhar(cadeia, monkeypatch, tentados)
    cadeia.definir_orcamento(time.monotonic() + 3600, 55)

    with pytest.raises(RuntimeError, match="All providers exhausted"):
        _chamar(cadeia)

    assert tentados == ["anthropic", "gemini", "openrouter"]


def test_o_prazo_conta_o_CUSTO_da_tentativa_nao_so_o_relogio(cadeia, monkeypatch):
    """Ainda restam 30s, mas uma tentativa custa até 55s: começar seria
    garantir o estouro. O corte tem que ser 'cabe outra?', não 'já passou?'."""
    tentados: list[str] = []
    _fazer_falhar(cadeia, monkeypatch, tentados)
    cadeia.definir_orcamento(time.monotonic() + 30, 55)

    with pytest.raises(RuntimeError, match="Orçamento de tempo esgotado"):
        _chamar(cadeia)

    assert tentados == ["anthropic"]


def test_a_mensagem_diz_quanto_faltava_e_quanto_custa(cadeia, monkeypatch, capsys):
    """Erro de orçamento sem os números manda o operador adivinhar se o
    problema é teto curto ou provedor lento."""
    _fazer_falhar(cadeia, monkeypatch, [])
    cadeia.definir_orcamento(time.monotonic() + 10, 55)

    with pytest.raises(RuntimeError) as e:
        _chamar(cadeia)

    assert "55s" in str(e.value)          # custo de uma tentativa
    assert "gemini" in str(e.value)       # quem NÃO foi tentado
    assert "sem orçamento para tentar gemini" in capsys.readouterr().err


def test_custo_negativo_vira_zero(cadeia):
    """Chamador passando lixo não pode virar prazo que nunca cabe."""
    cadeia.definir_orcamento(time.monotonic() + 10, -5)
    assert cadeia._custo_tentativa_s == 0.0
    assert cadeia._cabe_outra_tentativa() is True


# ── a ligação com o script ──────────────────────────────────────────────────

def test_a_analise_rapida_define_o_orcamento():
    """Ter o mecanismo e não usá-lo deixaria tudo como estava."""
    import pathlib
    from agent import analise_rapida_ia as mod

    fonte = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
    codigo = [l for l in fonte.splitlines() if not l.strip().startswith("#")]
    assert any("definir_orcamento(" in l for l in codigo)
    # e antes do laço que chama create(), senão o prazo chega tarde
    i_def = next(i for i, l in enumerate(codigo) if "definir_orcamento(" in l)
    i_create = next(i for i, l in enumerate(codigo) if "client.create(" in l)
    assert i_def < i_create
