"""Autenticação por token Bearer no próprio servidor MCP.

As chaves ficam num arquivo (uma por linha: ``agente  token``), recarregado
quando o arquivo muda. Assim, adicionar/revogar um agente é editar o arquivo —
sem sudo, sem tocar no Apache (que fica só como proxy). Requerer o token no app
também mantém a autenticação mesmo se o MCP for exposto por outro caminho.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from pathlib import Path

from starlette.responses import HTMLResponse, JSONResponse
from starlette.types import Receive, Scope, Send

# Agente da requisição corrente (setado pelo middleware, lido pelas tools ao logar).
current_agent: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_agent", default="desconhecido"
)


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


def load_token_agents(path: str) -> dict[str, str]:
    """Mapa token→agente (linhas ``agente  token``). Linhas com só um campo são ignoradas."""
    p = Path(path)
    if not p.exists():
        return {}
    agents: dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) >= 2:
            agents[fields[1]] = fields[0]
    return agents


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
        html_renderer: Callable[[dict], str] | None = None,
        admin_html: str | None = None,
        admin_provider: Callable[[dict], dict] | None = None,
        admin_authorized: Callable[[str], bool] | None = None,
        admin_logout_html: str | None = None,
    ) -> None:
        self.app = app
        self.token_path = token_path
        self.docs_provider = docs_provider
        self.docs_path = docs_path
        self.html_renderer = html_renderer
        self.admin_html = admin_html
        self.admin_provider = admin_provider
        self.admin_authorized = admin_authorized
        self.admin_logout_html = admin_logout_html
        self._mtime = -1.0
        self._tokens: set[str] = set()
        self._agents: dict[str, str] = {}
        self._refresh()

    def _refresh(self) -> None:
        try:
            mtime = Path(self.token_path).stat().st_mtime
        except FileNotFoundError:
            mtime = 0.0
        if mtime != self._mtime:
            self._tokens = load_tokens(self.token_path)
            self._agents = load_token_agents(self.token_path)
            self._mtime = mtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # Página pública de saída do painel (sem auth): landing após limpar o Basic Auth.
        path0 = scope.get("path", "").rstrip("/") or "/"
        if self.admin_provider is not None and path0 == "/mcp/admin/logout":
            await HTMLResponse(self.admin_logout_html or "Você saiu.")(scope, receive, send)
            return
        # Painel de administração (HTTP Basic próprio): /mcp/admin (HTML) e /mcp/admin/api (JSON).
        if self.admin_provider is not None and path0 in ("/mcp/admin", "/mcp/admin/api"):
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode("latin-1")
            if self.admin_authorized is None or not self.admin_authorized(auth):
                resp = JSONResponse(
                    {"error": "admin auth required"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="BaseUFF Admin"'},
                )
                await resp(scope, receive, send)
                return
            if path0 == "/mcp/admin/api":
                from urllib.parse import parse_qs

                qs = parse_qs(scope.get("query_string", b"").decode("latin-1"))
                params = {k: v[0] for k, v in qs.items()}
                await JSONResponse(self.admin_provider(params))(scope, receive, send)
            else:
                await HTMLResponse(self.admin_html or "")(scope, receive, send)
            return
        # Documentação pública (sem auth): GET em /mcp/docs, ou GET na raiz /mcp de um
        # navegador (Accept sem text/event-stream). Cliente MCP real (POST/SSE) segue p/ auth.
        if self.docs_provider is not None and scope.get("method") == "GET":
            path = scope.get("path", "").rstrip("/") or "/"
            headers = dict(scope.get("headers") or [])
            accept = headers.get(b"accept", b"").decode("latin-1").lower()
            wants_stream = "text/event-stream" in accept
            if path == self.docs_path.rstrip("/") or (path == "/mcp" and not wants_stream):
                docs = self.docs_provider()
                if self.html_renderer is not None and "text/html" in accept:
                    response = HTMLResponse(self.html_renderer(docs))
                else:
                    response = JSONResponse(docs)
                await response(scope, receive, send)
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
        # identifica o agente para o log de consultas (propaga via contextvar)
        current_agent.set(self._agents.get(extract_bearer(authorization), "desconhecido"))
        await self.app(scope, receive, send)
