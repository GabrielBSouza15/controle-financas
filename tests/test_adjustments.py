import pytest
from sqlalchemy.exc import IntegrityError
from test_settlements import client, entry, event, allocations, post

def adjustment(c,part,amount,key="adj"):
    return c.post("/api/adjustments/obligations",json={"request_key":key,"reason":"Credito ficticio","occurred_on":"2026-10-06","obligation_id":part,"delta_cents":amount})

def link(c,payment):
    return c.get(f'/api/settlements/{payment["id"]}').json()["allocations"][0]["allocation_id"]

def correction(c,payment,amount,action="refund",key="refund",**kw):
    data={"request_key":key,"reason":"Correcao ficticia","action":action,"occurred_on":"2026-10-07","event_id":payment["id"],"amount_cents":amount,"account_id":c.account if action=="refund" else None,"offsets":[{"allocation_id":link(c,payment),"amount_cents":amount}]}
    return c.post("/api/adjustments/corrections",json=data|kw)

def test_credit_after_payment_preserves_closed_snapshot_and_refund_is_cash(client):
    own=entry(client,total_cents=10000)
    payment=post(client,event(client,amount_cents=10000,allocations=allocations(own)))
    assert client.post("/api/invoices/1/close",json={"request_key":"close","reason":"Conferida","expected_total_cents":10000}).status_code==200
    assert adjustment(client,own["obligations"][0]["obligation_id"],-4000).status_code==200
    invoice=client.get("/api/invoices/1").json()
    assert invoice["closure"]["total_cents"]==10000 and invoice["total_cents"]==6000
    assert invoice["issuer_remaining_cents"]==-4000 and invoice["liquidation_status"]=="credit"
    assert client.get("/api/settlements").json()["cash_variation_cents"]==-10000
    r=correction(client,payment,4000);assert r.status_code==200,r.text
    assert correction(client,payment,4000).json()==r.json()
    assert client.get("/api/invoices/1").json()["issuer_remaining_cents"]==0
    assert client.get("/api/settlements").json()["cash_variation_cents"]==-6000
    assert client.get("/api/settlements",params={"date_from":"2026-10-07","date_to":"2026-10-07"}).json()["cash_in_cents"]==4000
    assert client.post(f'/api/settlements/{payment["id"]}/reverse',json={"request_key":"bad-reverse","reason":"Erro"}).status_code==409

def test_release_then_reallocate_changes_links_without_duplicate_cash(client):
    own=entry(client,total_cents=10000);other=entry(client,request_key="second",total_cents=10000)
    payment=post(client,event(client,amount_cents=10000,allocations=allocations(own)))
    r=correction(client,payment,10000,action="release");assert r.status_code==200,r.text
    assert client.get(f'/api/settlements/{payment["id"]}').json()["available_credit_cents"]==10000
    r=client.post(f'/api/settlements/{payment["id"]}/allocate',json={"request_key":"reassign","reason":"Parcela correta","allocations":allocations(other)})
    assert r.status_code==200,r.text
    assert client.get(f'/api/entries/{own["id"]}').json()["obligations"][0]["payment_cents"]==0
    assert client.get(f'/api/entries/{other["id"]}').json()["obligations"][0]["payment_cents"]==10000
    assert client.get("/api/settlements").json()["cash_out_cents"]==10000
    assert correction(client,payment,10000,action="release",key="again").status_code==422

def test_unallocated_advance_refund_limits_later_use(client):
    parent=entry(client,responsible_id=2,total_cents=10000)
    advance=post(client,event(client,kind="repayment",payer_id=2,invoice_id=None,amount_cents=10000,allocations=[]))
    r=client.post("/api/adjustments/corrections",json={"request_key":"return-advance","reason":"Devolvido","action":"refund","occurred_on":"2026-10-07","event_id":advance["id"],"amount_cents":4000,"unallocated_cents":4000,"account_id":client.account,"offsets":[]})
    assert r.status_code==200,r.text
    assert client.get(f'/api/settlements/{advance["id"]}').json()["available_credit_cents"]==6000
    assert client.post(f'/api/settlements/{advance["id"]}/allocate',json={"request_key":"too","reason":"Teste","allocations":allocations(parent,7000)}).status_code==422
    assert client.get("/api/settlements").json()["cash_variation_cents"]==6000

def test_transfers_have_two_legs_and_can_annul_wrong_information(client):
    other=client.post("/api/accounts",json={"name":"Outra conta ficticia"}).json()["id"]
    data={"request_key":"transfer","reason":"Realizada","source_account_id":client.account,"target_account_id":other,"occurred_on":"2026-10-06","amount_cents":2000}
    r=client.post("/api/adjustments/transfers",json=data);assert r.status_code==200,r.text
    assert client.post("/api/adjustments/transfers",json=data).json()==r.json()
    cash=client.get("/api/settlements").json()
    assert cash["cash_in_cents"]==cash["cash_out_cents"]==2000 and cash["cash_variation_cents"]==0
    assert client.get("/api/settlements",params={"account_id":client.account}).json()["cash_variation_cents"]==-2000
    assert client.get("/api/settlements",params={"account_id":other}).json()["cash_variation_cents"]==2000
    assert client.post("/api/adjustments/transfers",json=data|{"request_key":"same","target_account_id":client.account}).status_code==422
    assert client.post(f'/api/adjustments/transfers/{r.json()["id"]}/reverse',json={"request_key":"annul","reason":"Engano"}).status_code==200
    assert client.get("/api/settlements").json()["cash_in_cents"]==0

