"""Adoção explícita: fonte intacta, destino novo, limites registrados."""
import json
import pytest
from datetime import date
from sqlalchemy.orm import Session
from app.persistence.database import make_engine
from app.persistence.models import HistoryAdoption
from app.services.adoption import adopt
from app.services.backup import database_fingerprint
from app.services.catalogs import ConflictError
from app.persistence.entries import PersistenceValidationError
from test_settlements import client,ORIGIN
from test_history_events import prepare,payment

def test_adoption_preserves_all_records_and_effective_cash(client,tmp_path):
    prepare(client);assert payment(client).status_code==200
    before=database_fingerprint(client.app.state.database_path)
    target=tmp_path/"active.db";restored=tmp_path/"restored.db"
    result=adopt(client.app.state.database_path,target,restored,tmp_path/"copies",
        expected_source_sha256="a"*64,accept_known_limits=True,defer_independent_backup=True)
    assert result["adopted"] and result["independent_backup_deferred"] and result["pending_cents"]==4000
    assert database_fingerprint(client.app.state.database_path)==before
    assert database_fingerprint(target)==database_fingerprint(restored)
    engine=make_engine(target)
    try:
        with Session(engine) as s:
            receipt=s.get(HistoryAdoption,1)
            assert json.loads(receipt.acceptance_json)["unknown_payment_dates_and_repayments_preserved"]
    finally:engine.dispose()
    from app.main import create_app
    from fastapi.testclient import TestClient
    with TestClient(create_app(target),base_url=ORIGIN) as c:
        assert "Base principal" in c.get("/").text
        assert not c.get("/api/history/summary").json()["candidate_only"]
        assert c.get("/api/dashboard?month=2026-10&months=3").json()["budget"]["cash_out_cents"]==3000
    with pytest.raises(ConflictError):
        adopt(client.app.state.database_path,target,tmp_path/"other.db",tmp_path/"copies",
            expected_source_sha256="a"*64,accept_known_limits=True,defer_independent_backup=True)

@pytest.mark.parametrize("accepted,deferred",[(False,False),(True,False),(False,True)])
def test_adoption_needs_explicit_acceptance(client,tmp_path,accepted,deferred):
    prepare(client)
    with pytest.raises(PersistenceValidationError):
        adopt(client.app.state.database_path,tmp_path/"active.db",tmp_path/"check.db",tmp_path/"copies",
            expected_source_sha256="a"*64,accept_known_limits=accepted,defer_independent_backup=deferred)
    assert not (tmp_path/"active.db").exists()

def test_adoption_rejects_different_source_and_existing_destination(client,tmp_path):
    prepare(client)
    with pytest.raises(ConflictError):
        adopt(client.app.state.database_path,tmp_path/"active.db",tmp_path/"check.db",tmp_path/"copies",
            expected_source_sha256="b"*64,accept_known_limits=True,defer_independent_backup=True)
    assert not (tmp_path/"active.db").exists()
