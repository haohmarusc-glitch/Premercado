"""
Testes de get_exit_plan_items/update_exit_plan_item/create_exit_plan_item --
as ferramentas que o agente usa pra reavaliar o Plano de Saída via API
interna (mesmo padrão de save_observation/create_alert: chama de volta
localhost via requests, autenticado com OPERATOR_API_KEY).

Rodar (da raiz do repo): pytest artifacts/api-server/src/__tests__/test_exit_plan_tools.py -v
"""
from unittest import mock

from agent import tools


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
        with mock.patch.object(tools.requests, "get", return_value=_FakeResponse(payload)) as m:
            result = tools.get_exit_plan_items()
        assert result == payload
        args, kwargs = m.call_args
        assert args[0].endswith("/api/exit-plan")

    def test_fails_open_on_request_error(self, monkeypatch):
        with mock.patch.object(tools.requests, "get", side_effect=OSError("timeout")):
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

    def test_fails_open_on_request_error(self, monkeypatch):
        with mock.patch.object(tools.requests, "patch", side_effect=OSError("timeout")):
            result = tools.update_exit_plan_item(5, action="Vender")
        assert result["updated"] is False
        assert result["id"] == 5


class TestCreateExitPlanItem:
    def test_creates_item_with_all_fields(self, monkeypatch):
        with mock.patch.object(tools.requests, "post", return_value=_FakeResponse({"id": 9, "ticker": "AVGO"})) as m:
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
        with mock.patch.object(tools.requests, "post", side_effect=OSError("timeout")):
            result = tools.create_exit_plan_item(
                ticker="AVGO", phase=1, phase_label="Fase 1", target_date="2026-08-01",
                action="Vender", rationale="teste",
            )
        assert result["created"] is False
