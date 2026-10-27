"""Percurso HTTP com migrações e arquivos temporários; nunca usa o banco pessoal."""
import re
from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from app.main import create_app
from app.persistence.database import transaction
from app.persistence.migrations import upgrade_database
from app.persistence.models import Base, Entry, Invoice, Obligation

ORIGIN = "http://127.0.0.1:8000"

@pytest.fixture
def client(tmp_path):
    path = tmp_path / "web.db"
    upgrade_database(path)
    with TestClient(create_app(path), base_url=ORIGIN) as result:
        page = result.get("/")
        token = re.search(r'name="csrf-token" content="([^"]+)"', page.text).group(1)
        result.headers.update({"Origin": ORIGIN, "X-CSRF-Token": token})
        yield result

@pytest.fixture
def ready(client):
    result = client.post("/api/setup", json={"effective_from":"2020-01-01"})
    assert result.status_code == 200, result.text
    client.catalogs = result.json()
    return client

def payload(client, **changes):
    values = dict(request_key="operation-1", description="Curso fictício", occurred_on="2026-10-05", total_cents=10000,
                  installment_count=3, responsible_id=client.catalogs["responsibles"][0]["id"], kind="expense", payment_method="credit",
                  card_id=next(c["id"] for c in client.catalogs["cards"] if c["name"] == "Nubank"))
    values.update(changes)
    return values

def test_startup_reads_without_initializing(client):
    assert client.get("/api/catalogs").json()["ready"] is False
    with transaction(client.app.state.engine) as session:
        for table in Base.metadata.sorted_tables:
            assert session.scalar(select(func.count()).select_from(table)) == 0
    assert client.get("/static/app.js").status_code == 200
    page = client.get("/")
    assert 'lang="pt-BR"' in page.text and "Receitas e despesas" in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"
    assert client.get("/docs").status_code == 404

def test_missing_database_does_not_create_file(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(RuntimeError, match="Banco ausente"):
        with TestClient(create_app(path), base_url=ORIGIN): pass
    assert not path.exists()

def test_explicit_setup_is_repeatable_but_does_not_rewrite_rules(ready):
    c = ready.catalogs
    assert [(r["code"],r["name"]) for r in c["responsibles"]] == [("self","Eu"),("parents","Pai/Mãe")]
    assert len(c["cards"]) == 2
    assert ready.post("/api/setup",json={"effective_from":"2020-01-01"}).json() == c
    assert ready.post("/api/setup",json={"effective_from":"2021-01-01"}).status_code == 409
    assert ready.get("/api/catalogs").json() == c

def test_catalogs_names_hierarchy_and_duplicate_errors(ready):
    account = ready.post("/api/accounts", json={"name":"  Conta fictícia  "})
    assert account.status_code == 201 and account.json()["name"] == "Conta fictícia"
    assert account.json()["owner_id"] == ready.catalogs["responsibles"][0]["id"]
    duplicate = ready.post("/api/accounts", json={"name":"Conta fictícia"})
    assert duplicate.status_code == 409
    assert "INSERT" not in duplicate.text and "sqlalchemy" not in duplicate.text
    parent = ready.post("/api/categories",json={"name":"Estudos"}).json()
    child = ready.post("/api/categories",json={"name":"Cursos", "parent_id":parent["id"]})
    assert child.status_code == 201 and child.json()["parent_id"] == parent["id"]
    assert ready.post("/api/categories",json={"name":"Inválida", "parent_id":999}).status_code == 404
    assert ready.post("/api/accounts",json={"name":"  "}).status_code == 422

def test_account_requires_initial_responsible(client):
    assert client.post("/api/accounts",json={"name":"Conta"}).status_code == 409

def test_credit_roundtrip_and_idempotency(ready):
    body = payload(ready)
    response = ready.post("/api/entries", json=body)
    assert response.status_code == 200, response.text
    data = response.json()
    assert [p["amount_cents"] for p in data["obligations"]] == [3334,3333,3333]
    assert [p["due_date"] for p in data["obligations"]] == ["2026-11-05","2026-12-05","2027-01-05"]
    assert ready.get(f'/api/entries/{data["id"]}').json() == data
    assert ready.post("/api/entries",json=body).json() == data
    assert ready.post("/api/entries",json=body | {"description":"Diferente"}).status_code == 409
    assert ready.get("/api/entries").json()["total"] == 1


def test_self_pix_debit_can_be_registered_without_any_account(ready):
    assert ready.get("/api/catalogs").json()["accounts"] == []
    body=payload(ready,payment_method="pix_debit",card_id=None,account_id=None,
                 total_cents=10001,installment_count=1,paid_without_account=True)
    response=ready.post("/api/entries",json=body)
    assert response.status_code==200,response.text
    data=response.json()
    assert data["account_id"] is None and data["payment_method"]=="pix"
    assert data["obligations"][0]["amount_cents"]==10001
    assert data["paid_without_account"] and data["obligations"][0]["payment_cents"]==10001
    assert ready.post("/api/entries",json=body).json()==data
    assert ready.get("/api/catalogs").json()["accounts"]==[]
    assert ready.get("/api/entries").json()["total"]==1

@pytest.mark.parametrize("card_name,day,expected",[("Nubank",4,"2026-10-05"),("Nubank",5,"2026-11-05"),("Nubank",6,"2026-11-05"),("Mercado Pago/Livre",10,"2026-10-11"),("Mercado Pago/Livre",11,"2026-10-11"),("Mercado Pago/Livre",12,"2026-11-11")])
def test_api_preserves_card_boundary_rules(ready,card_name,day,expected):
    card = next(c for c in ready.catalogs["cards"] if c["name"] == card_name)
    response = ready.post("/api/entries",json=payload(ready,card_id=card["id"],occurred_on=f"2026-10-{day:02}"))
    assert response.status_code == 200, response.text
    assert response.json()["obligations"][0]["closing_date"] == expected

@pytest.mark.parametrize("kind,method",[("expense","pix"),("expense","cash"),("income","pix")])
def test_non_credit_schedules_without_invoices_or_cash(ready,kind,method):
    account = ready.post("/api/accounts",json={"name":"Conta teste"}).json()
    response = ready.post("/api/entries",json=payload(ready,kind=kind,payment_method=method,card_id=None,account_id=account["id"],occurred_on="2026-01-31",responsible_id=ready.catalogs["responsibles"][1]["id"]))
    assert response.status_code == 200, response.text
    data=response.json()
    assert data["responsible_name"] == "Pai/Mãe"
    assert [p["scheduled_date"] for p in data["obligations"]] == ["2026-01-31","2026-02-28","2026-03-31"]
    assert all(p["invoice_id"] is None for p in data["obligations"])
    with transaction(ready.app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Invoice)) == 0

