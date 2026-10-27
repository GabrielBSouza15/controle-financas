"""Integração financeira em bancos descartáveis, sem alterar dados reais."""
from datetime import date
import re
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from test_settlements import client, entry, event, post, allocations, ORIGIN
from test_history_candidate import documents
from app.main import create_app
from app.services.history_candidate import import_history, summary
from app.persistence.database import transaction
from app.persistence.models import Entry, Invoice, Obligation, HistoryEntryReview
from app.persistence.entries import EntryDraft, record_entry, PersistenceValidationError
from app.persistence.history_overlap import HistoryOverlapConflict

def load(c, change=None):
    import_history(c.app.state.engine, *documents(change))

def payload(c, **changes):
    return dict(request_key="new-observed", description=" Ficticio ", occurred_on="2025-01-03",
        total_cents=5000, installment_count=1, responsible_id=1,
        payment_method="credit", kind="expense", card_id=c.card) | changes

def dashboard(c):
    return c.get("/api/dashboard?month=2025-01&months=3").json()

def test_mixed_budget_detail_totals_and_no_fake_cash(client):
    load(client)
    entry(client, occurred_on="2025-01-03", total_cents=2500)
    data=dashboard(client)
    assert data["budget"]["total_self_expense_cents"]==7500
    assert data["budget"]["total_self_income_cents"]==30000
    assert data["budget"]["parents_expense_cents"]==7000
    assert data["budget"]["projected_self_result_cents"]==22500
    assert data["budget"]["cash_in_cents"]==data["budget"]["cash_out_cents"]==0
    assert data["scope"]["includes_history"] and not data["scope"]["includes_opening_balance"]
    detail=client.get("/api/dashboard/details?month=2025-01&metric=self_expense&limit=1").json()
    assert detail["total"]==2 and detail["total_cents"]==7500 and len(detail["items"])==1
    all_items=client.get("/api/dashboard/details?month=2025-01&metric=self_expense").json()["items"]
    assert sum(i["amount_cents"] for i in all_items)==7500
    historic=next(i for i in all_items if i.get("source")=="history")
    assert historic["number"]==10 and historic["installment_count"]==12 and historic["paid_on"] is None
    assert len(data["historical_invoices"])==1 and len(data["invoices"])==1
    cycle=data["historical_invoices"][0]
    assert (cycle["total_cents"],cycle["paid_undated_cents"],cycle["pending_cents"])==(12000,5000,7000)
    assert summary(client.app.state.engine)["historical_budget_in_operational_dashboard"]

def test_categories_original_are_filterable_without_inventing_catalog(client):
    def change(doc):doc["rows"][0]["proposal"]["category_original"]="  Educação  "
    load(client,change)
    data=dashboard(client)
    assert data["categories"][0]["history_category"]=="Educação"
    assert data["quality"]["uncategorized_expense_items"]==1  # só Pai/Mãe
    detail=client.get("/api/dashboard/details",params={"month":"2025-01","metric":"self_expense","history_category":"Educação"}).json()
    assert detail["total_cents"]==5000 and detail["total"]==1
    assert client.get("/api/dashboard/details?month=2025-01&metric=self_expense&uncategorized=true").json()["total"]==0
    assert client.get("/api/dashboard/details?month=2025-01&metric=cash&history_category=x").status_code==422
    assert client.get("/api/dashboard/details?month=2025-01&metric=self_expense&history_category=x&uncategorized=true").status_code==422

def test_projection_card_person_filters_and_pagination(client):
    load(client)
    data=client.get("/api/projections?month_from=2025-01&months=3&card_id=1&responsible_id=2&limit=1").json()
    assert data["total"]==1 and data["items"][0]["history_record_id"]==2
    assert data["months"][0]["all_expense_cents"]==7000 and data["months"][0]["total_self_income_cents"]==0
    assert client.get("/api/projections?month_from=2025-01&months=3&card_id=2").json()["total"]==0

def test_overlap_rolls_back_every_insert_and_requires_matching_review(client):
    load(client)
    data=payload(client)
    blocked=client.post("/api/entries",json=data)
    assert blocked.status_code==409 and blocked.json()["error"]["code"]=="history_overlap"
    assert blocked.json()["error"]["matches"][0]["source_row"]==3
    with transaction(client.app.state.engine) as s:
        assert all(s.scalar(select(func.count()).select_from(m))==0 for m in (Entry,Obligation,Invoice,HistoryEntryReview))
    wrong=data|{"history_confirmation":{"record_ids":[2],"reason":"Outro compromisso"}}
    assert client.post("/api/entries",json=wrong).status_code==409
    confirmed=data|{"history_confirmation":{"record_ids":[1],"reason":"Comprovante distinto conferido"}}
    saved=client.post("/api/entries",json=confirmed)
    assert saved.status_code==200,saved.text
    assert saved.json()["history_overlap_review"]["record_ids"]==[1]
    assert client.post("/api/entries",json=confirmed).json()["id"]==saved.json()["id"]
    assert client.post("/api/entries",json=data).status_code==409
    assert client.post("/api/entries",json=confirmed|{"history_confirmation":{"record_ids":[1],"reason":"Mudou"}}).status_code==409
    assert dashboard(client)["budget"]["total_self_expense_cents"]==10000
    with transaction(client.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(Entry))==1
    with pytest.raises(IntegrityError),transaction(client.app.state.engine) as s:
        s.connection().exec_driver_sql("UPDATE history_entry_reviews SET reason='alterado'")

@pytest.mark.parametrize("changes",[
    {"description":"Compra diferente"}, {"occurred_on":"2025-02-04"},
    {"total_cents":4999}, {"responsible_id":2}, {"card_id":2},
])
def test_similarity_does_not_erase_distinct_information(client,changes):
    load(client)
    assert client.post("/api/entries",json=payload(client,**changes)).status_code==200
    assert summary(client.app.state.engine)["source_rows"]==5

