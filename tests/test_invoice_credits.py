import pytest
from sqlalchemy.exc import IntegrityError
from test_settlements import client,entry,event,allocations,post
from test_adjustments import adjustment,correction

def scenario(c):
    source=entry(c,total_cents=10000)
    payment=post(c,event(c,amount_cents=10000,allocations=allocations(source)))
    assert adjustment(c,source["obligations"][0]["obligation_id"],-4000).status_code==200
    target=entry(c,request_key="later",occurred_on="2026-10-06",total_cents=10000)
    return source,payment,target

def apply(c,source,target,**kw):
    data={"request_key":"apply-bank-credit","reason":"Credito compensado pelo emissor","source_invoice_id":source["obligations"][0]["invoice_id"],"target_invoice_id":target["obligations"][0]["invoice_id"],"occurred_on":"2026-10-07","amount_cents":4000,"allocations":allocations(target,4000)}
    return c.post("/api/adjustments/invoice-credits",json=data|kw)

def test_credit_in_next_cycle_does_not_create_cash_or_double_reduce_expense(client):
    source,payment,target=scenario(client)
    r=apply(client,source,target);assert r.status_code==200,r.text
    assert apply(client,source,target).json()==r.json()
    old=client.get("/api/invoices/1").json();new=client.get("/api/invoices/2").json()
    assert old["total_cents"]==6000 and old["statement_total_cents"]==10000 and old["issuer_remaining_cents"]==0
    assert new["total_cents"]==10000 and new["statement_total_cents"]==6000 and new["issuer_remaining_cents"]==6000
    assert new["credit_in_cents"]==4000 and old["credit_out_cents"]==4000
    assert client.get("/api/settlements").json()["cash_out_cents"]==10000
    candidates=client.get("/api/settlements/obligations",params={"kind":"invoice_payment","payer_id":1,"invoice_id":2}).json()
    assert candidates["items"][0]["available_cents"]==6000
    post(client,event(client,request_key="later-payment",invoice_id=2,occurred_on="2026-10-08",amount_cents=6000,allocations=allocations(target,6000)))
    assert client.get("/api/invoices/2").json()["issuer_remaining_cents"]==0
    assert client.get("/api/settlements").json()["cash_variation_cents"]==-16000
    projection=client.get("/api/projections",params={"month_from":"2026-10","months":3}).json()
    assert sum(row["registered_self_expense_cents"] for row in projection["months"])==16000

def test_bank_payment_survives_release_and_second_cash_is_rejected(client):
    own=entry(client,total_cents=10000);payment=post(client,event(client,amount_cents=10000,allocations=allocations(own)))
    assert correction(client,payment,10000,action="release").status_code==200
    invoice=client.get("/api/invoices/1").json()
    assert invoice["issuer_paid_cents"]==10000 and invoice["issuer_remaining_cents"]==0 and invoice["issuer_unallocated_cents"]==10000
    response=client.post("/api/settlements",json=event(client,request_key="duplicate-real-payment",amount_cents=10000,allocations=allocations(own)))
    assert response.status_code==422
    assert client.get("/api/settlements").json()["cash_out_cents"]==10000

def test_credit_source_can_only_be_consumed_once_and_is_same_card(client):
    source,payment,target=scenario(client)
    assert apply(client,source,target).status_code==200
    assert apply(client,source,target,request_key="again").status_code==409
    card=next(x["id"] for x in client.catalogs["cards"] if x["id"]!=client.card)
    other=entry(client,request_key="other-card",card_id=card,occurred_on="2026-10-12",total_cents=10000)
    assert apply(client,source,other,request_key="cross-bank",occurred_on="2026-10-13").status_code==422

@pytest.mark.parametrize("kw",[
    {"occurred_on":"9999-01-01"},{"occurred_on":"2026-10-04"},{"amount_cents":4001},{"allocations":[]},
    {"amount_cents":True},{"allocations":[{"obligation_id":99999,"amount_cents":4000}]},
])
def test_bad_credit_applications_leave_no_effect(client,kw):
    source,payment,target=scenario(client)
    assert apply(client,source,target,**kw).status_code in {409,422}
    assert client.get("/api/invoices/1").json()["issuer_remaining_cents"]==-4000
    assert client.get("/api/invoices/2").json()["issuer_remaining_cents"]==10000

