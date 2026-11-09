"""The MCP server over streamable HTTP, for a client on another machine.

    fsr-agentic-toolkit mcp --http [--host 127.0.0.1] [--port 8765] [--token-file PATH]
                            [--certfile cert.pem --keyfile key.pem]

Same server as stdio (:func:`fsr_agentic_toolkit.verbs.mcp.build_server`, same tools,
same one-call-at-a-time lock), served at ``/mcp``. Every request needs
``Authorization: Bearer <token>``; the token comes from ``$FSR_MCP_TOKEN`` or
``--token-file``. With neither, a random token is generated at start and saved owner-only (never printed): there is no tokenless mode.
On a specific host, Host headers are also checked (DNS-rebinding protection).
With ``--certfile``/``--keyfile`` it serves HTTPS and prints the certificate's
SHA-256 fingerprint (for ``fortiagent mcp add --trust-fingerprint``).
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import ipaddress
import os
import ssl
import sys
from pathlib import Path
from typing import Any

TOKEN_ENV = "FSR_MCP_TOKEN"
MCP_PATH = "/mcp"


def is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def load_token(token_file: str | None) -> str | None:
    """The bearer token: ``--token-file`` first, then ``$FSR_MCP_TOKEN``. Blank means none."""
    if token_file:
        tok = Path(token_file).read_text(encoding="utf-8").strip()
    else:
        tok = os.environ.get(TOKEN_ENV, "").strip()
    return tok or None


def cert_fingerprint(certfile: str) -> str:
    """SHA-256 of the leaf certificate's DER, as colon-separated upper hex."""
    pem = Path(certfile).read_text(encoding="utf-8")
    der = ssl.PEM_cert_to_DER_cert(pem)
    digest = hashlib.sha256(der).hexdigest().upper()
    return ":".join(digest[i:i + 2] for i in range(0, len(digest), 2))


class BearerAuth:
    """ASGI middleware: 401 unless ``Authorization: Bearer <token>`` matches (constant-time)."""

    def __init__(self, app: Any, token: str | None) -> None:
        self.app = app
        if not token:
            raise ValueError("a bearer token is required")
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        got = b""
        for name, value in scope.get("headers") or []:
            if name == b"authorization":
                got = value
                break
        if not hmac.compare_digest(got, self._expected):
            body = b'{"error": "unauthorized: send Authorization: Bearer <token>"}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"www-authenticate", b"Bearer"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def build_app(target: Any, token: str, host: str = "127.0.0.1", port: int | None = None) -> Any:
    """Starlette app serving ``build_server(target)`` at ``/mcp``, behind :class:`BearerAuth`."""
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from mcp.server.transport_security import TransportSecuritySettings
    from starlette.applications import Starlette
    from starlette.routing import Route

    from .mcp import build_server

    # DNS-rebinding protection: only accept the Host names this server is reachable as. On a wildcard bind
    # the public name is unknown, so the Host check is off there and the bearer token is the gate.
    wildcard = host in ("0.0.0.0", "::", "")
    if is_loopback(host):
        hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    else:
        hosts = [f"{host}:*"]
    security = TransportSecuritySettings(enable_dns_rebinding_protection=not wildcard, allowed_hosts=hosts,
                                         allowed_origins=[f"http://{h}" for h in hosts] + [f"https://{h}" for h in hosts])
    manager = StreamableHTTPSessionManager(app=build_server(target), security_settings=security)

    class Handle:  # a class instance, so Starlette's Route passes it the raw ASGI call
        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            await manager.handle_request(scope, receive, send)

    @contextlib.asynccontextmanager
    async def lifespan(_app: Any):  # type: ignore[no-untyped-def]
        async with manager.run():
            yield

    # Route, not Mount: a Mount answers POST /mcp with a 307 to /mcp/, and behind a reverse proxy that redirect
    # names this server's own loopback address.
    app = Starlette(routes=[Route(MCP_PATH, endpoint=Handle(), methods=["GET", "POST", "DELETE"])], lifespan=lifespan)
    return BearerAuth(app, token)


def serve_http(host: str, port: int, *, token_file: str | None = None, certfile: str | None = None,
               keyfile: str | None = None, target: Any = None) -> None:
    """Run the HTTP server until interrupted. Refuses a non-loopback host without a token."""
    import uvicorn
    from pyfsr.instances import InstanceRegistry

    token = load_token(token_file)
    generated = token is None
    if token is None:
        import secrets

        token = secrets.token_urlsafe(32)
    if bool(certfile) != bool(keyfile):
        raise SystemExit("--certfile and --keyfile go together")
    if target is None:
        target = InstanceRegistry.load()
    scheme = "https" if certfile else "http"
    print(f"fsr-agentic-toolkit MCP over HTTP: {scheme}://{host}:{port}{MCP_PATH}"
          " (bearer token required)", file=sys.stderr)
    if generated:
        # Never log the secret (a service's stderr lands in journald): write it to an owner-only file instead.
        tok_path = Path(os.environ.get("FSR_MCP_TOKEN_OUT", Path.home() / ".config/fsr-agentic-toolkit/mcp-token"))
        tok_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(tok_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(token + "\n")
        os.chmod(tok_path, 0o600)
        print(f"no ${TOKEN_ENV} or --token-file: generated a token for this run, saved (mode 600) to {tok_path}",
              file=sys.stderr)
    if certfile:
        print(f"certificate SHA-256 fingerprint: {cert_fingerprint(certfile)}", file=sys.stderr)
    uvicorn.run(build_app(target, token, host, port), host=host, port=port, ssl_certfile=certfile, ssl_keyfile=keyfile,
                log_level="warning")
