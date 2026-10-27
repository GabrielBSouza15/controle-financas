"""Fábrica da aplicação: abrir a web não inicializa ou preenche o banco."""

from contextlib import asynccontextmanager
from pathlib import Path
import secrets
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.exc import IntegrityError, OperationalError
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware
from app.api.routes import router
from app.api.invoices import router as invoice_router
from app.api.settlements import router as settlement_router
from app.api.adjustments import router as adjustment_router
from app.api.recurrences import router as recurrence_router
from app.api.legacy import router as legacy_router
from app.api.dashboard import router as dashboard_router
from app.api.maintenance import router as maintenance_router
from app.services.backup import DEFAULT_BACKUP_ROOT
from app.local_identity import APPLICATION_ID, database_id
from app.api.security import LocalSecurityMiddleware, CandidateReadOnlyMiddleware
from app.api.history import router as history_router
from app.api.history_events import router as history_event_router
from app.persistence.models import HistoryBatch, HistoryAdoption
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.domain import FinancialValidationError
from app.persistence.database import DEFAULT_DATABASE_PATH, make_engine
from app.persistence.entries import IdempotencyConflict, PersistenceValidationError
from app.persistence.history_overlap import HistoryOverlapConflict
from app.persistence.migrations import current_revision, migration_config
from app.services.catalogs import ConflictError, NotFoundError

APP_ROOT = Path(__file__).resolve().parent


def error_response(status, code, message, fields=None, headers=None):
    error = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return JSONResponse({"error": error}, status_code=status, headers=headers)


def create_app(database_path: str | Path = DEFAULT_DATABASE_PATH, *, backup_root: str | Path = DEFAULT_BACKUP_ROOT) -> FastAPI:
    database_path = Path(database_path).resolve()
    token = secrets.token_urlsafe(32)
    backup_root = Path(backup_root).resolve()

    @asynccontextmanager
    async def lifespan(app):
        head = ScriptDirectory.from_config(migration_config()).get_current_head()
        if not database_path.is_file() or current_revision(database_path) != head:
            raise RuntimeError("Banco ausente/desatualizado. Execute python -m app.persistence upgrade no ambiente do projeto.")
        engine = make_engine(database_path)
        app.state.engine = engine
        app.state.database_path = database_path
        app.state.backup_root = backup_root
        with Session(engine) as session:
            app.state.adopted = session.get(HistoryAdoption,1) is not None
            app.state.history_candidate = session.scalar(select(HistoryBatch.id).where(HistoryBatch.posted.is_(True)).limit(1)) is not None
        try:
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Controle financeiro local", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.add_middleware(CandidateReadOnlyMiddleware)
    app.add_middleware(LocalSecurityMiddleware, csrf_token=token)
    app.mount("/static", StaticFiles(directory=APP_ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=APP_ROOT / "templates")

    @app.get("/api/health")
    def health():
        return {"application": APPLICATION_ID, "database_id": database_id(database_path),
                "revision": ScriptDirectory.from_config(migration_config()).get_current_head()}

    @app.get("/")
    def index(request: Request):
        return templates.TemplateResponse(request=request, name="index.html", context={"csrf_token": token,"history_candidate":app.state.history_candidate and not app.state.adopted,"adopted":app.state.adopted})

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exc):
        fields = []
        for item in exc.errors():
            message = item["msg"]
            if message.startswith("Value error, "):
                message = message.removeprefix("Value error, ")
            elif not message.startswith("Informe"):
                message = "Confira o tipo, o formato e o limite deste campo."
            fields.append({"field": ".".join(str(part) for part in item["loc"] if part != "body"), "message": message})
        return error_response(422, "validation", "Revise os campos informados.", fields)

    @app.exception_handler(NotFoundError)
    async def not_found(request, exc):
        return error_response(404, "not_found", str(exc))

    @app.exception_handler(HistoryOverlapConflict)
    async def history_overlap(request, exc):
        return JSONResponse({"error": {"code": "history_overlap", "message": str(exc), "matches": exc.matches}}, status_code=409)

    @app.exception_handler(IdempotencyConflict)
    @app.exception_handler(ConflictError)
    async def conflict(request, exc):
        return error_response(409, "conflict", str(exc))

    @app.exception_handler(PersistenceValidationError)
    @app.exception_handler(FinancialValidationError)
    async def invalid_operation(request, exc):
        return error_response(422, "invalid_operation", str(exc))

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, exc):
        return error_response(409, "integrity", "Já existe um cadastro com esses dados ou um vínculo não pôde ser confirmado.")

    @app.exception_handler(OperationalError)
    async def unavailable(request, exc):
        return error_response(503, "database_unavailable", "O banco não está disponível agora. Tente novamente mantendo os mesmos dados.", headers={"Retry-After": "1"})

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(exc.status_code, "http_error", "Recurso ou operação não disponível.")

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return error_response(500, "unexpected", "Não foi possível concluir a operação. Seus dados não foram confirmados pela interface.")

    from app.api.personal import router as personal_router
    app.include_router(personal_router)
    app.include_router(router)
    app.include_router(invoice_router)
    app.include_router(settlement_router)
    app.include_router(adjustment_router)
    app.include_router(recurrence_router)
    app.include_router(legacy_router)
    app.include_router(dashboard_router)
    app.include_router(maintenance_router)
    app.include_router(history_router)
    app.include_router(history_event_router)
    from app.api.history_operations import router as history_operation_router
    app.include_router(history_operation_router)
    return app
