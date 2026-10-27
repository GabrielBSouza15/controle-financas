from copy import deepcopy
import pytest
from app.services.history_reconstruction import apply_decisions, sequence_candidates
from app.services.legacy_reader import LegacyValidationError

def row(number=1, part=1, on="2026-01-31", count=2, cents=100):
    return {"row_number":number,"status":"accepted","blockers":[],"source_amount_decimal":"1.00",
        "proposal":{"observed_count":count,"observed_number":part,"reference_on":on,
         "amount_cents":cents,"normalized_description":"compra","paid_evidence":False}}

def test_group_anchor_preserved_without_fabricating_missing_parts():
    rows=[row(),row(2,2,"2026-02-28")]
    assert sequence_candidates(rows)==[[1,2]]
    assert sequence_candidates(rows[:1])==[]
    assert sequence_candidates(rows+[deepcopy(rows[1])])==[]
    assert sequence_candidates([row(174),row(236,2,"2026-02-28")])==[]

def test_precision_requires_matching_origin_and_limited_delta():
    r=row();r.update(status="pending",source_amount_decimal="0.999999999999999",blockers=["binary_representation_review"])
    result=apply_decisions([r],{1:("0.999999999999999",100)})
    assert result[0]["proposal"]["amount_cents"]==100 and result[0]["status"]=="prepared"
    assert r["status"]=="pending"
    with pytest.raises(LegacyValidationError):
        apply_decisions([r],{1:("0.999999999999999",101)})
    with pytest.raises(LegacyValidationError):
        apply_decisions([r],{1:("1.000000000000001",100)})

def test_approved_distribution_preserves_total_without_changing_source():
    rows=[row(421),row(477)]
    for r in rows:r.update(status="pending",source_amount_decimal="61.125",blockers=["fractional_cent"])
    result=apply_decisions(rows,{})
    assert [r["proposal"]["amount_cents"] for r in result]==[6113,6112]
    assert sum(r["proposal"]["amount_cents"] for r in result)==12225
    assert all(r["source_amount_decimal"]=="61.125" for r in result)
    assert all(not r["cash_event_created"] and r["payment_pending"] for r in result)

def test_balance_is_separate_role_and_never_cash_or_income_event():
    r=row(44,count=1);r.update(source_amount_decimal="420.00",blockers=["income_or_opening_balance"],status="pending")
    r["proposal"].update(reference_on="2025-01-03",bank_original="Nubank")
    result=apply_decisions([r],{})[0]
    assert result["role"]=="opening_balance"
    assert result["opening_balance"]["amount_cents"]==42000
    assert not result["cash_event_created"]
