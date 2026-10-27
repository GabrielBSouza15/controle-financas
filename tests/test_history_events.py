from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
import json
import pytest
from sqlalchemy.exc import IntegrityError
from test_settlements import client
from test_history_candidate import documents
from app.services.history_candidate import import_history

def prepare(c,change=None):
 import_history(c.app.state.engine,*documents(change))

def payment(c,**kw):
 data={"request_key":"history-payment","kind":"invoice_payment","occurred_on":"2026-10-01",
  "amount_cents":3000,"payer_code":"self","account_id":c.account,"reason":"Comprovante ficticio",
  "allocations":[{"record_id":2,"amount_cents":3000}]}
 return c.post("/api/history/events",json=data|kw)

def test_partial_payment_preserves_source_budget_and_cash_matches_every_view(client):
 prepare(client)
 before=client.get("/api/history/summary").json()
 r=payment(client);assert r.status_code==200,r.text
 assert payment(client).json()==r.json()
 summary=client.get("/api/history/summary").json()
 assert summary["pending_cents"]==4000 and summary["months"]==before["months"]
 part=client.get("/api/history/records/2").json()
 assert not part["paid_evidence"] and part["payment_state"]=="partial" and part["new_payment_cents"]==3000
 assert part["paid_on"] is None and part["observed_count"]==1
 assert part["original_paid_on"] is None and part["new_events"][0]["occurred_on"]=="2026-10-01"
 assert client.get("/api/settlements").json()["cash_out_cents"]==3000
 assert client.get("/api/projections?month_from=2026-10&months=3").json()["months"][0]["cash_out_cents"]==3000
 dashboard=client.get("/api/dashboard?month=2026-10&months=3").json()
 assert dashboard["budget"]["cash_out_cents"]==3000 and dashboard["budget"]["total_self_expense_cents"]==0
 detail=client.get("/api/dashboard/details?month=2026-10&metric=cash").json()
 assert detail["total_cents"]==-3000 and detail["items"][0]["kind"]=="history_invoice_payment"
 invoice=summary["invoices"][0]
 assert invoice["documented_paid_undated_cents"]+invoice["new_payment_cents"]+invoice["pending_cents"]==invoice["total_cents"]

def test_old_paid_never_becomes_new_cash_and_overpayment_rolls_back(client):
 prepare(client)
 assert payment(client,allocations=[{"record_id":1,"amount_cents":3000}]).status_code==422
 assert payment(client,amount_cents=7001,allocations=[{"record_id":2,"amount_cents":7001}]).status_code==422
 assert client.get("/api/history/events").json()["total"]==0
 assert client.get("/api/settlements").json()["cash_out_cents"]==0
 assert client.get("/api/history/records/1").json()["payment_state"]=="paid_undated"

def test_direct_parents_payment_covers_both_dimensions_without_own_cash(client):
 prepare(client)
 r=payment(client,payer_code="parents",account_id=None,amount_cents=7000,allocations=[{"record_id":2,"amount_cents":7000}])
 assert r.status_code==200,r.text
 part=client.get("/api/history/records/2").json()
 assert part["pending_cents"]==0 and part["known_parents_covered_cents"]==7000
 assert part["parents_repayment_state"]=="unknown_history_with_records"
 assert client.get("/api/settlements").json()["cash_variation_cents"]==0
 assert payment(client,request_key="extra-repayment",kind="repayment",payer_code="parents").status_code==422

def test_new_repayment_does_not_pay_issuer_or_turn_into_income(client):
 prepare(client)
 before=client.get("/api/history/summary").json()["months"]
 r=payment(client,kind="repayment",payer_code="parents")
 assert r.status_code==200,r.text
 summary=client.get("/api/history/summary").json()
 assert summary["pending_cents"]==7000 and summary["cash_in_cents"]==3000 and summary["months"]==before
 assert client.get("/api/history/records/2").json()["known_parents_covered_cents"]==3000
 assert payment(client,request_key="bank",amount_cents=7000,allocations=[{"record_id":2,"amount_cents":7000}]).status_code==200

def test_undated_paid_parent_expense_can_receive_explicit_repayment_only(client):
 prepare(client,lambda d:d["rows"][0]["proposal"].update(responsible_code="parents"))
 assert payment(client,kind="repayment",payer_code="parents",allocations=[{"record_id":1,"amount_cents":3000}]).status_code==200
 row=client.get("/api/history/records/1").json()
 assert row["payment_state"]=="paid_undated" and row["parents_repayment_state"]=="unknown_history_with_records"

