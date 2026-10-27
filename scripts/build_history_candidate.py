"""Construir candidato separado; nunca promove ou substitui o banco pessoal."""
from pathlib import Path
from hashlib import sha256
import argparse
import json
from sqlalchemy import inspect
from app.persistence.database import PROJECT_ROOT, DEFAULT_DATABASE_PATH, make_engine
from app.persistence.migrations import upgrade_database
from app.services.backup import create_backup, restore_backup, database_fingerprint
from app.services.history_candidate import import_history, summary
from app.services.legacy_reader import APPROVED_SOURCE_SHA256

def build(destination):
    destination=Path(destination).resolve()
    if destination==DEFAULT_DATABASE_PATH.resolve() or "candidate" not in destination.stem.lower():
        raise ValueError("Escolha outro arquivo com candidate no nome.")
    preview=PROJECT_ROOT/"private/history-reconstruction-preview-v3/preview.json"
    confirmations=PROJECT_ROOT/"private/history-confirmations-20261019/confirmed-groups.json"
    source=PROJECT_ROOT/"imports"/APPROVED_SOURCE_SHA256/"observed-obligations-v1/source.xlsm"
    if sha256(source.read_bytes()).hexdigest()!=APPROVED_SOURCE_SHA256:
        raise ValueError("A cópia aprovada mudou.")
    content=preview.read_bytes();decisions=confirmations.read_bytes()
    if json.loads(content)["source_sha256"]!=APPROVED_SOURCE_SHA256:
        raise ValueError("Prévia de outra origem.")
    if not destination.exists():
        engine=make_engine(DEFAULT_DATABASE_PATH)
        try:
            with engine.connect() as c:
                for table in ("entries","financial_events","legacy_import_batches","history_batches"):
                    if c.exec_driver_sql("SELECT COUNT(*) FROM "+table).scalar():
                        raise ValueError("A origem contém dados além dos cadastros; revisar mistura antes de construir candidato.")
        finally:engine.dispose()
        copy=create_backup(DEFAULT_DATABASE_PATH)
        restore_backup(copy["folder"],destination)
    upgrade_database(destination)
    engine=make_engine(destination)
    try:
        operation=import_history(engine,content,decisions)
        result=summary(engine)
        with engine.connect() as c:
            assert not c.exec_driver_sql("PRAGMA foreign_key_check").all()
            assert c.exec_driver_sql("PRAGMA integrity_check").scalar()=="ok"
            assert c.exec_driver_sql("SELECT COUNT(*) FROM financial_events").scalar()==0
            assert c.exec_driver_sql("SELECT COUNT(*) FROM entries").scalar()==0
    finally:engine.dispose()
    backup=create_backup(destination)
    restored=PROJECT_ROOT/"private"/(destination.stem+"-restored-"+backup["id"]+".db")
    restore_backup(backup["folder"],restored)
    assert database_fingerprint(destination)==database_fingerprint(restored)
    evidence={"candidate":str(destination),"operation":operation,"historical_summary":result,
        "backup":backup,"restored":str(restored),"active_database_replaced":False,
        "verified_restoration":True}
    report=PROJECT_ROOT/"private"/(destination.stem+"-validation-"+backup["id"]+".json")
    serialized=json.dumps(evidence,ensure_ascii=True,indent=2,default=str)
    with report.open("x",encoding="utf-8") as stream:stream.write(serialized+"\n")
    return {"candidate":str(destination),"created":operation["created"],"source_rows":result["source_rows"],
        "pending_rows":result["pending_rows"],"pending_cents":result["pending_cents"],
        "backup":backup["folder"],"restored":str(restored),"validation":str(report),
        "active_database_replaced":False}

def main():
    parser=argparse.ArgumentParser(description="Candidato histórico isolado, com restauração conferida.")
    parser.add_argument("--destination",type=Path,default=PROJECT_ROOT/"data/history-candidate-20261019.db")
    args=parser.parse_args()
    print(json.dumps(build(args.destination),ensure_ascii=True,indent=2))

if __name__=="__main__":main()
