"""Backup local protegido; a web não recebe caminhos nem substitui o banco ativo."""
from uuid import UUID
from fastapi import APIRouter, Request
from .schemas import InputModel
from app.services import backup

router = APIRouter(prefix="/api/maintenance")


class BackupInput(InputModel):
    request_key: UUID


@router.get("/backups")
def list_backups(request: Request):
    result = backup.list_backups(request.app.state.backup_root)
    result["backup_root"] = str(request.app.state.backup_root)
    result["active_database_name"] = request.app.state.database_path.name
    result["independent_destination_configured"] = False
    return result


@router.post("/backups")
def create_backup(request: Request, payload: BackupInput):
    return backup.create_backup(request.app.state.database_path, request.app.state.backup_root,
                                request_key=str(payload.request_key))


@router.get("/backups/{identity}")
def verify_backup(request: Request, identity: UUID):
    return backup.verify_backup(request.app.state.backup_root / ("finance-" + str(identity)))