def test_closure_and_reconciliation_include_credit_compensation(client):
    source,payment,target=scenario(client);assert apply(client,source,target).status_code==200
    compare=client.post("/api/adjustments/invoices/2/reconcile",json={"request_key":"compare","reason":"Conferida","expected_total_cents":6000})
    assert compare.status_code==200 and compare.json()["matches"]
    closed=client.post("/api/invoices/2/close",json={"request_key":"close","reason":"Conferida","expected_total_cents":6000})
    assert closed.status_code==200,closed.text
    assert closed.json()["closure"]["total_cents"]==6000

def test_correction_of_credit_preserves_history_and_protects_source_payment(client):
    source,payment,target=scenario(client);credit=apply(client,source,target).json()
    assert client.post(f'/api/settlements/{payment["id"]}/reverse',json={"request_key":"bad","reason":"Teste"}).status_code==409
    assert client.post(f'/api/entries/{target["id"]}/cancel',json={"request_key":"cancel","reason":"Teste"}).status_code==409
    for sql in ["DELETE FROM invoice_credit_allocations","UPDATE invoice_credit_applications SET amount_cents=1","UPDATE obligations SET amount_cents=1 WHERE invoice_id=2"]:
        with pytest.raises(IntegrityError):
            with client.app.state.engine.begin() as conn:conn.exec_driver_sql(sql)
    data={"request_key":"reverse-credit","reason":"Vinculo informado errado"}
    r=client.post(f'/api/adjustments/invoice-credits/{credit["id"]}/reverse',json=data);assert r.status_code==200,r.text
    assert client.post(f'/api/adjustments/invoice-credits/{credit["id"]}/reverse',json=data).status_code==200
    assert client.get("/api/invoices/1").json()["issuer_remaining_cents"]==-4000
    assert client.get("/api/invoices/2").json()["issuer_remaining_cents"]==10000
    assert client.get("/api/settlements").json()["cash_out_cents"]==10000

def test_remaining_part_cannot_receive_both_full_cash_and_credit(client):
    source,payment,target=scenario(client);assert apply(client,source,target).status_code==200
    assert client.post("/api/settlements",json=event(client,request_key="overpay",invoice_id=2,occurred_on="2026-10-08",amount_cents=10000,allocations=allocations(target))).status_code==422
    assert client.get("/api/settlements").json()["cash_out_cents"]==10000


def test_review_combined_credit_refund_and_reversal_conserve_budget_and_cash(client):
    source, payment, target = scenario(client)
    credit = apply(client, source, target).json()
    later = post(client, event(client, request_key="review-later-payment", invoice_id=2,
        occurred_on="2026-10-08", amount_cents=6000, allocations=allocations(target, 6000)))
    def conserved(expected_budget, expected_cash, expected_remaining):
        invoices = client.get("/api/invoices").json()["items"]
        budget = client.get("/api/projections", params={"month_from":"2026-10", "months":3}).json()
        cash = client.get("/api/settlements").json()["cash_variation_cents"]
        assert sum(row["registered_self_expense_cents"] for row in budget["months"]) == expected_budget
        assert cash == expected_cash
        assert sum(row["issuer_remaining_cents"] for row in invoices) == expected_remaining
        assert expected_budget + cash == expected_remaining
        assert sum(row["credit_in_cents"] - row["credit_out_cents"] for row in invoices) == 0
    conserved(16000, -16000, 0)
    assert adjustment(client, target["obligations"][0]["obligation_id"], -2000,
                      key="review-target-refund").status_code == 200
    conserved(14000, -16000, -2000)
    assert correction(client, later, 2000, key="review-real-refund",
                      occurred_on="2026-10-09").status_code == 200
    conserved(14000, -14000, 0)
    response = client.post(f'/api/adjustments/invoice-credits/{credit["id"]}/reverse',
        json={"request_key":"review-credit-reversal", "reason":"Compensacao informada por engano"})
    assert response.status_code == 200, response.text
    conserved(14000, -14000, 0)
    assert client.get("/api/invoices/1").json()["issuer_remaining_cents"] == -4000
    assert client.get("/api/invoices/2").json()["issuer_remaining_cents"] == 4000


def test_review_concurrent_credit_applications_cannot_consume_twice(client):
    from concurrent.futures import ThreadPoolExecutor
    source, payment, target = scenario(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda key: apply(client, source, target, request_key=key),
                                  ["review-concurrent-a", "review-concurrent-b"]))
    assert sum(response.status_code == 200 for response in responses) == 1
    assert all(response.status_code in {200, 409, 503} for response in responses)
    assert client.get("/api/invoices/1").json()["credit_out_cents"] == 4000
    assert client.get("/api/invoices/2").json()["credit_in_cents"] == 4000
    assert client.get("/api/settlements").json()["cash_out_cents"] == 10000
