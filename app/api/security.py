"""Proteções do aplicativo local: host, origem, token e recursos próprios."""

import secrets
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse

MAX_BODY_BYTES = 65536
CSP = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"


class LocalSecurityMiddleware:
    def __init__(self, app, csrf_token):
        self.app = app
        self.csrf_token = csrf_token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def secured_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CSP
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "no-referrer"
            await send(message)

        async def reject(status, code, message):
            response = JSONResponse({"error": {"code": code, "message": message}}, status_code=status)
            await response(scope, receive, secured_send)

        if scope["method"] not in {"GET", "HEAD", "OPTIONS"}:
            headers = Headers(scope=scope)
            expected_origin = f"{scope['scheme']}://{headers.get('host', '')}"
            token = headers.get("x-csrf-token", "")
            if headers.get("origin") != expected_origin or len(token) > 256 or not secrets.compare_digest(token.encode("utf-8"), self.csrf_token.encode("ascii")):
                await reject(403, "origin_or_token", "A sessão de cadastro não é válida. Recarregue a página e tente novamente.")
                return
            if headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
                await reject(415, "content_type", "Envie os dados no formato JSON.")
                return
            # Limite efetivo também para corpos sem Content-Length ou em partes.
            chunks = []
            length = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                length += len(chunk)
                if length > MAX_BODY_BYTES:
                    await reject(413, "body_too_large", "O formulário excede o tamanho permitido.")
                    return
                chunks.append(chunk)
                if not message.get("more_body", False):
                    break
            delivered = False

            async def buffered_receive():
                nonlocal delivered
                if not delivered:
                    delivered = True
                    return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
                return await receive()

            await self.app(scope, buffered_receive, secured_send)
        else:
            await self.app(scope, receive, secured_send)


class CandidateReadOnlyMiddleware:
    """Mantém o candidato separado; permite somente operações já integradas."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        import re
        allowed = scope.get("method") == "POST" and (
            scope.get("path") in {"/api/maintenance/backups", "/api/accounts", "/api/categories",
                "/api/history/events", "/api/history/operations", "/api/entries", "/api/settlements", "/api/recurrences",
                "/api/personal/reinvestment", "/api/personal/bill",
                "/api/adjustments/obligations", "/api/adjustments/corrections", "/api/adjustments/transfers", "/api/adjustments/cycles", "/api/adjustments/invoice-credits"}
            or re.fullmatch(r"/api/(history/events/[0-9]+/reverse|entries/[0-9]+/(edit|cancel)|settlements/[0-9]+/(allocate|reverse)|invoices/[0-9]+/close|recurrences/[0-9]+/(changes|occurrences/[0-9]{4}-[0-9]{2})|adjustments/(transfers/[0-9]+/reverse|invoices/[0-9]+/reconcile|invoice-credits/[0-9]+/reverse))", scope.get("path", "")))
        if (scope["type"] == "http" and scope["method"] not in {"GET", "HEAD", "OPTIONS"}
                and getattr(scope.get("app").state, "history_candidate", False) and not allowed):
            response = JSONResponse({"error": {"code": "history_candidate_restricted",
                "message": "Operação ainda não integrada ao candidato. Use novos lançamentos, suas liquidações ou o Histórico financeiro."}}, status_code=409)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
