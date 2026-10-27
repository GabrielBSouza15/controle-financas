from datetime import date
import re
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.services.adoption import adopt
from test_settlements import client, entry, ORIGIN
from test_history_integration import load

def post(c,path,data):
    r=c.post("/api/personal/"+path,json=data)
    assert r.status_code==200,r.text
    return r.json()

def test_reinvestment_is_independent_replay_and_conflict(client):
    assert client.get("/api/personal/reinvestment").json()==dict(amount_cents=0,version=0)
    data=dict(request_key="reinvest",version=0,mode="add",amount_cents=100)
    assert post(client,"reinvestment",data)["amount_cents"]==100
    assert post(client,"reinvestment",data)["amount_cents"]==100
    assert client.post("/api/personal/reinvestment",json=data|dict(amount_cents=200)).status_code==409
    assert client.post("/api/personal/reinvestment",json=data|dict(request_key="stale")).status_code==409
    assert post(client,"reinvestment",dict(request_key="subtract-all",version=1,mode="subtract",amount_cents=100))["amount_cents"]==0
    assert client.get("/api/settlements").json()["cash_out_cents"]==0

def bill(c,month,total,key="whole"):
    return dict(request_key=key,card_id=c.card,month=month,expected_remaining_cents=total,occurred_on="2026-10-05",account_id=c.account)

def test_whole_month_covers_both_people_replay_and_no_double_expense(client):
    entry(client,total_cents=1000)
    entry(client,total_cents=2000,request_key="parent",responsible_id=2)
    data=bill(client,"2026-10",3000)
    assert post(client,"bill",data)["paid_cents"]==3000
    assert post(client,"bill",data)["paid_cents"]==3000
    assert client.post("/api/personal/bill",json=data|dict(request_key="another")).status_code==409
    inv=client.get("/api/invoices/1").json()
    assert inv["issuer_remaining_cents"]==0 and inv["parents_uncovered_cents"]==2000
    assert inv["total_cents"]==3000
    assert client.get("/api/settlements").json()["cash_out_cents"]==3000

def test_mixed_bill_rolls_back_if_account_invalid_and_preserves_paid_undated(client):
    load(client)
    entry(client,occurred_on="2025-01-03",total_cents=2500)
    data=bill(client,"2025-01",9500)
    assert client.post("/api/personal/bill",json=data|dict(account_id=999)).status_code==422
    assert client.get("/api/invoices/1").json()["issuer_paid_cents"]==0
    assert post(client,"bill",data)["paid_cents"]==9500
    flow=client.get("/api/personal/flow?date_from=2025-01-01&date_to=2025-01-31").json()
    assert any(i["payment_status"]=="Pago sem data (planilha)" for i in flow["items"])
    assert all(i["pending_cents"]==0 for i in flow["items"] if i["kind"]=="expense")

def test_immediate_atomic_idempotent_and_future_rejected(client):
    e=entry(client,card_id=None,payment_method="pix_debit",immediate_account_id=client.account,total_cents=1000)
    assert e["obligations"][0]["payment_cents"]==1000
    assert entry(client,card_id=None,payment_method="pix_debit",immediate_account_id=client.account,total_cents=1000)["id"]==e["id"]
    assert client.get("/api/settlements").json()["cash_out_cents"]==1000
    bad=dict(request_key="future",description="Futuro fictício",occurred_on="2099-01-01",total_cents=1000,installment_count=1,responsible_id=1,payment_method="pix",kind="expense",immediate_account_id=client.account)
    assert client.post("/api/entries",json=bad).status_code==422
    assert client.get("/api/entries").json()["total"]==1

def test_flow_chronology_pagination_and_pending(client):
    entry(client,request_key="late",occurred_on="2026-10-20",payment_method="pix",card_id=None,total_cents=200)
    entry(client,request_key="early",occurred_on="2026-10-01",payment_method="pix",card_id=None,total_cents=100)
    result=client.get("/api/personal/flow?date_from=2026-10-01&date_to=2026-10-31&limit=1").json()
    assert result["total"]==2 and result["items"][0]["amount_cents"]==100
    assert result["items"][0]["payment_status"]=="Pendente"


