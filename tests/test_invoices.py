"""Faturas refletem somente parcelas ativas, sem inferir caixa ou liquidação."""
from datetime import date
import hashlib
import re
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from app.main import create_app
from app.persistence.database import transaction
from app.persistence.entries import EntryDraft, record_entry
from app.persistence.migrations import upgrade_database
from app.persistence.models import Base, Card, Invoice
from app.persistence.types import MAX_INTEGER

ORIGIN="http://127.0.0.1:8000"

@pytest.fixture
def client(tmp_path):
    path=tmp_path/"invoices.db";upgrade_database(path)
    with TestClient(create_app(path),base_url=ORIGIN) as c:
        token=re.search(r'name="csrf-token" content="([^"]+)"',c.get("/").text).group(1)
        c.headers.update({"Origin":ORIGIN,"X-CSRF-Token":token})
        c.catalogs=c.post("/api/setup",json={"effective_from":"2020-01-01"}).json()
        c.database_path=path
        yield c

def entry(c,**changes):
    data=dict(request_key="self",description="Compra fictícia",occurred_on="2026-10-01",total_cents=150000,installment_count=1,
        responsible_id=c.catalogs["responsibles"][0]["id"],kind="expense",payment_method="credit",card_id=next(x["id"] for x in c.catalogs["cards"] if x["name"]=="Nubank"))
    r=c.post("/api/entries",json=data|changes);assert r.status_code==200,r.text;return r.json()

def test_shared_invoice_complete_and_responsible_filter_do_not_change_total(client):
    own=entry(client)
    parents=client.catalogs["responsibles"][1]["id"]
    entry(client,request_key="parents-1",total_cents=100000,responsible_id=parents)
    entry(client,request_key="parents-2",total_cents=50000,responsible_id=parents)
    listing=client.get("/api/invoices").json();assert listing["total"]==1
    invoice=listing["items"][0]
    assert (invoice["total_cents"],invoice["self_cents"],invoice["parents_cents"])==(300000,150000,150000)
    assert invoice["active_obligation_count"]==3 and not invoice["empty"]
    filtered=client.get("/api/invoices",params={"responsible_id":parents}).json()
    assert filtered["items"]==listing["items"]
    details=client.get(f'/api/invoices/{invoice["id"]}').json()
    assert details["total_cents"]==sum(p["amount_cents"] for p in details["items"])
    assert {p["responsible_name"] for p in details["items"]}=={"Eu","Pai/Mãe"}
    assert own["id"] in [p["entry_id"] for p in details["items"]]
    assert "paid_cents" not in details and "balance_cents" not in details

@pytest.mark.parametrize("card_name,day,first",[("Nubank",4,"2026-10-05"),("Nubank",5,"2026-11-05"),("Nubank",6,"2026-11-05"),("Mercado Pago/Livre",10,"2026-10-11"),("Mercado Pago/Livre",11,"2026-10-11"),("Mercado Pago/Livre",12,"2026-11-11")])
def test_list_and_detail_follow_card_cycles_not_purchase_month(client,card_name,day,first):
    card=next(x for x in client.catalogs["cards"] if x["name"]==card_name)
    entry(client,card_id=card["id"],occurred_on=f"2026-10-{day:02}",total_cents=10000,installment_count=3)
    data=client.get("/api/invoices",params={"card_id":card["id"]}).json()
    assert data["total"]==3 and sum(x["total_cents"] for x in data["items"])==10000
    assert [i["reference_month"] for i in data["items"]]==sorted(i["reference_month"] for i in data["items"])
    first_invoice=data["items"][0]
    assert first_invoice["closing_date"]==first and first_invoice["total_cents"]==3334
    detail=client.get(f'/api/invoices/{first_invoice["id"]}').json()
    assert detail["items"][0]["amount_cents"]==3334
    assert detail["items"][0]["number"]==1 and detail["items"][0]["installment_count"]==3
    assert detail["items"][0]["occurred_on"]==f"2026-10-{day:02}"

def test_month_filters_are_inclusive_and_combine_with_card_and_state(client):
    created=entry(client,occurred_on="2026-12-05",total_cents=10000,installment_count=3)
    mercado=next(x for x in client.catalogs["cards"] if x["name"]!="Nubank")
    entry(client,request_key="mercado",card_id=mercado["id"],occurred_on="2027-02-01")
    with transaction(client.app.state.engine) as s:
        s.get(Invoice,created["obligations"][1]["invoice_id"]).status="closed"
        s.get(Card,created["card_id"]).active=False
    all_months=client.get("/api/invoices",params={"month_from":"2027-01","month_to":"2027-02","card_id":created["card_id"]}).json()
    assert [i["reference_month"] for i in all_months["items"]]==["2027-01-01","2027-02-01"]
    closed=client.get("/api/invoices",params={"month_from":"2027-02","month_to":"2027-02","status":"closed"}).json()
    assert closed["total"]==1 and closed["items"][0]["status"]=="closed"
    assert closed["items"][0]["total_cents"]==3333
    # Cartão inativo preserva faturas históricas consultáveis.
    assert client.get("/api/invoices",params={"card_id":created["card_id"]}).json()["total"]==3

