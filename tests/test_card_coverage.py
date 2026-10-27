"""Extensão explícita de período, preservando regras usadas e histórico."""
from datetime import date
import pytest
from sqlalchemy import func, select
from test_web import client
from app.domain import CardRule, NUBANK
from app.persistence.database import transaction
from app.persistence.entries import PersistenceValidationError
from app.persistence.models import CardRuleVersion, Entry, FinancialEvent
from app.services.card_coverage import extend_initial_rule
from app.services.catalogs import ConflictError

@pytest.fixture
def prepared(client):
    catalog=client.post("/api/setup",json={"effective_from":"2026-10-19"}).json()
    client.card=next(c["id"] for c in catalog["cards"] if c["name"]=="Nubank")
    with transaction(client.app.state.engine) as session:
        client.rule=session.scalar(select(CardRuleVersion.id).where(CardRuleVersion.card_id==client.card))
    return client

def extend(c, **changes):
    return extend_initial_rule(c.app.state.engine, **(dict(card_id=c.card,
        effective_from=date(2026,10,15), expected_rule_id=c.rule, expected_rule=NUBANK)|changes))

def body(c, **changes):
    return dict(request_key="purchase", description="Compra fictícia",
        occurred_on="2026-10-15",total_cents=15000,installment_count=3,
        responsible_id=1,kind="expense",payment_method="credit",card_id=c.card)|changes

def test_missing_period_explains_date_and_retry_preserves_three_installments(prepared):
    c=prepared
    blocked=c.post("/api/entries",json=body(c))
    assert blocked.status_code==422
    assert "Nubank" in blocked.text and "19/10/2026" in blocked.text
    result=extend(c)
    assert result["created"]
    assert extend(c)==result|{"created":False}
    saved=c.post("/api/entries",json=body(c))
    assert saved.status_code==200,saved.text
    assert [p["amount_cents"] for p in saved.json()["obligations"]]==[5000]*3
    assert [p["due_date"] for p in saved.json()["obligations"]]==["2026-11-05","2026-12-05","2027-01-05"]
    assert c.post("/api/entries",json=body(c)).json()["id"]==saved.json()["id"]
    with transaction(c.app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(Entry))==1
        assert session.scalar(select(func.count()).select_from(FinancialEvent))==0
        assert session.scalar(select(func.count()).select_from(CardRuleVersion))==3

def test_used_rule_is_preserved_and_other_dates_and_cards_remain_protected(prepared):
    c=prepared
    saved=c.post("/api/entries",json=body(c,occurred_on="2026-10-20",request_key="later"))
    assert saved.status_code==200
    before=saved.json()
    extend(c)
    assert c.get(f"/api/entries/{before['id']}").json()==before
    assert c.post("/api/entries",json=body(c,occurred_on="2026-10-14")).status_code==422
    other=next(card["id"] for card in c.get("/api/catalogs").json()["cards"] if card["id"]!=c.card)
    assert c.post("/api/entries",json=body(c,card_id=other)).status_code==422
    with transaction(c.app.state.engine) as session:
        assert session.get(CardRuleVersion,c.rule).effective_from==date(2026,10,19)
        assert session.get(Entry,before["id"]).card_rule_id==c.rule

@pytest.mark.parametrize("changes",[
    {"expected_rule_id":999},
    {"expected_rule":CardRule("Nubank",6,6,False)},
    {"effective_from":date(2026,10,19)},
    {"effective_from":date(2026,10,20)},
])
def test_stale_or_inapplicable_request_does_not_write(prepared,changes):
    with pytest.raises((ConflictError,PersistenceValidationError)):
        extend(prepared,**changes)
    with transaction(prepared.app.state.engine) as session:
        assert session.scalar(select(func.count()).select_from(CardRuleVersion))==2
