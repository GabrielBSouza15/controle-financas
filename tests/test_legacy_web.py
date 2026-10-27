import re
from fastapi.testclient import TestClient
import pytest
from app.main import create_app
from app.services.legacy_import import run_trial
from test_legacy_import import source_file,source_row
from test_settlements import client as empty_client

@pytest.fixture
def legacy_client(tmp_path):
    source,hash_=source_file(tmp_path,[source_row(C="Compra ficticia"),source_row(C="Conta Banco",I="Receita"),source_row(C=None,F=None,H="Pai/Mãe")])
    path=tmp_path/"web-trial.db";run_trial(source,path,tmp_path/"imports",expected_sha256=hash_)
    with TestClient(create_app(path),base_url="http://127.0.0.1:8000") as c:yield c

def test_batch_summary_separates_trial_and_does_not_create_cash(legacy_client):
    c=legacy_client;data=c.get("/api/legacy/batches").json()
    assert data["trial_only"] and len(data["items"])==1
    assert data["items"][0]["counts"]=={"accepted":2,"pending":1,"excluded":0}
    assert data["items"][0]["operational_events_created"]==0
    assert c.get("/api/settlements").json()["cash_in_cents"]==0
    assert c.get("/api/entries").json()["total"]==0
    assert not c.get("/api/catalogs").json()["ready"]
    assert "Histórico do ensaio" in c.get("/").text

def test_filters_pagination_and_raw_origin(legacy_client):
    c=legacy_client
    rows=c.get("/api/legacy/batches/1/rows",params={"limit":1,"offset":1}).json()
    assert rows["total"]==3 and rows["items"][0]["row_number"]==4
    pending=c.get("/api/legacy/batches/1/rows",params={"status":"pending"}).json()
    assert pending["total"]==1 and "income_or_opening_balance" in pending["items"][0]["issues"]
    parents=c.get("/api/legacy/batches/1/rows",params={"responsible_code":"parents"}).json()
    assert parents["total"]==1
    source=c.get(f'/api/legacy/batches/1/rows/{parents["items"][0]["id"]}').json()
    assert source["description"] is None and source["raw"]["cells"]["C"] is None
    assert source["paid_evidence"] and "parents_settlement_unknown" in source["issues"]
    assert c.get("/api/legacy/batches/1/rows",params={"q":"fictícia"}).json()["total"]==1
    assert c.get("/api/legacy/batches/1/rows",params={"month_from":"2026-11"}).json()["total"]==0
    assert c.get("/api/legacy/batches/1/rows",params={"month_from":"2026-11","month_to":"2026-10"}).status_code==422

@pytest.mark.parametrize("path",["/api/legacy/batches/999","/api/legacy/batches/999/rows","/api/legacy/batches/2/rows/1"])
def test_wrong_batch_or_origin_is_not_found(legacy_client,path):
    assert legacy_client.get(path).status_code==404

def test_empty_personal_database_has_no_legacy_and_no_automatic_import(empty_client):
    assert empty_client.get("/api/legacy/batches").json()["items"]==[]
    assert empty_client.get("/api/entries").json()["total"]==0
    assert empty_client.get("/api/legacy/batches/1/rows").status_code==404

def test_legacy_routes_are_read_only(legacy_client):
    c=legacy_client
    token=re.search(r'name="csrf-token" content="([^"]+)"',c.get("/").text).group(1)
    c.headers.update({"Origin":"http://127.0.0.1:8000","X-CSRF-Token":token})
    assert c.post("/api/legacy/batches",json={}).status_code==405
    assert c.post("/api/legacy/batches/1/rows/1",json={"status":"accepted"}).status_code==405