def test_revisions_cancellations_and_cross_card_moves_do_not_double_count(client):
    original=entry(client,total_cents=10000,installment_count=3)
    new_card=next(x for x in client.catalogs["cards"] if x["name"]!="Nubank")
    parents=client.catalogs["responsibles"][1]["id"]
    payload=dict(request_key="revise",reason="Correção fictícia",description="Versão nova",occurred_on="2026-11-12",total_cents=12001,installment_count=2,
        responsible_id=parents,kind="expense",payment_method="credit",card_id=new_card["id"])
    revised=client.post(f'/api/entries/{original["id"]}/edit',json=payload);assert revised.status_code==200,revised.text
    after=client.get("/api/invoices").json()
    assert sum(i["total_cents"] for i in after["items"])==12001
    assert sum(i["active_obligation_count"] for i in after["items"])==2
    assert all(i["self_cents"]==0 for i in after["items"])
    for obligation in original["obligations"]:
        old=client.get(f'/api/invoices/{obligation["invoice_id"]}').json()
        assert old["empty"] and old["items"]==[] and old["total_cents"]==0
    assert client.get("/api/invoices",params={"responsible_id":original["responsible_id"]}).json()["total"]==0
    assert client.get("/api/invoices",params={"responsible_id":parents}).json()["total"]==2
    response=client.post(f'/api/entries/{revised.json()["id"]}/cancel',json={"request_key":"cancel","reason":"Teste"})
    assert response.status_code==200
    assert all(i["empty"] for i in client.get("/api/invoices").json()["items"])
    assert client.get("/api/invoices",params={"responsible_id":parents}).json()["total"]==0

@pytest.mark.parametrize("kind,method",[("expense","pix"),("expense","cash"),("income","pix")])
def test_non_credit_does_not_appear_in_invoice(client,kind,method):
    entry(client,kind=kind,payment_method=method,card_id=None,total_cents=10000,installment_count=3)
    assert client.get("/api/invoices").json()["total"]==0

def test_invoice_list_and_detail_are_paginated_without_partial_summary(client):
    for number in range(3):
        entry(client,request_key=f"number-{number}",total_cents=10000,installment_count=3)
    page=client.get("/api/invoices",params={"limit":1,"offset":1}).json()
    assert page["total"]==3 and len(page["items"])==1
    invoice=page["items"][0]
    detail=client.get(f'/api/invoices/{invoice["id"]}',params={"limit":1,"offset":1}).json()
    assert detail["active_obligation_count"]==3 and len(detail["items"])==1
    assert detail["total_cents"]==9999 and detail["items"][0]["amount_cents"]==3333
    assert client.get("/api/invoices",params={"offset":50}).json()["items"]==[]
    tail=client.get(f'/api/invoices/{invoice["id"]}',params={"offset":50}).json()
    assert tail["items"]==[] and tail["total_cents"]==9999

@pytest.mark.parametrize("params",[{"month_from":"2026-10-01"},{"month_to":"2026-13"},{"month_from":"0000-01"},{"month_to":"2026-1"},{"month_from":"2027-02","month_to":"2027-01"},{"status":"paid"},{"limit":101},{"offset":-1},{"responsible_id":0},{"card_id":"true"}])
def test_invalid_filters_are_explicit(client,params):
    result=client.get("/api/invoices",params=params)
    assert result.status_code==422,result.text

def test_detail_missing_or_invalid_and_unknown_filters(client):
    assert client.get("/api/invoices/999").status_code==404
    assert client.get("/api/invoices/0").status_code==422
    assert client.get("/api/invoices/1",params={"limit":0}).status_code==422
    assert client.get("/api/invoices",params={"card_id":999}).json()["total"]==0
    assert client.get("/api/invoices",params={"responsible_id":999}).json()["total"]==0

def test_gets_do_not_write_or_infer_payment_and_xss_stays_text(client):
    text='<img src=x onerror=alert(1)>'
    entry(client,description=text,occurred_on="2020-01-01")
    before=hashlib.sha256(client.database_path.read_bytes()).hexdigest()
    invoice=client.get("/api/invoices").json()["items"][0]
    assert invoice["status"]=="open" # Passagem da data não consolida ou liquida.
    details=client.get(f'/api/invoices/{invoice["id"]}').json()
    assert details["items"][0]["description"]==text
    assert "paid" not in details and "cash" not in details
    assert text not in client.get("/").text
    assert "innerHTML" not in client.get("/static/invoices.js").text
    after=hashlib.sha256(client.database_path.read_bytes()).hexdigest()
    assert after==before
    assert client.post(f'/api/invoices/{invoice["id"]}',json={}).status_code==405

def test_safe_integer_limit_checks_invoice_sum_and_avoids_sqlite_overflow(client):
    card=next(x for x in client.catalogs["cards"] if x["name"]=="Nubank")
    for number in range(2):
        record_entry(client.app.state.engine,EntryDraft(request_key=f"huge-{number}",description="Fictícia fora da faixa web",occurred_on=date(2026,10,1),
            total_cents=MAX_INTEGER,installment_count=1,responsible_id=1,payment_method="credit",card_id=card["id"]))
    listing=client.get("/api/invoices")
    assert listing.status_code==422 and "faixa exata" in listing.text
    with transaction(client.app.state.engine) as s:
        invoice_id=s.scalar(select(Invoice.id))
    detail=client.get(f"/api/invoices/{invoice_id}")
    assert detail.status_code==422 and "SQL" not in detail.text

def test_empty_database_has_empty_invoice_read_without_seed(tmp_path):
    path=tmp_path/"empty.db";upgrade_database(path)
    with TestClient(create_app(path),base_url=ORIGIN) as c:
        assert c.get("/api/invoices").json()=={"items":[],"total":0,"limit":50,"offset":0}
        with transaction(c.app.state.engine) as s:
            assert all(s.scalar(select(func.count()).select_from(t))==0 for t in Base.metadata.sorted_tables)
