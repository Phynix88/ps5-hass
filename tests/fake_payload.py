"""A stateful fake of the ps5-hass payload API (/api/v1).

Every JSON answer is validated against tests/fixtures/schema.json, a copy of
api/schema.json from Phynix88/ps5-hass-payload (copied at commit bcb2d2b).
A violation is answered with 500 and recorded in `schema_errors`; the `fake`
fixture fails the test when any are left.
"""

from __future__ import annotations

import json
import pathlib
import secrets
import struct
import zlib
from typing import Any

import jsonschema
from aiohttp import web

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SCHEMA = json.loads((FIXTURES / "schema.json").read_text())
APPS = json.loads((FIXTURES / "apps.json").read_text())
CODE = "123456"
DEVICE_ID = "00112233445566778899aabbccddeeff"


def tiny_png(seed: int = 0) -> bytes:
    """A valid 1x1 PNG whose pixel depends on seed."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = bytes([0, seed & 0xFF, 0, 0])
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class FakePayload:
    """Fake console with the payload running."""

    def __init__(self) -> None:
        self.device_id = DEVICE_ID
        self.code = CODE
        self.tokens: dict[str, str] = {}  # client_id -> token
        self.apps: list[dict[str, Any]] = [dict(a) for a in APPS]
        self.etag = "0123456789abcdef"
        self.running: tuple[str, int] | None = None  # (title_id, app_id)
        self.next_app_id = 100
        self.power_actions = False
        self.capabilities = {
            "temps": True, "fan": True, "notify": True, "random": True,
            "launch": True, "standby": True, "mdns": True,
        }
        self.has_user = True
        self.standby_calls = 0
        self.fail: dict[str, tuple[int, dict[str, Any]]] = {}  # path -> once
        self.calls: list[tuple[str, str, dict[str, str], Any]] = []
        self.state_override: dict[str, Any] = {}
        self.schema_errors: list[str] = []
        self.expect_schema_errors = False
        self._pairing = False
        self._attempts = 0
        self._runner: web.AppRunner | None = None
        self.port = 0

    # --- lifecycle -------------------------------------------------------

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def start(self, port: int = 0) -> int:
        app = web.Application(middlewares=[self._guard])
        app.router.add_route("*", "/api/v1/{tail:.*}", self._dispatch)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", port or self.port)
        await site.start()
        self.port = site._server.sockets[0].getsockname()[1]  # noqa: SLF001
        return self.port

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None

    def add_token(self, client_id: str = "test-client") -> str:
        token = secrets.token_hex(32)
        self.tokens[client_id] = token
        return token

    def info(self) -> dict[str, Any]:
        return {
            "result": "ok", "name": "ps5-hass", "version": "0.1.0",
            "commit": "abc1234", "api": 1, "device_id": self.device_id,
            "model": "CFI-TEST", "firmware": "13.60",
            "paired": bool(self.tokens), "power_actions": self.power_actions,
            "config": "ok", "capabilities": dict(self.capabilities),
            "last_error": None,
        }

    def app(self, title_id: str) -> dict[str, Any] | None:
        return next((a for a in self.apps if a["title_id"] == title_id), None)

    # --- plumbing --------------------------------------------------------

    def _json(self, defn: str, body: dict[str, Any], status: int = 200,
              headers: dict[str, str] | None = None) -> web.Response:
        schema = dict(SCHEMA)
        schema["$ref"] = f"#/$defs/{defn}"
        try:
            jsonschema.validate(body, schema)
        except jsonschema.ValidationError as err:
            self.schema_errors.append(f"{defn}: {err.message}")
            return web.json_response({"fake": "schema violation"}, status=500)
        return web.json_response(body, status=status, headers=headers)

    def _error(self, status: int, code: str, **extra: Any) -> web.Response:
        body = {"result": "error", "error": code, "message": code, **extra}
        return self._json("error", body, status)

    @web.middleware
    async def _guard(self, request: web.Request, handler: Any) -> web.StreamResponse:
        if "Origin" in request.headers:
            return self._error(403, "origin_forbidden")
        if request.method == "POST":
            if (
                not request.headers.get("Content-Type", "").startswith("application/json")
                or request.headers.get("X-PS5-Hass") != "1"
            ):
                return self._error(403, "browser_guard")
        return await handler(request)

    def _client(self, request: web.Request) -> str | None:
        auth = request.headers.get("Authorization", "")
        for client_id, token in self.tokens.items():
            if auth == f"Bearer {token}":
                return client_id
        return None

    async def _dispatch(self, request: web.Request) -> web.StreamResponse:
        path = "/" + request.match_info["tail"]
        body: Any = None
        if request.method == "POST":
            try:
                body = await request.json()
            except (ValueError, UnicodeDecodeError):
                body = None
            if not isinstance(body, dict):
                self.calls.append((request.method, path, dict(request.headers), body))
                return self._error(400, "bad_request")
        self.calls.append((request.method, path, dict(request.headers), body))
        if path in self.fail:
            status, err = self.fail.pop(path)
            if status == 304:
                return web.Response(status=304)
            return self._json("error", {"result": "error", "message": "forced", **err}, status)

        open_routes = {
            ("GET", "/info"): lambda: self._json("info", self.info()),
            ("POST", "/pair/start"): self._pair_start,
            ("POST", "/pair/finish"): lambda: self._pair_finish(body),
        }
        if (request.method, path) in open_routes:
            return open_routes[(request.method, path)]()

        client = self._client(request)
        if client is None:
            return self._error(401, "unauthorized")
        if (request.method, path) == ("POST", "/pair/revoke"):
            del self.tokens[client]
            return self._json("ok", {"result": "ok"})
        if (request.method, path) == ("GET", "/state"):
            return self._state()
        if (request.method, path) == ("GET", "/apps"):
            return self._apps(request)
        if (request.method, path) == ("POST", "/apps/running/close"):
            return self._close(body)
        if (request.method, path) == ("POST", "/power/standby"):
            if not self.power_actions:
                return self._error(403, "power_actions_disabled")
            self.standby_calls += 1
            return self._json("ok", {"result": "ok"})
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "apps" and parts[2] in ("icon", "launch"):
            if parts[2] == "icon" and request.method == "GET":
                return self._icon(parts[1], request)
            if parts[2] == "launch" and request.method == "POST":
                return self._launch(parts[1], body)
        return self._error(404, "not_found")

    # --- handlers --------------------------------------------------------

    def _pair_start(self) -> web.Response:
        self._pairing = True
        self._attempts = 0
        return self._json("pair_start", {"result": "ok", "expires_in": 120})

    def _pair_finish(self, body: dict[str, Any]) -> web.Response:
        if not all(isinstance(body.get(k), str) for k in ("code", "client_id", "label")):
            return self._error(400, "bad_request")
        if not self._pairing:
            return self._error(409, "no_code")
        if self._attempts >= 5:
            return self._error(409, "code_exhausted")
        if body["code"] != self.code:
            self._attempts += 1
            return self._error(401, "wrong_code", attempts_left=5 - self._attempts)
        self._pairing = False
        token = secrets.token_hex(32)
        self.tokens[body["client_id"]] = token
        return self._json("pair_finish", {"result": "ok", "token": token})

    def _state(self) -> web.Response:
        app = None
        if self.running:
            meta = self.app(self.running[0])
            app = {
                "title_id": self.running[0],
                "name": meta["name"] if meta else self.running[0],
                "app_id": self.running[1],
            }
        body = {
            "result": "ok", "in_app": app is not None, "app": app,
            "temps": {"cpu": 41, "soc": 38}, "fan_raw": 131,
            "storage": {"data_free_mb": 76731, "data_total_mb": 838000,
                        "user_free_mb": 76731, "user_total_mb": 838000},
            "uptime_s": 284, "payload_uptime_s": 120, "apps_etag": self.etag,
        }
        body.update(self.state_override)
        return self._json("state", body)

    def _apps(self, request: web.Request) -> web.Response:
        tag = f'"{self.etag}"'
        if request.headers.get("If-None-Match") == tag:
            return web.Response(status=304, headers={"ETag": tag})
        body = {"result": "ok", "etag": self.etag, "apps": self.apps}
        return self._json("apps", body, headers={"ETag": tag})

    def _icon(self, title_id: str, request: web.Request) -> web.Response:
        app = self.app(title_id)
        if not app:
            return self._error(404, "not_installed")
        if not app["has_icon"]:
            return self._error(404, "not_found")
        index = self.apps.index(app)
        return web.Response(body=tiny_png(index), content_type="image/png",
                            headers={"ETag": f'"{index:x}-1"'})

    def _launch(self, title_id: str, body: dict[str, Any]) -> web.Response:
        replace = body.get("replace_running", False)
        if not isinstance(replace, bool):
            return self._error(400, "bad_request")
        if not self.app(title_id):
            return self._error(404, "not_installed")
        if self.running and self.running[0] == title_id:
            return self._json("launch", {"result": "ok", "already_running": True})
        if self.running and not replace:
            return self._error(409, "other_app_running")
        if not self.has_user:
            return self._error(409, "no_user")
        self.running = (title_id, self.next_app_id)
        self.next_app_id += 1
        return self._json("launch", {"result": "ok", "already_running": False,
                                     "app_id": self.running[1]})

    def _close(self, body: dict[str, Any]) -> web.Response:
        if not self.running:
            return self._error(409, "no_app")
        want = body.get("title_id")
        if want is not None and want != self.running[0]:
            return self._error(409, "title_mismatch")
        self.running = None
        return self._json("ok", {"result": "ok"})