def test_annulling_wrong_information_restores_pending_and_keeps_history(client):
 prepare(client);event=payment(client).json()
 data={"request_key":"history-annul","reason":"Registro ficticio informado por engano"}
 url=f'/api/history/events/{event["id"]}/reverse'
 assert client.post(url,json=data).status_code==200
 assert client.post(url,json=data).status_code==200
 assert client.get("/api/history/summary").json()["pending_cents"]==7000
 assert client.get("/api/settlements").json()["cash_out_cents"]==0
 assert client.get("/api/history/events").json()["items"][0]["status"]=="reversed"
 assert client.post(url,json=data|{"request_key":"other-annul"}).status_code==409
 assert payment(client,request_key="corrected").status_code==200

@pytest.mark.parametrize("changes",[
 {"amount_cents":True},{"amount_cents":1.5},{"occurred_on":"9999-01-01"},
 {"payer_code":"parents","account_id":1},{"payer_code":None},
 {"kind":"repayment","payer_code":"self"},{"account_id":None},
 {"allocations":[{"record_id":4,"amount_cents":3000}]},
 {"allocations":[{"record_id":5,"amount_cents":3000}]},
 {"allocations":[{"record_id":2,"amount_cents":1500},{"record_id":2,"amount_cents":1500}]},
])
def test_invalid_history_facts_leave_no_event(client,changes):
 prepare(client)
 assert payment(client,**changes).status_code in {409,422}
 assert client.get("/api/history/events").json()["total"]==0

def test_same_key_different_payload_is_conflict(client):
 prepare(client);assert payment(client).status_code==200
 assert payment(client,reason="Diferente").status_code==409
 assert client.get("/api/history/events").json()["total"]==1

def test_history_write_requires_csrf(client):
 prepare(client)
 response=client.post("/api/history/events",json={},headers={"X-CSRF-Token":"bad"})
 assert response.status_code==403

def test_financial_sql_history_and_account_owner_are_protected(client):
 prepare(client);assert payment(client).status_code==200
 for sql in ["DELETE FROM history_events","UPDATE history_events SET amount_cents=1",
  "DELETE FROM history_allocations","UPDATE history_allocations SET amount_cents=1",
  "UPDATE accounts SET owner_id=2 WHERE id=1",
  "UPDATE history_records SET paid_evidence=1 WHERE id=2"]:
  with pytest.raises(IntegrityError):
   with client.app.state.engine.begin() as c:c.exec_driver_sql(sql)
 with pytest.raises(IntegrityError):
  with client.app.state.engine.begin() as c:
   c.exec_driver_sql("INSERT INTO history_events(request_key,kind,occurred_on,amount_cents,payer_code,account_id,reason,recorded_at,payload_json,posted) VALUES('null-payer','invoice_payment','2026-10-01',1000,NULL,1,'Teste','now','{}',0)")

def test_concurrent_full_payments_do_not_duplicate_cash(client):
 prepare(client)
 def run(key):
  return payment(client,request_key=key,amount_cents=7000,allocations=[{"record_id":2,"amount_cents":7000}])
 with ThreadPoolExecutor(max_workers=2) as pool:
  responses=list(pool.map(run,["concurrent-a","concurrent-b"]))
 assert sum(r.status_code==200 for r in responses)==1
 assert all(r.status_code in {200,409,422,503} for r in responses)
 assert client.get("/api/settlements").json()["cash_out_cents"]==7000

def test_repayment_cannot_exceed_remaining_known_coverage(client):
 prepare(client)
 assert payment(client,kind="repayment",payer_code="parents",amount_cents=5000,allocations=[{"record_id":2,"amount_cents":5000}]).status_code==200
 assert payment(client,request_key="too-many",kind="repayment",payer_code="parents").status_code==422
 assert client.get("/api/settlements").json()["cash_in_cents"]==5000

def test_unpaid_income_receipt_and_noncredit_payment_use_actual_dates_only(client):
 def changes(d):
  d["rows"][2]["proposal"]["paid_evidence"]=False
  d["summary"]["payment_pending_cents"]+=30000
 prepare(client,changes)
 r=payment(client,kind="income_receipt",payer_code=None,amount_cents=30000,allocations=[{"record_id":3,"amount_cents":30000}])
 assert r.status_code==200,r.text
 assert client.get("/api/settlements").json()["cash_in_cents"]==30000


@pytest.mark.parametrize("field,value",[("card_code","mercado_pago"),("simulated_cycle","2025-02-01")])
def test_one_bank_payment_cannot_mix_historical_card_cycles(client,field,value):
 def changes(d):
  from copy import deepcopy
  row=deepcopy(d["rows"][1]);row["row_number"]=8;row["proposal"].update({field:value})
  d["rows"].append(row);d["summary"]["payment_pending_cents"]+=7000
 prepare(client,changes)
 response=payment(client,amount_cents=4000,allocations=[{"record_id":2,"amount_cents":2000},{"record_id":6,"amount_cents":2000}])
 assert response.status_code==422,response.text
 assert client.get("/api/history/events").json()["total"]==0
