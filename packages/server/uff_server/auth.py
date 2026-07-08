"""Autenticação por token Bearer no próprio servidor MCP.

As chaves ficam num arquivo (uma por linha: ``agente  token``), recarregado
quando o arquivo muda. Assim, adicionar/revogar um agente é editar o arquivo —
sem sudo, sem tocar no Apache (que fica só como proxy). Requerer o token no app
também mantém a autenticação mesmo se o MCP for exposto por outro caminho.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send


def extract_bearer(authorization: str) -> str:
    """Extrai o token de um header ``Authorization: Bearer <token>`` (ou ``""``)."""
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


def load_tokens(path: str) -> set[str]:
    """Carrega os tokens válidos do arquivo (2ª coluna, ou a linha toda)."""
    p = Path(path)
    if not p.exists():
        return set()
    tokens: set[str] = set()
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        tokens.add(fields[1] if len(fields) >= 2 else fields[0])
    return tokens


def is_authorized(authorization: str, tokens: set[str]) -> bool:
    token = extract_bearer(authorization)
    return bool(token) and token in tokens


class BearerAuthMiddleware:
    """Middleware ASGI: exige ``Authorization: Bearer <token>`` válido.

    Recarrega o arquivo de tokens quando muda (onboard de agente sem reiniciar).
    """

    def __init__(
        self,
        app,
        token_path: str,
        docs_provider: Callable[[], dict] | None = None,
        docs_path: str = "/mcp/docs",
    ) -> None:
        self.app = app
        self.token_path = token_path
        self.docs_provider = docs_provider
        self.docs_path = docs_path
        self._mtime = -1.0
        self._tokens: set[str] = set()
        self._refresh()

    def _refresh(self) -> None:
        try:
            mtime = Path(self.token_path).stat().st_mtime
        except FileNotFoundError:
            mtime = 0.0
        if mtime != self._mtime:
            self._tokens = load_tokens(self.token_path)
            self._mtime = mtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Documentação pública (sem auth): GET em /mcp/docs, ou GET na raiz /mcp de um
        # navegador (Accept sem text/event-stream). Cliente MCP real (POST/SSE) segue p/ auth.
        if self.docs_provider is not None and scope.get("method") == "GET":
            path = scope.get("path", "").rstrip("/") or "/"
            headers = dict(scope.get("headers") or [])
            accept = headers.get(b"accept", b"").decode("latin-1").lower()
            wants_stream = "text/event-stream" in accept
            if path == self.docs_path.rstrip("/") or (path == "/mcp" and not wants_stream):
                await JSONResponse(self.docs_provider())(scope, receive, send)
                return
        self._refresh()
        headers = dict(scope.get("headers") or [])
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        if not is_authorized(authorization, self._tokens):
            response = JSONResponse(
                {"error": "unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