def test_low_level_and_future_installment_also_check_overlap(client):
    load(client)
    draft=EntryDraft(**payload(client,occurred_on=date(2024,12,4),total_cents=10000,installment_count=2))
    with pytest.raises(HistoryOverlapConflict):record_entry(client.app.state.engine,draft)
    # O cadastro de recorrência passa pela mesma função transacional ao confirmar.
    with transaction(client.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(Entry))==0

def test_edit_guard_and_cancellation_leave_history_unchanged(client):
    load(client)
    saved=entry(client,occurred_on="2025-01-03",total_cents=5000)
    data=payload(client,request_key="edit-one",reason="Correção")
    assert client.post(f"/api/entries/{saved['id']}/edit",json=data).status_code==409
    unchanged=client.get(f"/api/entries/{saved['id']}").json()
    assert unchanged["status"]=="active"
    data["history_confirmation"]={"record_ids":[1],"reason":"Compra diferente confirmada"}
    revised=client.post(f"/api/entries/{saved['id']}/edit",json=data)
    assert revised.status_code==200,revised.text
    assert client.post(f"/api/entries/{saved['id']}/edit",json=data).json()["id"]==revised.json()["id"]
    assert client.post(f"/api/entries/{revised.json()['id']}/cancel",json={"request_key":"cancel-new","reason":"Previsão dispensada"}).status_code==200
    assert dashboard(client)["budget"]["total_self_expense_cents"]==5000
    assert summary(client.app.state.engine)["pending_cents"]==7000

def test_stale_or_invalid_confirmation_cannot_bypass(client):
    load(client)
    confirmed=payload(client,description="Outra",history_confirmation={"record_ids":[1],"reason":"distinto"})
    assert client.post("/api/entries",json=confirmed).status_code==422
    assert client.post("/api/entries",json=payload(client,history_confirmation={"record_ids":[True],"reason":"x"})).status_code==422
    assert client.post("/api/entries",json=payload(client,history_confirmation={"record_ids":[1],"reason":" "})).status_code==422

def test_candidate_new_entry_payment_and_restricted_commands(client,tmp_path):
    load(client)
    with TestClient(create_app(client.app.state.database_path,backup_root=tmp_path/"copies"),base_url=ORIGIN) as c:
        token=re.search(r'name="csrf-token" content="([^"]+)"',c.get("/").text).group(1)
        c.headers.update({"Origin":ORIGIN,"X-CSRF-Token":token})
        c.card=client.card;c.account=client.account
        assert c.post("/api/setup",json={"effective_from":"2020-01-01"}).status_code==409
        assert c.post("/api/categories",json={"name":"Categoria nova"}).status_code==201
        saved=entry(c,total_cents=2500,occurred_on="2025-01-03")
        payment=post(c,event(c,occurred_on="2025-01-05",amount_cents=2500,invoice_id=saved["obligations"][0]["invoice_id"],allocations=allocations(saved)))
        data=dashboard(c)["budget"]
        assert data["total_self_expense_cents"]==7500 and data["cash_out_cents"]==2500
        assert c.post(f"/api/settlements/{payment['id']}/reverse",json={"request_key":"annul-payment","reason":"Informação incorreta"}).status_code==200
        assert dashboard(c)["budget"]["cash_out_cents"]==0
        assert summary(c.app.state.engine)["pending_cents"]==7000

def test_non_credit_overlap_compares_observed_dates_not_invoice(client):
    load(client)
    data=payload(client,description="ficticio",kind="income",payment_method="pix",card_id=None,total_cents=30000)
    blocked=client.post("/api/entries",json=data)
    assert blocked.status_code==409 and blocked.json()["error"]["matches"][0]["record_id"]==3
    moved=client.post("/api/entries",json=data|{"occurred_on":"2025-01-05"})
    assert moved.status_code==200
    assert dashboard(client)["budget"]["total_self_income_cents"]==60000
    assert dashboard(client)["budget"]["cash_in_cents"]==0

def test_review_must_cover_all_coinciding_sources(client):
    def change(doc):
        doc["rows"][1]["proposal"].update(responsible_code="self",amount_cents=5000)
        doc["summary"]["payment_pending_cents"]=5000
    load(client,change)
    data=payload(client,description=" FICTICIO ")
    blocked=client.post("/api/entries",json=data)
    assert {item["record_id"] for item in blocked.json()["error"]["matches"]}=={1,2}
    assert client.post("/api/entries",json=data|{"history_confirmation":{"record_ids":[1],"reason":"Conferi apenas uma"}}).status_code==409
    saved=client.post("/api/entries",json=data|{"history_confirmation":{"record_ids":[1,2],"reason":"Conferi ambas, comprovante novo"}})
    assert saved.status_code==200 and saved.json()["history_overlap_review"]["record_ids"]==[1,2]

def test_recurrence_confirmation_cannot_bypass_history_guard(client):
    from test_recurrences import series,decide
    load(client)
    row=series(client,description="Ficticio",amount_cents=5000,start_on="2025-01-03",
               payment_method="credit",card_id=client.card,account_id=None)
    result=decide(client,row["id"],month="2025-01",expected_cents=5000,actual_cents=5000,occurred_on="2025-01-03")
    assert result.status_code==409 and result.json()["error"]["code"]=="history_overlap"
    occurrence=client.get(f"/api/recurrences/{row['id']}/occurrences?month_from=2025-01&months=1").json()["items"][0]
    assert occurrence["status"]=="forecast" and occurrence["entry_id"] is None
    with transaction(client.app.state.engine) as s:
        assert s.scalar(select(func.count()).select_from(Entry))==0
