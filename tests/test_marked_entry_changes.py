"""Correções auditadas sem inventar eventos de caixa ou duplicar orçamento."""
import pytest
from sqlalchemy import select, func
from test_web import client, ready, payload
from test_payment_marks import draft
from app.persistence.database import transaction
from app.persistence.models import EntryPaymentMark, Entry, FinancialEvent, EntryChange


def edit_body(c, **changes):
    return draft(c, request_key="edit-paid", paid_without_account=False,
                 description="Valor corrigido", total_cents=12002, reason="Correção") | changes


def test_marked_revision_chain_reconciles_budget_flow_and_keeps_evidence(ready):
    c = ready
    source = c.post("/api/entries", json=draft(c)).json()
    assert c.get("/api/entries").json()["items"][0]["can_modify"]
    body = edit_body(c)
    response = c.post(f'/api/entries/{source["id"]}/edit', json=body)
    assert response.status_code == 200, response.text
    target = response.json()
    assert target["paid_without_account"] and target["can_modify"]
    assert [p["payment_cents"] for p in target["obligations"]] == [4001,4001,4000]
    assert c.post(f'/api/entries/{source["id"]}/edit', json=body).json() == target
    assert c.post(f'/api/entries/{source["id"]}/edit', json=body | {"total_cents":12003}).status_code == 409
    assert c.post(f'/api/entries/{source["id"]}/cancel', json={"request_key":"stale"}).status_code == 409
    old = c.get(f'/api/entries/{source["id"]}').json()
    assert old["status"] == "superseded" and old["paid_without_account"] and not old["can_modify"]
    flow = c.get('/api/personal/flow?date_from=2026-10-01&date_to=2026-12-31').json()
    assert len(flow["items"]) == 3
    assert sum(p["amount_cents"] for p in flow["items"]) == 12002
    assert all(p["pending_cents"] == 0 for p in flow["items"])
    budget = c.get('/api/dashboard?month=2026-10&months=3').json()["budget"]
    assert budget["total_self_expense_cents"] == 4001 and budget["cash_out_cents"] == 0
    removal = {"request_key":"remove-corrected"}
    path = f'/api/entries/{target["id"]}/cancel'
    removed = c.post(path, json=removal)
    assert removed.status_code == 200 and removed.json()["status"] == "cancelled"
    assert c.post(path, json=removal).json() == removed.json()
    assert c.get('/api/entries').json()["total"] == 0
    assert c.get('/api/personal/flow?date_from=2026-10-01&date_to=2026-12-31').json()["items"] == []
    assert c.get('/api/dashboard?month=2026-10&months=3').json()["budget"]["total_self_expense_cents"] == 0
    with transaction(c.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(EntryPaymentMark)) == 2
        assert s.scalar(select(func.count()).select_from(EntryChange)) == 2
        assert s.scalar(select(func.count()).select_from(FinancialEvent)) == 0


@pytest.mark.parametrize('changes', [
    {"kind":"income"},
    {"responsible_id":2,"parents_repayment":"pending"},
    {"payment_method":"credit","card_id":1},
])
def test_change_nature_preserves_old_mark_without_paying_new_commitment(ready, changes):
    old = ready.post('/api/entries', json=draft(ready)).json()
    r = ready.post(f'/api/entries/{old["id"]}/edit', json=edit_body(ready, **changes))
    assert r.status_code == 200, r.text
    assert not r.json()["paid_without_account"]
    assert all(p["payment_cents"] == 0 for p in r.json()["obligations"])
    assert ready.get(f'/api/entries/{old["id"]}').json()["paid_without_account"]


def test_future_paid_correction_rolls_back_but_key_can_retry_same_valid_attempt(ready):
    old = ready.post('/api/entries', json=draft(ready)).json()
    path = f'/api/entries/{old["id"]}/edit'
    r = ready.post(path, json=edit_body(ready, occurred_on="2099-01-01"))
    assert r.status_code == 422, r.text
    assert ready.get(f'/api/entries/{old["id"]}').json() == old
    with transaction(ready.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(Entry)) == 1
        assert s.scalar(select(func.count()).select_from(EntryPaymentMark)) == 1
    assert ready.post(path, json=edit_body(ready)).status_code == 200


@pytest.mark.parametrize('kind', ['income','expense'])
def test_unpaid_nonrecurring_entry_can_edit_and_remove_without_implicit_payment(ready,kind):
    old = ready.post('/api/entries', json=draft(ready,kind=kind,paid_without_account=False)).json()
    r = ready.post(f'/api/entries/{old["id"]}/edit',json=edit_body(ready,kind=kind))
    assert r.status_code == 200, r.text
    new = r.json()
    assert not new['paid_without_account']
    assert all(p['payment_cents']==0 for p in new['obligations'])
    assert ready.post(f'/api/entries/{new["id"]}/cancel',json={'request_key':'remove'}).status_code==200


def test_real_account_payment_still_blocks_edit_and_removal(ready):
    account=ready.post('/api/accounts',json={'name':'Conta fictícia'}).json()['id']
    old=ready.post('/api/entries',json=draft(ready,paid_without_account=False,immediate_account_id=account)).json()
    assert not old['can_modify'] and old['has_settlement_links']
    assert ready.post(f'/api/entries/{old["id"]}/edit',json=edit_body(ready)).status_code==409
    assert ready.post(f'/api/entries/{old["id"]}/cancel',json={'request_key':'remove'}).status_code==409


def test_marked_changes_allowed_after_startup_with_history(ready):
    import re
    from fastapi.testclient import TestClient
    from app.main import create_app
    from test_web import ORIGIN
    from test_history_integration import load
    load(ready)
    with TestClient(create_app(ready.app.state.database_path), base_url=ORIGIN) as c:
        token=re.search(r'name="csrf-token" content="([^"]+)"',c.get('/').text).group(1)
        c.headers.update({'Origin':ORIGIN,'X-CSRF-Token':token})
        history=c.get('/api/history/summary').json()
        old=c.post('/api/entries',json=draft(ready)).json()
        r=c.post(f'/api/entries/{old["id"]}/edit',json=edit_body(ready))
        assert r.status_code==200,r.text
        new=r.json()
        assert new['paid_without_account']
        assert c.post(f'/api/entries/{new["id"]}/cancel',json={'request_key':'remove'}).status_code==200
        assert c.get('/api/history/summary').json()==history


def test_downgrade_refuses_to_hide_marked_correction_history(ready):
    from alembic import command
    from app.persistence.migrations import migration_config, current_revision, head_revision
    old=ready.post('/api/entries',json=draft(ready)).json()
    assert ready.post(f'/api/entries/{old["id"]}/cancel',json={'request_key':'remove'}).status_code==200
    with pytest.raises(RuntimeError,match='Preservar correções'):
        with ready.app.state.engine.begin() as connection:
            cfg=migration_config()
            cfg.attributes['connection']=connection
            command.downgrade(cfg,'0015_entry_payment_marks')
    assert current_revision(ready.app.state.database_path)==head_revision()
