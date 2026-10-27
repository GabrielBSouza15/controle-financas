"""Adoção local explícita; nunca substitui arquivo existente ou altera atalho."""
import argparse,json
from pathlib import Path
from app.services.adoption import adopt
def main():
    p=argparse.ArgumentParser(description="Adotar candidato em arquivo novo com conferência de recuperação.")
    for name in ("source","destination","restore-check","backup-root"):
        p.add_argument("--"+name,type=Path,required=True)
    p.add_argument("--expected-source-sha256",required=True)
    p.add_argument("--accept-known-limitations",action="store_true")
    p.add_argument("--defer-independent-backup",action="store_true")
    a=p.parse_args()
    result=adopt(a.source,a.destination,a.restore_check,a.backup_root,
        expected_source_sha256=a.expected_source_sha256,accept_known_limits=a.accept_known_limitations,
        defer_independent_backup=a.defer_independent_backup)
    print(json.dumps(result,ensure_ascii=True,indent=2))
if __name__=="__main__":main()
