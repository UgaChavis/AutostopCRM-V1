"""The cold MCP namespace is checked outside all SDK authentication middleware."""

from __future__ import annotations

from urllib.parse import urlsplit

from mcp.server.auth.routes import build_metadata
from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse, Response

from ..cold_http_ingress import STATUS_PATH


def local_endpoint(config, host, port, path):
    prefix = config.prefix("mcp") if config is not None else ""
    authority = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"http://{authority}:{port}{prefix}{path}"


class ColdMcpIngress:
    def __init__(self, app, *, config, auth):
        self.app, self.config = app, config
        base, resource, _ = config.mcp_urls()
        prefix = config.prefix("mcp")
        self.metadata = {}
        if auth is not None:
            authorization = build_metadata(
                auth.issuer_url,
                auth.service_documentation_url,
                auth.client_registration_options,
                auth.revocation_options,
            ).model_dump(mode="json", exclude_none=True)
            for path in (
                "/.well-known/oauth-authorization-server",
                "/.well-known/oauth-authorization-server" + prefix,
                prefix + "/.well-known/oauth-authorization-server",
            ):
                self.metadata[path] = authorization
            protected = {
                "resource": resource,
                "authorization_servers": [base],
                "scopes_supported": auth.required_scopes or [],
                "bearer_methods_supported": ["header"],
            }
            for path in (
                "/.well-known/oauth-protected-resource",
                "/.well-known/oauth-protected-resource" + prefix + "/mcp",
            ):
                self.metadata[path] = protected

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
                return
            raise RuntimeError("Unsupported cold ingress transport")
        try:
            raw_path = scope.get("raw_path", scope["path"].encode("ascii")).decode("ascii")
        except (UnicodeError, AttributeError):
            raw_path = ""
        method = scope["method"]
        headers = {"Cache-Control": "no-store"}
        if raw_path in self.metadata and method in {"GET", "HEAD"}:
            response = JSONResponse(self.metadata[raw_path], headers=headers)
        else:
            stripped = self.config.strip_path(raw_path, "mcp")
            if stripped is None:
                response = JSONResponse(
                    {"error": "cold_ingress_generation_required"},
                    status_code=409,
                    headers={**headers, "Connection": "close"},
                )
            elif urlsplit(stripped).path == STATUS_PATH:
                response = (
                    JSONResponse(self.config.status("mcp"), headers=headers)
                    if method == "GET"
                    else Response(status_code=405, headers=headers)
                )
            else:
                # Keep SDK routes internal, while its issuer/resource metadata
                # retain the complete generation URL. Never rewrite old paths.
                forwarded = {
                    **scope,
                    "path": stripped,
                    "raw_path": stripped.encode("ascii"),
                    "root_path": "",
                }
                await self.app(forwarded, receive, send)
                return
        if method == "HEAD":
            response.body = b""
        await response(scope, receive, send)


class ColdAwareFastMCP(FastMCP):
    def __init__(self, *, cold_http_ingress=None, **kwargs):
        self.cold_http_ingress = cold_http_ingress
        super().__init__(**kwargs)

    def streamable_http_app(self):
        app = super().streamable_http_app()
        if self.cold_http_ingress is not None:
            app.add_middleware(
                ColdMcpIngress, config=self.cold_http_ingress, auth=self.settings.auth
            )
        return app
