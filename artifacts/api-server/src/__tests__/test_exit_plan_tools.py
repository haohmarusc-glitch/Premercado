"""
Testes de get_exit_plan_items/update_exit_plan_item/create_exit_plan_item --
as ferramentas que o agente usa pra reavaliar o Plano de Saída via API
interna (mesmo padrão de save_observation/create_alert: chama de volta
localhost via requests, autenticado com OPERATOR_API_KEY).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_exit_plan_tools.py -v
"""
from unittest import mock

import pytest

from agent import tools


@pytest.fixture(autouse=True)
def sem_data_alvo(monkeypatch):
    """Nenhuma data-alvo, por padrão, em TODO teste deste arquivo.

    `create_exit_plan_item` e `update_exit_plan_item` consultam a data-alvo
    da carteira antes de escrever (guarda de prazo). Sem este stub cada uma
    dessas chamadas vira três tentativas de conexão recusada contra
    localhost:5000 -- 6 segundos por teste, 60 no arquivo.

    O padrão é "não configurada" porque é o caso em que a guarda não opina:
    quem testa a guarda declara a data que quer, e o resto do arquivo não
    precisa saber que ela existe.
    """
    monkeypatch.setattr(tools, "_data_alvo_da_carteira", lambda: None)


class _FakeResponse:
    def __init__(self, payload=None, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class TestGetExitPlanItems:
    def test_returns_items_from_internal_api(self, monkeypatch):
        payload = [{"id": 1, "ticker": "SMCI", "status": "pending", "targetDate": "2026-08-03"}]
        with mock.patch.object(tools.SESSION, "get", return_value=_FakeResponse(payload)) as m:
            result = tools.get_exit_plan_items()
        assert result == payload
        args, kwargs = m.call_args
        assert args[0].endswith("/api/exit-plan")

    def test_fails_open_on_request_error(self, monkeypatch):
        with mock.patch.object(tools.SESSION, "get", side_effect=OSError("timeout")):
            result = tools.get_exit_plan_items()
        assert result[0]["error"]


class TestUpdateExitPlanItem:
    def test_sends_only_provided_fields(self, monkeypatch):
        with mock.patch.object(tools.requests, "patch", return_value=_FakeResponse({"id": 5})) as m:
            result = tools.update_exit_plan_item(5, target_date="2026-08-10", rationale="Novo motivo")
        assert result["updated"] is True
        _, kwargs = m.call_args
        assert kwargs["json"] == {"targetDate": "2026-08-10", "rationale": "Novo motivo"}

    def test_omits_none_fields_entirely(self, monkeypatch):
        with mock.patch.object(tools.requests, "patch", return_value=_FakeResponse({"id": 5})) as m:
            tools.update_exit_plan_item(5, action="Vender 50%")
        _, kwargs = m.call_args
        assert kwargs["json"] == {"action": "Vender 50%"}

    def test_marca_item_como_skipped(self, monkeypatch):
        """status precisa chegar na API interna -- sem isso o agente não tem
        como tirar da lista "pending" um item de ticker que já saiu da
        carteira (ver build_exit_plan_prompt, passo de limpeza)."""
        with mock.patch.object(tools.requests, "patch", return_value=_FakeResponse({"id": 5})) as m:
            tools.update_exit_plan_item(5, status="skipped", rationale="Posição não está mais na carteira")
        _, kwargs = m.call_args
        assert kwargs["json"] == {"status": "skipped", "rationale": "Posição não está mais na carteira"}

    def test_fails_open_on_request_error(self, monkeypatch):
        with mock.patch.object(tools.requests, "patch", side_effect=OSError("timeout")):
            result = tools.update_exit_plan_item(5, action="Vender")
        assert result["updated"] is False
        assert result["id"] == 5


def _sem_plano():
    """Plano vazio -- `create_exit_plan_item` lê o plano antes de criar (guarda
    contra duplicata), então todo teste de criação precisa declarar o que ela
    vai encontrar. Sem isto a chamada tentaria HTTP de verdade."""
    return mock.patch.object(tools, "get_exit_plan_items", return_value=[])


class TestCreateExitPlanItem:
    def test_creates_item_with_all_fields(self, monkeypatch):
        with _sem_plano(), mock.patch.object(tools.requests, "post", return_value=_FakeResponse({"id": 9, "ticker": "AVGO"})) as m:
            result = tools.create_exit_plan_item(
                ticker="avgo", phase=2, phase_label="Fase 2", target_date="2026-08-15",
                action="Vender na força", rationale="Medo de capex de IA",
            )
        assert result["created"] is True
        _, kwargs = m.call_args
        assert kwargs["json"]["ticker"] == "AVGO"

    def test_rejects_invalid_ticker(self):
        result = tools.create_exit_plan_item(
            ticker="", phase=1, phase_label="Fase 1", target_date="2026-08-01",
            action="Vender", rationale="teste",
        )
        assert result["created"] is False
        assert "error" in result

    def test_fails_open_on_request_error(self, monkeypatch):
        with _sem_plano(), mock.patch.object(tools.requests, "post", side_effect=OSError("timeout")):
            result = tools.create_exit_plan_item(
                ticker="AVGO", phase=1, phase_label="Fase 1", target_date="2026-08-01",
                action="Vender", rationale="teste",
            )
        assert result["created"] is False


class TestCreateNaoDuplica:
    """A guarda contra dois planos pro mesmo papel.

    Incidente (21/09/2026): depois de "Reavaliar plano", MRVL, ADI e AVGO
    ficaram com DOIS itens cada -- o antigo, pendente e vencido, e um novo. O
    prompt já mandava atualizar em vez de criar; um prazo vencido lê-se como
    plano encerrado, e o modelo escreveu o plano novo ao lado do velho. A
    tela passou a mostrar duas ordens de venda contraditórias por papel.
    """

    def _plano(self, **campos):
        base = {"id": 7, "ticker": "MRVL", "status": "pending", "targetDate": "2026-09-01"}
        base.update(campos)
        return [base]

    def test_recusa_segundo_item_e_aponta_o_existente(self):
        with mock.patch.object(tools, "get_exit_plan_items", return_value=self._plano()), \
                mock.patch.object(tools.requests, "post") as post:
            r = tools.create_exit_plan_item(
                ticker="MRVL", phase=1, phase_label="Fase 1", target_date="2026-10-01",
                action="Vender 50%", rationale="subiu 17% desde o plano",
            )
        assert r["created"] is False
        assert r["existing_item_id"] == 7
        # O id tem que vir no texto: é por ele que o modelo chama o update.
        assert "7" in r["error"]
        assert "update_exit_plan_item" in r["error"]
        post.assert_not_called()

    def test_prazo_vencido_nao_libera_a_criacao(self):
        # O caso REAL: o item velho está pendente com prazo no passado. Se a
        # guarda olhasse só pra data, ela abriria exatamente o buraco pelo
        # qual MRVL, ADI e AVGO passaram.
        with mock.patch.object(tools, "get_exit_plan_items",
                               return_value=self._plano(targetDate="2020-01-01")), \
                mock.patch.object(tools.requests, "post") as post:
            r = tools.create_exit_plan_item(
                ticker="MRVL", phase=1, phase_label="Fase 1", target_date="2026-10-01",
                action="Vender 50%", rationale="prazo passou",
            )
        assert r["created"] is False
        post.assert_not_called()

    def test_ticker_em_minuscula_nao_escapa(self):
        with mock.patch.object(tools, "get_exit_plan_items", return_value=self._plano()), \
                mock.patch.object(tools.requests, "post") as post:
            r = tools.create_exit_plan_item(
                ticker="mrvl", phase=1, phase_label="Fase 1", target_date="2026-10-01",
                action="Vender", rationale="teste",
            )
        assert r["created"] is False
        post.assert_not_called()

    def test_item_fechado_nao_bloqueia(self):
        # "skipped"/"sold" são planos encerrados. Bloquear por causa deles
        # impediria o papel de ganhar um plano novo -- que é justamente o que
        # a ferramenta existe pra fazer.
        for encerrado in ("skipped", "sold"):
            with mock.patch.object(tools, "get_exit_plan_items",
                                   return_value=self._plano(status=encerrado)), \
                    mock.patch.object(tools.requests, "post",
                                      return_value=_FakeResponse({"id": 9})):
                r = tools.create_exit_plan_item(
                    ticker="MRVL", phase=1, phase_label="Fase 1", target_date="2026-10-01",
                    action="Vender", rationale="tese nova",
                )
            assert r["created"] is True, encerrado

    def test_outro_ticker_nao_bloqueia(self):
        with mock.patch.object(tools, "get_exit_plan_items", return_value=self._plano()), \
                mock.patch.object(tools.requests, "post", return_value=_FakeResponse({"id": 9})):
            r = tools.create_exit_plan_item(
                ticker="ADI", phase=1, phase_label="Fase 1", target_date="2026-10-01",
                action="Vender", rationale="teste",
            )
        assert r["created"] is True

    def test_leitura_falhando_nao_impede_o_plano_de_nascer(self):
        # Fail open de propósito: entre uma duplicata (visível e corrigível
        # num clique) e um plano que não nasceu porque a API piscou, a
        # duplicata é o erro mais barato.
        falha = [{"leitura_falhou": True, "error": "OSError: timeout"}]
        with mock.patch.object(tools, "get_exit_plan_items", return_value=falha), \
                mock.patch.object(tools.requests, "post", return_value=_FakeResponse({"id": 9})):
            r = tools.create_exit_plan_item(
                ticker="MRVL", phase=1, phase_label="Fase 1", target_date="2026-10-01",
                action="Vender", rationale="teste",
            )
        assert r["created"] is True


class TestPromptDaReavaliacao:
    """As regras que a reavaliação de 21/09/2026 mostrou que faltavam.

    Mesmo padrão de `_REGRAS_QUE_NAO_PODEM_SUMIR` em test_analise_rapida_ia:
    o teste não julga a redação, só garante que a regra não some numa
    consolidação futura do prompt. Regra apagada volta como o incidente que a
    produziu.
    """

    def _prompt(self):
        from agent.llm_runtime import build_exit_plan_prompt
        # `build_exit_plan_prompt` lê a data-alvo pela API interna; sem o
        # stub cada chamada vira três tentativas de conexão recusada.
        with mock.patch.object(tools, "_data_alvo_da_carteira", return_value="2026-10-05"):
            return build_exit_plan_prompt()

    def test_prazo_vencido_e_item_a_corrigir(self):
        # MRVL/ADI/AVGO ganharam um item novo ao lado do vencido.
        assert "PRAZO JÁ VENCIDO" in self._prompt()

    def test_proibe_o_item_novo_com_o_vencido_de_pe(self):
        p = self._prompt()
        assert "NUNCA crie um item novo pro mesmo ticker" in p

    def test_manda_escrever_em_portugues(self):
        # A tela recebeu "hold", "tight stop-loss", "breakout", "target" e
        # "keep 50%" -- o prompt não pedia português em lugar nenhum.
        p = self._prompt()
        assert "Em PORTUGUÊS" in p
        for ingles in ("hold", "stop-loss", "breakout", "target"):
            assert ingles in p, f"o exemplo '{ingles}' saiu da lista"

    def test_manda_datar_o_preco_citado(self):
        # A tela mostra o preço ao vivo ao lado do texto; sem data, os dois
        # se leem como contradição (MRVL 255,49 na tela x 254,81 no texto).
        assert "DATA E HORA" in self._prompt()

    def test_manda_limpar_duplicata_existente(self):
        # A guarda em create_exit_plan_item impede duplicata NOVA; as três que
        # já estão no banco (MRVL, ADI, AVGO) só saem se a reavaliação as
        # fechar. "skipped", nunca apagar: o histórico é o que explica por que
        # o plano mudou.
        p = self._prompt()
        assert "LIMPEZA DE DUPLICATA" in p
        assert "MAIS DE UM item" in p
        assert 'status="skipped"' in p


class TestPrazoNaoPassaDaDataAlvo:
    """O plano não pode vencer depois de o dinheiro sair.

    Incidente (21/09/2026): a reavaliação escreveu para a AVGO prazo
    09/12/2026 e ação "aguardar catalisador pré-earnings de dezembro", para um
    usuário cuja data-alvo é 05/10. O prompt não mencionava horizonte nenhum e
    `get_scenario_status` -- a única ferramenta que conhece a data-alvo -- nem
    estava em EXIT_PLAN_TOOLS. O modelo não errou a conta: ele nunca soube que
    havia prazo.
    """

    def _alvo(self, data="2026-10-05", configured=True):
        return mock.patch.object(
            tools, "_data_alvo_da_carteira",
            return_value=data if configured else None,
        )

    def test_recusa_criacao_depois_do_alvo(self):
        with self._alvo(), _sem_plano(), mock.patch.object(tools.requests, "post") as post:
            r = tools.create_exit_plan_item(
                ticker="AVGO", phase=1, phase_label="Fase 1", target_date="2026-12-09",
                action="Aguardar catalisador pré-earnings", rationale="dezembro",
            )
        assert r["created"] is False
        assert "2026-10-05" in r["error"]
        post.assert_not_called()

    def test_recusa_update_depois_do_alvo(self):
        with self._alvo(), mock.patch.object(tools.requests, "patch") as patch_:
            r = tools.update_exit_plan_item(3, target_date="2026-12-09")
        assert r["updated"] is False
        assert "2026-10-05" in r["error"]
        patch_.assert_not_called()

    def test_o_proprio_dia_do_alvo_passa(self):
        # Fronteira: sair NO dia do resgate é plano válido, não estouro.
        with self._alvo(), mock.patch.object(tools.requests, "patch",
                                             return_value=_FakeResponse({"id": 3})):
            r = tools.update_exit_plan_item(3, target_date="2026-10-05")
        assert r["updated"] is True

    def test_prazo_anterior_passa(self):
        with self._alvo(), _sem_plano(), mock.patch.object(
                tools.requests, "post", return_value=_FakeResponse({"id": 9})):
            r = tools.create_exit_plan_item(
                ticker="AVGO", phase=1, phase_label="Fase 1", target_date="2026-10-01",
                action="Vender", rationale="dentro do prazo",
            )
        assert r["created"] is True

    def test_sem_data_alvo_configurada_nao_opina(self):
        # Sem prazo escolhido pelo usuário, a rota devolve um default --
        # tratá-lo como compromisso seria inventar um prazo que ninguém deu.
        with self._alvo(configured=False), _sem_plano(), mock.patch.object(
                tools.requests, "post", return_value=_FakeResponse({"id": 9})):
            r = tools.create_exit_plan_item(
                ticker="AVGO", phase=1, phase_label="Fase 1", target_date="2027-12-09",
                action="Vender", rationale="sem prazo configurado",
            )
        assert r["created"] is True

    def test_update_sem_mexer_na_data_nao_e_barrado(self):
        # Mudar só a ação/motivo de um item cujo prazo já estava fora não pode
        # travar: a guarda vale pra data que ESTA chamada escreve.
        with self._alvo(), mock.patch.object(tools.requests, "patch",
                                             return_value=_FakeResponse({"id": 3})):
            r = tools.update_exit_plan_item(3, action="Vender 50%")
        assert r["updated"] is True

    def test_data_alvo_ilegivel_nao_vira_recusa(self):
        with mock.patch.object(tools.SESSION, "get", side_effect=OSError("timeout")):
            assert tools._data_alvo_da_carteira() is None
        with mock.patch.object(tools.SESSION, "get",
                               return_value=_FakeResponse({"configured": False, "dataAlvo": "2026-12-31"})):
            assert tools._data_alvo_da_carteira() is None


class TestEstruturaDoItemNoPrompt:
    def _prompt(self):
        from agent.llm_runtime import build_exit_plan_prompt
        with mock.patch.object(tools, "_data_alvo_da_carteira", return_value="2026-10-05"):
            return build_exit_plan_prompt()

    def test_o_prazo_da_carteira_entra_no_system(self):
        p = self._prompt()
        assert "PRAZO DA CARTEIRA: 2026-10-05" in p
        assert "NENHUM item pode ter data-alvo depois dela" in p

    def test_sem_prazo_configurado_o_prompt_diz_isso(self):
        from agent.llm_runtime import build_exit_plan_prompt
        with mock.patch.object(tools, "_data_alvo_da_carteira", return_value=None):
            p = build_exit_plan_prompt()
        assert "não configurado" in p

    def test_exige_stop_alvo_e_regra_temporal(self):
        # O plano da AVGO trazia só "rompimento acima de US$ 370+": gatilho de
        # entrada, sem alvo de realização e sem o que fazer se nada acontecer.
        p = self._prompt()
        for regra in ("STOP:", "ALVO:", "REGRA TEMPORAL:"):
            assert regra in p, regra

    def test_exige_stop_abaixo_do_gatilho_parcial(self):
        # "Stop em US$ 350" + "se tocar US$ 340, vender 50%" é impossível: a
        # US$ 350 a posição já saiu inteira.
        p = self._prompt()
        assert "COERÊNCIA ENTRE OS NÍVEIS" in p
        assert "ABAIXO de todo gatilho de venda parcial" in p

    def test_preco_citado_vem_com_hora(self):
        assert "DATA E HORA" in self._prompt()