def test_reconciliation_is_snapshot_and_stales_after_adjustment(client):
    own=entry(client,total_cents=10000)
    data={"request_key":"compare","reason":"Fatura ficticia","expected_total_cents":9900}
    r=client.post("/api/adjustments/invoices/1/reconcile",json=data)
    assert r.status_code==200 and r.json()["difference_cents"]==-100 and not r.json()["matches"]
    assert client.get("/api/invoices/1").json()["reconciliation"]["current"]
    assert adjustment(client,own["obligations"][0]["obligation_id"],-100).status_code==200
    current=client.get("/api/invoices/1").json()
    assert not current["reconciliation"]["current"] and current["reconciliation"]["difference_cents"]==0
    assert client.post("/api/adjustments/invoices/1/reconcile",json=data|{"request_key":"again"}).json()["matches"]

@pytest.mark.parametrize("delta",[-10001,0,True,1.5,"100"])
def test_bad_adjustments_leave_no_changes(client,delta):
    own=entry(client,total_cents=10000)
    assert adjustment(client,own["obligations"][0]["obligation_id"],delta).status_code==422
    assert client.get("/api/invoices/1").json()["total_cents"]==10000

def test_adjusted_capacity_and_audit_protection(client):
    own=entry(client,total_cents=10000)
    assert adjustment(client,own["obligations"][0]["obligation_id"],500).status_code==200
    post(client,event(client,amount_cents=10500,allocations=allocations(own,10500)))
    assert client.get("/api/invoices/1").json()["issuer_remaining_cents"]==0
    for sql in ["DELETE FROM obligation_adjustments","UPDATE obligation_adjustments SET delta_cents=1","UPDATE obligations SET amount_cents=1","UPDATE entries SET responsible_id=2"]:
        with pytest.raises(IntegrityError):
            with client.app.state.engine.begin() as conn:conn.exec_driver_sql(sql)
    assert client.post(f'/api/entries/{own["id"]}/cancel',json={"request_key":"cancel","reason":"Teste"}).status_code==409

def test_parents_refund_does_not_change_issuer_payment(client):
    parent=entry(client,responsible_id=2,total_cents=10000)
    post(client,event(client,amount_cents=10000,allocations=allocations(parent)))
    repay=post(client,event(client,request_key="repay",kind="repayment",payer_id=2,invoice_id=None,amount_cents=10000,allocations=allocations(parent)))
    assert adjustment(client,parent["obligations"][0]["obligation_id"],-3000).status_code==200
    assert client.get("/api/invoices/1").json()["parents_uncovered_cents"]==-3000
    r=correction(client,repay,3000);assert r.status_code==200,r.text
    invoice=client.get("/api/invoices/1").json()
    assert invoice["parents_uncovered_cents"]==0 and invoice["issuer_remaining_cents"]==-3000
    assert client.get("/api/settlements").json()["cash_variation_cents"]==-3000

def test_refund_limits_and_transaction_rollback(client):
    own=entry(client,total_cents=10000);payment=post(client,event(client,amount_cents=10000,allocations=allocations(own)))
    for fields in [{"offsets":[]},{"account_id":None},{"occurred_on":"9999-01-01"},{"offsets":[{"allocation_id":99999,"amount_cents":100}]}]:
        assert correction(client,payment,100,**fields).status_code in {404,422}
    assert client.get("/api/settlements").json()["cash_variation_cents"]==-10000
    assert client.get("/api/adjustments").json()["items"]==[]

def test_cycle_move_keeps_purchase_date_and_records_origin(client):
    own=entry(client,total_cents=10000)
    part=own["obligations"][0]
    data={"request_key":"move","reason":"Cobranca processada no ciclo seguinte","obligation_id":part["obligation_id"],"expected_invoice_id":part["invoice_id"],"target_month":"2026-11"}
    r=client.post("/api/adjustments/cycles",json=data);assert r.status_code==200,r.text
    assert client.post("/api/adjustments/cycles",json=data).json()==r.json()
    updated=client.get(f'/api/entries/{own["id"]}').json()
    assert updated["occurred_on"]=="2026-10-01" and updated["obligations"][0]["scheduled_date"]=="2026-11-05"
    assert updated["cycle_moves"][0]["source_invoice_id"]==part["invoice_id"]
    assert client.get("/api/invoices/1").json()["total_cents"]==0
    assert client.get("/api/invoices/2").json()["total_cents"]==10000
    assert client.post("/api/adjustments/cycles",json=data|{"request_key":"stale"}).status_code==409
    for sql in ["DELETE FROM obligation_moves","UPDATE obligations SET invoice_id=1,scheduled_date='2026-10-05'"]:
        with pytest.raises(IntegrityError):
            with client.app.state.engine.begin() as conn:conn.exec_driver_sql(sql)

def test_cycle_move_cannot_rewrite_settled_purchase(client):
    own=entry(client,total_cents=10000);post(client,event(client,amount_cents=10000,allocations=allocations(own)))
    r=client.post("/api/adjustments/cycles",json={"request_key":"move","reason":"Teste","obligation_id":own["obligations"][0]["obligation_id"],"expected_invoice_id":1,"target_month":"2026-11"})
    assert r.status_code==409