@pytest.mark.parametrize("changes",[{"total_cents":100.5},{"total_cents":True},{"total_cents":"100"},{"total_cents":2**53},{"installment_count":True},{"installment_count":1201},{"occurred_on":"2026-10-05T00:00:00"},{"occurred_on":20261005},{"occurred_on":"2026-02-30"},{"responsible_id":True},{"kind":"income"},{"description":" "},{"extra":"injected"},{"total_cents":2,"installment_count":3}])
def test_strict_rejection_is_atomic(ready,changes):
    result=ready.post("/api/entries",json=payload(ready,**changes))
    assert result.status_code == 422, result.text
    assert ready.get("/api/entries").json()["total"] == 0
    with transaction(ready.app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Obligation)) == 0
        assert session.scalar(select(func.count()).select_from(Invoice)) == 0

def test_missing_reference_and_missing_rule_do_not_write(ready):
    assert ready.post("/api/entries",json=payload(ready,category_id=999)).status_code == 404
    assert ready.post("/api/entries",json=payload(ready,occurred_on="2019-01-01")).status_code == 422
    assert ready.get("/api/entries").json()["total"] == 0
    assert ready.get("/api/entries/999").status_code == 404

def test_late_closed_invoice_rolls_back_entire_http_operation(ready):
    with transaction(ready.app.state.engine) as session:
        session.add(Invoice(card_id=payload(ready)["card_id"],reference_month=date(2026,12,1),closing_date=date(2026,12,5),due_date=date(2026,12,5),status="closed"))
    assert ready.post("/api/entries",json=payload(ready)).status_code == 422
    assert ready.get("/api/entries").json()["total"] == 0
    with transaction(ready.app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Invoice)) == 1
        assert session.scalar(select(func.count()).select_from(Obligation)) == 0

def test_filters_are_combined_inclusive_paginated_and_escape_wildcards(ready):
    a=payload(ready,description="Curso 100% _ teste")
    assert ready.post("/api/entries",json=a).status_code == 200
    b=payload(ready,request_key="operation-2",description="Salário fictício",kind="income",payment_method="pix",card_id=None,occurred_on="2026-10-06",responsible_id=ready.catalogs["responsibles"][1]["id"])
    assert ready.post("/api/entries",json=b).status_code == 200
    combined={"q":"100% _", "date_from":"2026-10-05","date_to":"2026-10-05","kind":"expense","payment_method":"credit","responsible_id":a["responsible_id"],"card_id":a["card_id"]}
    data=ready.get("/api/entries",params=combined).json()
    assert data["total"]==1 and data["items"][0]["description"]==a["description"]
    assert ready.get("/api/entries",params={"q":"%"}).json()["total"]==1
    page=ready.get("/api/entries",params={"limit":1,"offset":1}).json()
    assert page["total"]==2 and len(page["items"])==1 and page["items"][0]["kind"]=="income"
    assert ready.get("/api/entries",params={"kind":"income","responsible_id":b["responsible_id"]}).json()["total"]==1
    for params in [{"limit":101},{"offset":-1},{"responsible_id":0},{"date_from":"2026-10-07","date_to":"2026-10-01"},{"date_from":"2026-10-05T00:00:00"}]:
        assert ready.get("/api/entries",params=params).status_code==422

def test_user_text_is_only_json_data_and_never_template_code(ready):
    text='<img src=x onerror=alert(1)> {{ 7*7 }}'
    result=ready.post("/api/entries",json=payload(ready,description=text))
    assert result.status_code==200 and result.json()["description"]==text
    assert text not in ready.get("/").text
    js=ready.get("/static/app.js").text
    assert "innerHTML" not in js and "textContent" in js

@pytest.mark.parametrize("headers",[{"Origin":"https://outsider.example"},{"Origin":"null"},{"X-CSRF-Token":"wrong"},{"X-CSRF-Token":""}])
def test_writes_require_local_origin_and_token(client,headers):
    response=client.post("/api/setup",json={"effective_from":"2020-01-01"},headers=headers)
    assert response.status_code==403
    assert not client.get("/api/catalogs").json()["ready"]

def test_body_size_content_type_host_and_malformed_json(client):
    assert client.post("/api/setup",content="x"*65537,headers={"Content-Type":"application/json"}).status_code==413
    assert client.post("/api/setup",content='{}',headers={"Content-Type":"text/plain"}).status_code==415
    assert client.post("/api/setup",content='{',headers={"Content-Type":"application/json"}).status_code==422
    assert client.get("/",headers={"Host":"malicious.example"}).status_code==400
    token=client.headers.pop("X-CSRF-Token")
    assert client.post("/api/setup",json={"effective_from":"2020-01-01"}).status_code==403
    client.headers["X-CSRF-Token"]=token
