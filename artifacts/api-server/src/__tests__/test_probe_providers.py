"""
probe_providers.py: medir quais modelos do Gemini sustentam o fluxo, em vez de
escolher pelo nome da listagem.

O script existe porque as duas falhas que este repo já teve com o tier "full"
são de naturezas opostas: o `gemini-2.5-pro` virou 404 (falha barata, quebra
na hora) e o `gemini-2.5-flash` respondia normalmente mas abandonava o fluxo
multi-turno no meio (falha cara, gasta a run inteira antes de aparecer). Uma
listagem de modelos não distingue as duas -- só a ida e volta real distingue.

Aqui é testada a lógica que roda sem rede: filtro de candidatos, classificação
do resultado e a saída. A chamada em si é substituída por um cliente falso.

Rodar: pytest artifacts/api-server/src/__tests__/test_probe_providers.py -v
"""

from agent import probe_providers as pg
from agent.provider import NormalizedResponse, TextBlock, ToolUseBlock

RESPOSTA_LONGA = "A NVDA está cotada a US$ 181,42, alta de 1,2% no dia até agora."


class _ClienteFalso:
    """Substitui ProviderClient. `roteiro` é uma lista de respostas por turno."""

    def __init__(self, roteiro):
        self._roteiro = list(roteiro)
        self.modelos_pedidos = []

    def create(self, **kwargs):
        self.modelos_pedidos.append(kwargs["model"])
        r = self._roteiro.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _instalar(monkeypatch, roteiro):
    cliente = _ClienteFalso(roteiro)
    monkeypatch.setattr(pg, "ProviderClient", lambda nome: cliente)
    return cliente


def _pediu_ferramenta(tool_id="call_1"):
    return NormalizedResponse(
        content=[ToolUseBlock(id=tool_id, name="get_stock_data", input={"ticker": "NVDA"})],
        stop_reason="tool_use",
    )


def _texto(t):
    return NormalizedResponse(content=[TextBlock(text=t)], stop_reason="end_turn")


class TestCandidatos:
    def test_descarta_o_que_nunca_serviria_no_tier_full(self):
        todos = [
            "gemini-2.5-flash",
            "gemini-3-pro",
            "text-embedding-004",
            "imagen-3.0-generate",
            "veo-2.0",
            "gemma-3-27b-it",
        ]
        assert pg.candidatos(todos) == ["gemini-2.5-flash", "gemini-3-pro"]

    def test_lista_vazia_nao_quebra(self):
        assert pg.candidatos([]) == []


