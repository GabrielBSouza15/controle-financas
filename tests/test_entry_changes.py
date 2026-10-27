from app.persistence.migrations import head_revision
"""Revisões/cancelamentos preservam evidências e excluem versões antigas do ativo."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import re
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from app.main import create_app
from app.persistence.database import make_engine, transaction
from app.persistence.entries import EntryDraft, record_entry
from app.persistence.migrations import current_revision, migration_config, upgrade_database
from app.persistence.models import Base, Entry, EntryChange, Invoice, Obligation
from app.services.catalogs import initialize_catalogs
from app.services.entries import change_entry, get_entry

ORIGIN="http://127.0.0.1:8000"

@pytest.fixture
def client(tmp_path):
    path=tmp_path/"changes.db"; upgrade_database(path)
    with TestClient(create_app(path),base_url=ORIGIN) as c:
        token=re.search(r'name="csrf-token" content="([^"]+)"',c.get("/").text).group(1)
        c.headers.update({"Origin":ORIGIN,"X-CSRF-Token":token})
        c.catalogs=c.post("/api/setup",json={"effective_from":"2020-01-01"}).json()
        yield c

def body(c,**changes):
    data=dict(request_key="original",description="Compra fictícia",occurred_on="2026-10-05",total_cents=10000,installment_count=3,
        responsible_id=c.catalogs["responsibles"][0]["id"],kind="expense",payment_method="credit",card_id=next(x["id"] for x in c.catalogs["cards"] if x["name"]=="Nubank"))
    return data|changes

def create(c):
    r=c.post("/api/entries",json=body(c));assert r.status_code==200,r.text;return r.json()

def edit(c,source,**changes):
    return c.post(f"/api/entries/{source}/edit",json=body(c,**({"request_key":"edit-1","reason":"Correção fictícia"}|changes)))

def test_revision_preserves_original_schedule_and_active_total(client):
    original=create(client)
    response=edit(client,original["id"],total_cents=12001,installment_count=2,responsible_id=client.catalogs["responsibles"][1]["id"])
    assert response.status_code==200,response.text
    revised=response.json();assert revised["id"]!=original["id"] and revised["responsible_name"]=="Pai/Mãe"
    assert [p["amount_cents"] for p in revised["obligations"]]==[6001,6000]
    old=client.get(f'/api/entries/{original["id"]}').json()
    assert old["obligations"]==original["obligations"] and old["status"]=="superseded" and not old["can_modify"]
    assert old["replacement_id"]==revised["id"]
    assert len(revised["history"])==2 and revised["history"][0]["reason"]=="Correção fictícia"
    active=client.get("/api/entries").json();assert active["total"]==1 and active["items"][0]["total_cents"]==12001
    assert client.get("/api/entries",params={"status":"superseded"}).json()["items"][0]["id"]==original["id"]
    assert client.get("/api/entries",params={"status":"all"}).json()["total"]==2
    with transaction(client.app.state.engine) as s:
        active_sum=s.scalar(select(func.sum(Obligation.amount_cents)).join(Entry).where(~Entry.id.in_(select(EntryChange.source_entry_id))))
        assert active_sum==12001
        assert s.get(Entry,original["id"]).request_key=="original"

def test_cancellation_preserves_history_without_refund_or_new_entry(client):
    original=create(client); data={"request_key":"cancel-1","reason":"Previsão duplicada"}
    result=client.post(f'/api/entries/{original["id"]}/cancel',json=data)
    assert result.status_code==200,result.text
    cancelled=result.json();assert cancelled["status"]=="cancelled" and cancelled["obligations"]==original["obligations"]
    assert not cancelled["can_modify"] and client.get("/api/entries").json()["total"]==0
    assert client.get("/api/entries",params={"status":"cancelled"}).json()["total"]==1
    assert client.post(f'/api/entries/{original["id"]}/cancel',json=data).json()==cancelled
    with transaction(client.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(Entry))==1
        assert s.scalar(select(func.count()).select_from(EntryChange))==1
        assert s.scalar(select(func.count()).select_from(Obligation))==3

def test_repeated_revision_and_keys_cannot_cross_operations(client):
    original=create(client);first=edit(client,original["id"]).json()
    assert edit(client,original["id"]).json()==first
    assert edit(client,original["id"],total_cents=20000).status_code==409
    assert client.post("/api/entries",json=body(client,request_key="edit-1")).status_code==409
    assert client.post(f'/api/entries/{first["id"]}/cancel',json={"request_key":"original","reason":"Inválida"}).status_code==409
    # Repetição do cadastro original aponta à versão original, sem reativá-la.
    assert client.post("/api/entries",json=body(client)).json()["status"]=="superseded"
    assert client.get("/api/entries").json()["total"]==1

def test_stale_view_and_multi_revision_history(client):
    first=create(client);second=edit(client,first["id"]).json()
    stale=client.post(f'/api/entries/{first["id"]}/cancel',json={"request_key":"stale","reason":"Tela antiga"})
    assert stale.status_code==409
    third=client.post(f'/api/entries/{second["id"]}/edit',json=body(client,request_key="edit-2",reason="Segunda revisão",payment_method="pix",card_id=None,occurred_on="2026-01-31")).json()
    assert len(third["history"])==3 and third["obligations"][1]["scheduled_date"]=="2026-02-28"
    assert all(p["invoice_id"] is None for p in third["obligations"])
    assert client.get(f'/api/entries/{first["id"]}').json()["history"]==third["history"]
    cancelled=client.post(f'/api/entries/{third["id"]}/cancel',json={"request_key":"last-cancel","reason":"Cancelado"}).json()
    assert len(cancelled["history"])==3 and cancelled["history"][-1]["status"]=="cancelled"
    assert client.get("/api/entries").json()["total"]==0

@pytest.mark.parametrize("operation",["edit","cancel"])
def test_closed_invoice_blocks_all_changes(client,operation):
    original=create(client)
    with transaction(client.app.state.engine) as s:
        invoice=s.get(Invoice,original["obligations"][1]["invoice_id"]);invoice.status="closed"
    result=edit(client,original["id"]) if operation=="edit" else client.post(f'/api/entries/{original["id"]}/cancel',json={"request_key":"cancel","reason":"Teste"})
    assert result.status_code==409
    detail=client.get(f'/api/entries/{original["id"]}').json();assert not detail["can_modify"] and detail["status"]=="active"
    with transaction(client.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(Entry))==1
        assert s.scalar(select(func.count()).select_from(EntryChange))==0

def test_failure_in_replacement_rolls_back_new_invoices_and_history(client):
    original=create(client)
    with transaction(client.app.state.engine) as s:
        s.add(Invoice(card_id=original["card_id"],reference_month=date(2027,3,1),closing_date=date(2027,3,5),due_date=date(2027,3,5),status="closed"))
    result=edit(client,original["id"],occurred_on="2027-01-06")
    assert result.status_code==422
    with transaction(client.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(Entry))==1
        assert s.scalar(select(func.count()).select_from(EntryChange))==0
        assert s.scalar(select(func.count()).select_from(Invoice))==4
    assert client.get(f'/api/entries/{original["id"]}').json()["status"]=="active"

@pytest.mark.parametrize("changes",[{"reason":" "},{"reason":"x"*1001},{"total_cents":True},{"installment_count":0},{"category_id":999},{"occurred_on":"2026-02-30"}])
def test_invalid_revision_leaves_original_unchanged(client,changes):
    original=create(client);result=edit(client,original["id"],**changes)
    assert result.status_code in (404,422),result.text
    assert client.get(f'/api/entries/{original["id"]}').json()==original

def test_unknown_and_cancelled_source_cannot_be_edited(client):
    assert edit(client,999).status_code==404
    original=create(client)
    client.post(f'/api/entries/{original["id"]}/cancel',json={"request_key":"cancel","reason":"Teste"})
    assert edit(client,original["id"]).status_code==409

def test_audit_and_both_versions_are_immutable_even_via_sql(client):
    original=create(client);revised=edit(client,original["id"]).json()
    statements=["UPDATE entry_changes SET reason='apagada'", "DELETE FROM entry_changes"]
    for item in [original,revised]:
        identity=item["id"];part=item["obligations"][0]
        statements.extend([f"UPDATE entries SET description='alterada' WHERE id={identity}",f"DELETE FROM entries WHERE id={identity}",f"UPDATE obligations SET amount_cents=1 WHERE entry_id={identity}",f"DELETE FROM obligations WHERE entry_id={identity}",f"INSERT INTO obligations(entry_id,number,amount_cents,scheduled_date,invoice_id) VALUES({identity},99,1,'2026-11-05',{part['invoice_id']})"])
    for sql in statements:
        with pytest.raises(IntegrityError):
            with client.app.state.engine.begin() as conn: conn.exec_driver_sql(sql)
    assert client.get(f'/api/entries/{original["id"]}').json()["obligations"]==original["obligations"]

def test_populated_0002_upgrade_preserves_data_and_cannot_drop_audit(tmp_path):
    path=tmp_path/"legacy-schema.db";engine=make_engine(path)
    with engine.begin() as conn:
        config=migration_config();config.attributes["connection"]=conn;command.upgrade(config,"0002_integrity")
    # Preparação e gravação da etapa anterior usam apenas as tabelas antigas.
    initialize_catalogs(engine,date(2020,1,1))
    draft=EntryDraft(request_key="old",description="Anterior",occurred_on=date(2026,1,31),total_cents=10000,installment_count=3,responsible_id=1,payment_method="pix")
    # Reproduzir os registros da revisão congelada, sem usar código atual que requer 0003.
    with engine.begin() as conn:
        conn.exec_driver_sql("INSERT INTO entries(request_key,description,kind,total_cents,installment_count,occurred_on,payment_method,responsible_id) VALUES('old','Anterior','expense',10000,3,'2026-01-31','pix',1)")
        identity=conn.exec_driver_sql("SELECT id FROM entries WHERE request_key='old'").scalar()
        for number,amount,scheduled in [(1,3334,'2026-01-31'),(2,3333,'2026-02-28'),(3,3333,'2026-03-31')]:
            conn.exec_driver_sql("INSERT INTO obligations(entry_id,number,amount_cents,scheduled_date) VALUES(?,?,?,?)",(identity,number,amount,scheduled))
    with engine.connect() as conn:
        before=conn.exec_driver_sql("SELECT * FROM obligations").all()
    upgrade_database(path)
    assert current_revision(path)==head_revision()
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT * FROM obligations").all()==before
        assert compare_metadata(MigrationContext.configure(conn),Base.metadata)==[]
    change_entry(engine,identity,"cancel","Teste de preservação")
    with pytest.raises(RuntimeError,match="histórico"):
        with engine.begin() as conn:
            config=migration_config();config.attributes["connection"]=conn;command.downgrade(config,"0002_integrity")
    assert current_revision(path)==head_revision()
    assert get_entry(engine,identity)["status"]=="cancelled"
    engine.dispose()

def test_competing_operations_have_one_winner(client):
    original=create(client)
    def attempt(n):
        try:
            return change_entry(client.app.state.engine,original["id"],f"parallel-{n}","Cancelamento concorrente")["status"]
        except Exception as exc:
            return type(exc).__name__
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(attempt,[1,2]))
    assert results.count("cancelled")==1
    assert any(x in {"ConflictError","OperationalError","IntegrityError"} for x in results)
    with transaction(client.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(EntryChange))==1

def test_change_routes_require_token_and_do_not_interpret_user_text(client):
    original=create(client)
    assert client.post(f'/api/entries/{original["id"]}/cancel',json={"request_key":"x","reason":"Teste"},headers={"X-CSRF-Token":"wrong"}).status_code==403
    text='<script>alert(1)</script>'
    revised=edit(client,original["id"],reason=text,description=text).json()
    assert revised["description"]==text and revised["history"][0]["reason"]==text
    assert text not in client.get("/").text


def test_internal_registration_cannot_reuse_cancellation_key(client):
    original=create(client)
    change_entry(client.app.state.engine,original["id"],"reserved-cancel","Teste")
    from app.persistence.entries import IdempotencyConflict
    with pytest.raises(IdempotencyConflict):
        record_entry(client.app.state.engine,EntryDraft(request_key="reserved-cancel",description="Outra",occurred_on=date(2026,10,18),total_cents=100,installment_count=1,responsible_id=1,payment_method="pix"))