def test_subtract_is_exact_idempotent_and_cannot_overdraw(client):
    post(client,"reinvestment",dict(request_key="fictitious-opening",version=0,mode="add",amount_cents=20000))
    data=dict(request_key="subtract",version=1,mode="subtract",amount_cents=10001)
    assert post(client,"reinvestment",data)==dict(amount_cents=9999,version=2)
    assert post(client,"reinvestment",data)==dict(amount_cents=9999,version=2)
    too_much=data|dict(request_key="overdraw",version=2,amount_cents=10000)
    assert client.post("/api/personal/reinvestment",json=too_much).status_code==422
    assert client.get("/api/personal/reinvestment").json()==dict(amount_cents=9999,version=2)
    assert client.post("/api/personal/reinvestment",json=data|dict(request_key="old-mode",version=2,mode="replace")).status_code==422
    assert client.get("/api/settlements").json()["cash_out_cents"]==0


def test_legacy_replace_receipt_replays_without_reapplying(client):
    from app.persistence.database import transaction
    from app.services.personal import save_operation
    data=dict(request_key="legacy-replace",version=0,mode="replace",amount_cents=12345)
    result=dict(amount_cents=12345,version=1)
    with transaction(client.app.state.engine) as session:
        save_operation(session,data,"reinvestment",result)
    assert post(client,"reinvestment",data)==result
    assert client.get("/api/personal/reinvestment").json()==result


@pytest.mark.parametrize("adopted", [False, True])
def test_personal_commands_after_startup_with_history(client, tmp_path, adopted):
    load(client)
    path=client.app.state.database_path
    if adopted:
        path=tmp_path/"active.db"
        adopt(client.app.state.database_path,path,tmp_path/"restored.db",tmp_path/"copies",
              expected_source_sha256="a"*64,accept_known_limits=True,defer_independent_backup=True)
    with TestClient(create_app(path),base_url=ORIGIN) as c:
        assert c.app.state.history_candidate and c.app.state.adopted is adopted
        token=re.search(r'name="csrf-token" content="([^"]+)"',c.get("/").text).group(1)
        c.headers.update({"Origin":ORIGIN,"X-CSRF-Token":token})
        before=c.get("/api/dashboard?month=2025-01&months=3").json()
        history=c.get("/api/history/summary").json()
        data=dict(request_key="history-reinvest",version=0,mode="add",amount_cents=20000)
        assert post(c,"reinvestment",data)==dict(amount_cents=20000,version=1)
        assert post(c,"reinvestment",data)==dict(amount_cents=20000,version=1)
        assert post(c,"reinvestment",dict(request_key="history-subtract",version=1,mode="subtract",amount_cents=12345))==dict(amount_cents=7655,version=2)
        assert c.get("/api/dashboard?month=2025-01&months=3").json()==before
        assert c.get("/api/history/summary").json()==history

        data=bill(client,"2025-01",7000,key="history-whole")
        assert post(c,"bill",data)["paid_cents"]==7000
        assert post(c,"bill",data)["paid_cents"]==7000
        assert c.get("/api/history/summary").json()["pending_cents"]==0
        assert c.get("/api/settlements").json()["cash_out_cents"]==7000

        for endpoint in ("reinvestment","bill"):
            assert c.post("/api/personal/"+endpoint,json={},headers={"X-CSRF-Token":""}).status_code==403
            assert c.post("/api/personal/"+endpoint,json={},headers={"Origin":"https://example.com"}).status_code==403
        for endpoint in ("/api/setup","/api/personal/unknown"):
            response=c.post(endpoint,json={})
            assert response.status_code==409
            assert response.json()["error"]["code"]=="history_candidate_restricted"