class TestProbe:
    def test_modelo_que_sustenta_os_dois_turnos_passa(self, monkeypatch):
        _instalar(monkeypatch, [_pediu_ferramenta(), _texto(RESPOSTA_LONGA)])
        r = pg.probe("gemini", "gemini-3-pro")
        assert r["ok"] is True
        assert r["chamou_ferramenta"] is True
        assert r["fechou_com_texto"] is True
        assert r["erro"] is None

    def test_modelo_que_nao_chama_ferramenta_reprova_no_primeiro_turno(self, monkeypatch):
        cliente = _instalar(monkeypatch, [_texto("Não tenho acesso a cotações.")])
        r = pg.probe("gemini", "gemini-x")
        assert r["ok"] is False
        assert r["chamou_ferramenta"] is False
        assert "não chamou a ferramenta" in r["erro"]
        # Não gasta o segundo turno num modelo que já falhou no primeiro.
        assert len(cliente.modelos_pedidos) == 1

    def test_modelo_que_abandona_no_segundo_turno_reprova(self, monkeypatch):
        """O caso do gemini-2.5-flash: responde, chama ferramenta, e some."""
        _instalar(monkeypatch, [_pediu_ferramenta(), _texto("Ok.")])
        r = pg.probe("gemini", "gemini-2.5-flash")
        assert r["ok"] is False
        assert r["chamou_ferramenta"] is True
        assert r["fechou_com_texto"] is False
        assert "curta demais" in r["erro"]

    def test_404_vira_resultado_e_nao_derruba_a_varredura(self, monkeypatch):
        """O caso do gemini-2.5-pro. Um modelo morto não pode abortar o teste
        dos outros -- descobrir o substituto é justamente o objetivo."""
        _instalar(monkeypatch, [RuntimeError("Error code: 404 - model not found")])
        r = pg.probe("gemini", "gemini-2.5-pro")
        assert r["ok"] is False
        assert "404" in r["erro"]

    def test_chamada_vazada_como_texto_e_registrada(self, monkeypatch):
        """provider.py recupera chamada emitida como texto, então o modelo
        'funciona' -- mas é sinal de quem não segue o protocolo, e some do
        relatório se não for marcado."""
        _instalar(monkeypatch, [_pediu_ferramenta("leaked_ab12"), _texto(RESPOSTA_LONGA)])
        r = pg.probe("gemini", "gemini-frouxo")
        assert r["ok"] is True
        assert r["vazou_como_texto"] is True

    def test_marca_ausencia_de_preco(self, monkeypatch):
        """Modelo sem preço reporta custo None, e custo None soma ZERO no teto
        diário -- furo conhecido, precisa aparecer antes da troca."""
        _instalar(monkeypatch, [_pediu_ferramenta(), _texto(RESPOSTA_LONGA)])
        assert pg.probe("gemini", "modelo-inexistente-sem-preco")["tem_preco"] is False

        _instalar(monkeypatch, [_pediu_ferramenta(), _texto(RESPOSTA_LONGA)])
        conhecido = next(iter(pg.MODEL_PRICING))
        assert pg.probe("gemini", conhecido)["tem_preco"] is True

    def test_sempre_mede_o_tempo(self, monkeypatch):
        _instalar(monkeypatch, [RuntimeError("boom")])
        assert pg.probe("gemini", "x")["segundos"] is not None


class TestSaida:
    def _saida(self, capsys, resultados):
        pg.imprimir(resultados)
        return capsys.readouterr().out

    def _resultado(self, **kw):
        base = {
            "provedor": "gemini", "model": "m", "ok": True, "chamou_ferramenta": True,
            "vazou_como_texto": False, "fechou_com_texto": True,
            "tem_preco": True, "segundos": 1.0, "erro": None,
        }
        base.update(kw)
        return base

    def test_sugere_o_aprovado(self, capsys):
        out = self._saida(capsys, [self._resultado(model="gemini-3-pro")])
        assert "gemini-3-pro" in out
        assert "sugestão" in out

    def test_prefere_o_que_nao_vaza_chamada(self, capsys):
        out = self._saida(capsys, [
            self._resultado(model="frouxo", vazou_como_texto=True),
            self._resultado(model="limpo"),
        ])
        assert "PROVIDERS['gemini']['models']['full'] -> limpo" in out

    def test_avisa_do_preco_faltando_na_sugestao(self, capsys):
        out = self._saida(capsys, [self._resultado(model="novo", tem_preco=False)])
        assert "MODEL_PRICING" in out
        assert "ZERO no teto" in out

    def test_sem_aprovado_desaconselha_a_troca(self, capsys):
        out = self._saida(capsys, [
            self._resultado(model="a", ok=False, erro="404"),
        ])
        assert "NENHUM candidato" in out
        assert "Sugestão" not in out

    def test_lembra_que_dois_turnos_nao_e_prova_completa(self, capsys):
        """O gemini-2.5-flash passaria num teste de 2 turnos e ainda assim
        abandonou as 12 rodadas do fluxo diário."""
        out = self._saida(capsys, [self._resultado(model="ok")])
        assert "piso, não a prova completa" in out


