"""Observação opcional preserva auditoria, idempotência e validações financeiras."""
import pytest
from pydantic import TypeAdapter, ValidationError
from app.api.schemas import Reason, UNINFORMED_REASON
from test_settlements import client, entry, event, allocations


@pytest.mark.parametrize("fields", [{}, {"reason": ""}, {"reason": "  "}, {"reason": None}])
def test_payment_without_observation_replays_and_reverses(client, fields):
    original = entry(client)
    data = event(client, allocations=allocations(original))
    data.pop("reason")
    data.update(fields)
    response = client.post("/api/settlements", json=data)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["reason"] == UNINFORMED_REASON
    assert client.post("/api/settlements", json=data).json()["id"] == saved["id"]
    assert client.get("/api/settlements").json()["cash_out_cents"] == 150000
    reversed_event = client.post(f"/api/settlements/{saved['id']}/reverse", json={"request_key": "reverse", **fields})
    assert reversed_event.status_code == 200, reversed_event.text
    assert client.get("/api/settlements").json()["cash_out_cents"] == 0


@pytest.mark.parametrize("fields", [{}, {"reason": ""}])
def test_cancellation_without_reason_preserves_previous_version(client, fields):
    original = entry(client)
    data = {"request_key": "cancel", **fields}
    path = f"/api/entries/{original['id']}/cancel"
    response = client.post(path, json=data)
    assert response.status_code == 200, response.text
    assert client.post(path, json=data).status_code == 200
    detail = client.get(f"/api/entries/{original['id']}").json()
    assert detail["status"] == "cancelled"
    assert detail["obligations"] == original["obligations"]
    assert client.get("/api/entries").json()["total"] == 0


@pytest.mark.parametrize("value", [True, 123, [], "x" * 1001])
def test_optional_reason_does_not_accept_invalid_types_or_excess_length(value):
    with pytest.raises(ValidationError):
        TypeAdapter(Reason).validate_python(value)