class TestVariosProvedores:
    """Sondar só o Gemini não teria mostrado o problema de 03/08: os QUATRO
    provedores de fallback estavam fora ao mesmo tempo -- gemini e openrouter
    com modelo 404, openai sem cota e kimi com a conta suspensa."""

    def test_sonda_todos_os_provedores_openai_compat(self):
        assert set(pg.PROVEDORES_SONDAVEIS) == {"gemini", "openrouter", "openai", "kimi"}

    def test_anthropic_fica_de_fora(self):
        """Não é camada compat, e não é ele que está quebrado -- é o fallback
        DEPOIS dele."""
        assert "anthropic" not in pg.PROVEDORES_SONDAVEIS

    def test_saida_separa_a_sugestao_por_provedor(self, capsys):
        def _r(provedor, model, ok):
            return {"provedor": provedor, "model": model, "ok": ok,
                    "chamou_ferramenta": ok, "vazou_como_texto": False,
                    "fechou_com_texto": ok, "tem_preco": True,
                    "segundos": 1.0, "erro": None if ok else "404"}

        pg.imprimir([
            _r("gemini", "gemini-x", False),
            _r("openrouter", "llama-y", True),
        ])
        out = capsys.readouterr().out

        assert "gemini: NENHUM candidato" in out
        assert "PROVIDERS['openrouter']['models']['full'] -> llama-y" in out

    def test_avisa_quando_a_cadeia_inteira_esta_fora(self, capsys):
        """O caso de 03/08 -- e a consequência precisa estar escrita, senão a
        tabela vazia parece só 'não achei nada'."""
        pg.imprimir([
            {"provedor": p, "model": "m", "ok": False, "chamou_ferramenta": False,
             "vazou_como_texto": False, "fechou_com_texto": False,
             "tem_preco": False, "segundos": 1.0, "erro": "404"}
            for p in pg.PROVEDORES_SONDAVEIS
        ])
        out = capsys.readouterr().out
        assert "cadeia de fallback é decorativa" in out


class TestDiagnosticoDeAmbiente:
    """A primeira execução real do script falhou por interpretador errado, e a
    mensagem final acusou a causa ERRADA: disse "nenhuma chave de provedor no
    ambiente" quando as quatro chaves estavam lá -- o que quebrou foi o import
    do SDK `openai`, porque o `python` do shell não é o mesmo que o servidor
    usa (runner.ts::getPythonBin escolhe .venv/bin/python).

    Mensagem que acusa a causa errada custa mais tempo do que mensagem nenhuma.
    """

    def test_sem_o_sdk_openai_o_erro_aponta_o_interpretador(self, monkeypatch):
        import builtins

        real_import = builtins.__import__

        def _sem_openai(nome, *a, **kw):
            if nome == "openai":
                raise ImportError("No module named 'openai'")
            return real_import(nome, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _sem_openai)

        msg = pg._exigir_openai()
        assert msg is not None
        assert ".venv/bin/python" in msg, "precisa dizer QUAL interpretador usar"

    def test_com_o_sdk_presente_nao_reclama(self, monkeypatch):
        import sys as _sys
        import types

        monkeypatch.setitem(_sys.modules, "openai", types.ModuleType("openai"))
        assert pg._exigir_openai() is None

    def test_falha_de_listagem_nao_e_reportada_como_falta_de_chave(
        self, monkeypatch, capsys
    ):
        """O caso exato de produção: chaves presentes, listagem quebrada."""
        import sys as _sys
        import types

        monkeypatch.setitem(_sys.modules, "openai", types.ModuleType("openai"))
        for nome in pg.PROVEDORES_SONDAVEIS:
            monkeypatch.setenv(pg.PROVIDERS[nome]["api_key_env"], "chave-presente")
        monkeypatch.setattr(pg, "listar_modelos", lambda p, k: (_ for _ in ()).throw(
            RuntimeError("conexão recusada")))
        monkeypatch.setattr(_sys, "argv", ["probe", "--provider", "todos"])

        assert pg.main() == 1

        err = capsys.readouterr().err
        assert "falha ao listar modelos" in err
        assert "As chaves existem" in err
        assert "nenhuma chave de provedor" not in err
