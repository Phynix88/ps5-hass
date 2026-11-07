"""A stateful fake of the ps5-hass payload API (/api/v1).

Every JSON answer is validated against tests/fixtures/schema.json, a copy of
api/schema.json from Phynix88/ps5-hass-payload (v0.4 branch).
A violation is answered with 500 and recorded in `schema_errors`; the `fake`
fixture fails the test when any are left.
"""

from __future__ import annotations

import json
import pathlib
import re
import secrets
import struct
import time
import zlib
from typing import Any

import jsonschema
from aiohttp import web

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
SCHEMA = json.loads((FIXTURES / "schema.json").read_text())
APPS = json.loads((FIXTURES / "apps.json").read_text())
CODE = "123456"
DEVICE_ID = "00112233445566778899aabbccddeeff"
V04_CAPABILITIES = ("focus", "power_state", "suspend", "keep_awake", "soc_power", "playtime",
                    "payload_control")
# Routes that call a console function; the payload refuses them with 409 not_on outside "on".
NEEDS_ON = {
    ("POST", "/apps/running/close"), ("POST", "/apps/running/suspend"), ("POST", "/power/standby"),
    ("POST", "/power/off"), ("POST", "/notify"), ("POST", "/fan"), ("POST", "/payloads/start"),
    ("POST", "/payloads/stop"), ("POST", "/pair/start"), ("POST", "/pair/finish"),
}


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
            # v0.4; payload_control is left out on purpose: it follows the first /payloads call
            "focus": True, "power_state": True, "suspend": True, "keep_awake": True,
            "soc_power": True, "playtime": True,
        }
        self.has_user = True
        self.version = "0.1.0"
        self.standby_calls = 0
        self.fail: dict[str, tuple[int, dict[str, Any]]] = {}  # path -> once
        self.calls: list[tuple[str, str, dict[str, str], Any]] = []
        self.state_override: dict[str, Any] = {}
        self.schema_errors: list[str] = []
        self.expect_schema_errors = False
        self.down = False  # abort every connection, like a payload that is not loaded
        # v0.2
        self.power_off = False      # the console can power off (v0.3: and it is allowed)
        self.fan_control = False    # the console can set the fan (v0.3: and it is allowed)
        self.power_off_calls = 0
        self.payloads = ["ftpsrv.elf", "kstuff.elf", "shadowmountplus.elf", "websrv.elf"]
        self.fan = {"target_c": None, "current_c": 91, "stock_c": 91}
        self.notifications: list[str] = []
        self.legacy_v01 = False  # answer /state like a v0.1 payload (no payloads/fan)
        # v0.3
        self.legacy_v02 = False  # /info without permissions, no /grant/* (payload v0.1/v0.2)
        # v0.4 has no permissions: what the console can do is allowed. A v0.3 payload
        # still answers 403 *_disabled for what is not allowed (the flags above).
        self.legacy_v03 = False
        # v0.4
        self.power_state = "on"  # on | going_to_rest | rest | shutting_down | unknown
        self.keep_awake = False
        self.soc_power: dict[str, Any] | None = {
            "total_mw": 48000,
            "rails": [{"mw": 30000, "mv": 1000, "ma": 30000}, {"mw": 18000, "mv": 1200, "ma": 15000}],
        }
        self.drives: list[dict[str, Any]] = []
        self.playtime_titles: list[dict[str, Any]] = []
        self.playtime_sessions: list[dict[str, Any]] = []
        self.playtime_current: dict[str, Any] | None = None
        # What the Payload Manager lists (`payloads` above are the running process names).
        self.payload_list: list[dict[str, Any]] = [
            {"slot": "repo/ftpsrv@e8cce5d1", "name": "ftpsrv", "version": "1.2",
             "running": False, "protected": False},
            {"slot": "repo/websrv@0a1b2c3d", "name": "websrv", "version": "",
             "running": False, "protected": False},
            {"slot": "repo/kstuff@11223344", "name": "kstuff", "version": "1.6",
             "running": True, "protected": True},
        ]
        self.payload_manager = True  # False: not reachable
        self.pldmgr_actions = 0  # starts and stops that reached the Payload Manager
        self.minimised = False  # a game is in memory without running: the payload refuses (v0.4.2)
        self.payload_lag = 0  # lists after a start or stop that still show the old state
        self._lag: dict[str, tuple[bool, int]] = {}  # slot -> (old running, lists left)
        self._pldmgr_ok = False  # the last call to the Payload Manager worked
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
        self._runner = web.AppRunner(app, access_log=None)
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
        caps = {**self.capabilities, "power_off": self.power_off, "fan_control": self.fan_control}
        if self.legacy_v03 or self.legacy_v02:
            for key in V04_CAPABILITIES:
                caps.pop(key, None)
        else:
            caps.setdefault("payload_control", self._pldmgr_ok)
        return {
            "result": "ok", "name": "ps5-hass", "version": self.version,
            "commit": "abc1234", "api": 1, "device_id": self.device_id,
            "model": "CFI-TEST", "firmware": "13.60",
            "paired": bool(self.tokens), "power_actions": self.power_actions,
            "config": "ok",
            "capabilities": caps,
            "last_error": None,
        } | ({} if self.legacy_v02 else {"permissions": self.permissions()})

    PERMS = ("power_actions", "power_off", "fan_control")

    def permissions(self) -> dict[str, dict[str, bool]]:
        """Every name allowed = available (payload v0.4)."""
        return {
            name: {"allowed": bool(getattr(self, name)), "available": bool(getattr(self, name))}
            for name in self.PERMS
        }

    def _disabled(self, code: str) -> web.Response:
        """A v0.3 payload says 403 for what is not allowed, v0.4 that it cannot."""
        if self.legacy_v03:  # a v0.3 code the v0.4 schema no longer knows: not checked
            return web.json_response(
                {"result": "error", "error": code, "message": code}, status=403)
        return self._error(409, "not_available")

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
        if self.down:
            if request.transport is not None:
                request.transport.abort()
            raise ConnectionResetError("fake payload is down")
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
            ("GET", "/info"): lambda: (
    web.json_response(self.info()) if self.legacy_v02 or self.legacy_v03
                else self._json("info", self.info())
            ),
            ("POST", "/pair/start"): self._pair_start,
            ("POST", "/pair/finish"): lambda: self._pair_finish(body),
        }
        route = (request.method, path)
        if route in NEEDS_ON and not self._is_on():
            return self._error(409, "not_on")
        if route in open_routes:
            return open_routes[route]()

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
        if (request.method, path) == ("POST", "/notify"):
            return self._notify(body)
        if (request.method, path) == ("POST", "/power/off"):
            if not self.power_off:
                return self._disabled("power_off_disabled")
            self.power_off_calls += 1
            return self._json("ok", {"result": "ok"})
        if (request.method, path) == ("POST", "/fan"):
            return self._fan(body)
        if path.startswith("/grant/") and not self.legacy_v02 and request.method == "POST":
            return self._grant(path)
        if (request.method, path) == ("POST", "/power/standby"):
            if not self.power_actions:
                return self._disabled("power_actions_disabled")
            self.standby_calls += 1
            return self._json("ok", {"result": "ok"})
        if not self.legacy_v02 and not self.legacy_v03:
            v04 = {
                ("POST", "/apps/running/suspend"): self._suspend,
                ("POST", "/keep_awake"): lambda: self._keep_awake(body),
                ("GET", "/playtime"): lambda: self._playtime(request),
                ("GET", "/payloads"): self._payloads,
                ("POST", "/payloads/start"): lambda: self._payload_action(body, start=True),
                ("POST", "/payloads/stop"): lambda: self._payload_action(body, start=False),
            }
            if route in v04:
                return v04[route]()
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

    def _grant(self, path: str) -> web.Response:
        """Payload v0.4: nothing to grant, everything is allowed after pairing."""
        if path == "/grant/start":
            return self._error(409, "already_allowed")
        if path == "/grant/finish":
            return self._error(409, "no_code")
        if path == "/grant/revoke":
            return self._json("grant_revoke", {"result": "ok", "permissions": self.permissions()})
        return self._error(404, "not_found")

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
            "payloads": list(self.payloads), "fan": dict(self.fan),
        }
        if not self._is_on():  # the payload reads nothing from the console outside "on"
            body.update(
                in_app=False, app=None, temps={"cpu": None, "soc": None}, fan_raw=None,
                storage=dict.fromkeys(body["storage"]), payloads=[],
            )
        body.update(power_state=self.power_state, keep_awake=self.keep_awake,
                    soc_power=self.soc_power if self._is_on() else None,
                    drives=list(self.drives) if self._is_on() else [])
        body.update(self.state_override)
        if self.legacy_v03 or self.legacy_v02:
            for key in ("power_state", "keep_awake", "soc_power", "drives"):
                body.pop(key)
            return web.json_response(body)  # v0.3 schema; not checked here
        if self.legacy_v01:
            body.pop("payloads")
            body.pop("fan")
            return web.json_response(body)  # v0.1 schema; not checked here
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
        if not self._is_on():
            return self._error(409, "not_on")
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

    def _notify(self, body: dict[str, Any]) -> web.Response:
        msg = body.get("message")
        if not isinstance(msg, str) or not msg.strip():
            return self._error(400, "bad_request")
        if len(self.notifications) >= 5:
            return self._error(429, "rate_limited", retry_after=12)
        self.notifications.append(msg)
        return self._json("ok", {"result": "ok"})

    def _fan(self, body: dict[str, Any]) -> web.Response:
        if not self.fan_control:
            return self._disabled("fan_control_disabled")
        value = body.get("threshold_c")
        if not isinstance(value, int) or (value != 0 and not 45 <= value <= 85):
            return self._error(400, "bad_request")
        stock = self.fan["stock_c"]
        if value and stock and value > stock:
            return self._error(409, "fan_above_stock")
        self.fan = {"target_c": value or None, "current_c": value or stock, "stock_c": stock}
        return self._json("fan", {"result": "ok", "fan": dict(self.fan)})

    def _close(self, body: dict[str, Any]) -> web.Response:
        if not self.running:
            return self._error(409, "no_app")
        want = body.get("title_id")
        if want is not None and want != self.running[0]:
            return self._error(409, "title_mismatch")
        self.running = None
        return self._json("ok", {"result": "ok"})

    # --- v0.4 handlers ---------------------------------------------------

    def _is_on(self) -> bool:
        """A console that cannot report its power state counts as on, like the payload."""
        return self.power_state in ("on", "unknown")

    def _suspend(self) -> web.Response:
        if not self._is_on():
            return self._error(409, "not_on")
        if not self.capabilities.get("suspend"):
            return self._error(409, "not_available")
        if not self.running:
            return self._error(409, "not_running")
        self.running = None
        return self._json("ok", {"result": "ok"})

    def _keep_awake(self, body: dict[str, Any]) -> web.Response:
        on = body.get("on")
        if not isinstance(on, bool):
            return self._error(400, "bad_request")
        if on and not self._is_on():
            return self._error(409, "not_on")
        if not self.capabilities.get("keep_awake"):
            return self._error(409, "not_available")
        self.keep_awake = on
        return self._json("keep_awake", {"result": "ok", "keep_awake": on})

    def _playtime(self, request: web.Request) -> web.Response:
        raw = request.query.get("since")
        if raw is None:
            since = int(time.time()) - 8 * 86400
        elif re.fullmatch(r"[0-9]{1,11}", raw):
            since = int(raw)
        else:
            return self._error(400, "bad_request")
        sessions = [s for s in self.playtime_sessions if s["start"] + s["duration_s"] >= since]
        return self._json("playtime", {
            "result": "ok", "foreground": bool(self.capabilities.get("focus")),
            "titles": list(self.playtime_titles), "sessions": sessions,
            "current": self.playtime_current,
        })

    @staticmethod
    def _protected_name(*names: Any) -> bool:
        """The payload's own list: kstuff, etaHEN, the loader, the Payload Manager, itself, ps5debug."""
        needles = ("kstuff", "etahen", "elfldr", "pldmgr", "ps5-hass", "ps5debug")
        return any(isinstance(n, str) and any(x in n.lower() for x in needles) for n in names)

    def _payloads(self) -> web.Response:
        if not self.payload_manager:
            self._pldmgr_ok = False
        elif self._is_on():
            self._pldmgr_ok = True
        if not self.payload_manager or not self._is_on():  # outside "on" it is not even asked
            return self._json("payloads", {"result": "ok", "available": False, "payloads": []})
        out = []
        for p in self.payload_list:
            shown = {**p, "protected": p["protected"] or self._protected_name(p["slot"], p["name"])}
            old, left = self._lag.get(p["slot"], (p["running"], 0))
            if left > 0:  # the Payload Manager does not list the new process yet
                shown["running"] = old
                self._lag[p["slot"]] = (old, left - 1)
            out.append(shown)
        return self._json("payloads", {"result": "ok", "available": True, "payloads": out})

    def _payload_action(self, body: dict[str, Any], *, start: bool) -> web.Response:
        slot = body.get("slot")
        if not isinstance(slot, str) or not slot:
            return self._error(400, "bad_request")
        if self._protected_name(slot):  # protected, then a running app: before any contact
            return self._error(403, "protected")
        if self.running or self.minimised:  # a game or app is in memory: nothing is started or stopped
            return self._error(409, "app_running")
        if not self.payload_manager:
            self._pldmgr_ok = False
            return self._error(503, "payload_manager_unavailable")
        entry = next((p for p in self.payload_list if p["slot"] == slot), None)
        if entry is None:
            return self._error(404, "unknown_payload")
        if entry["protected"] or self._protected_name(entry["name"]):  # the Payload Manager is not asked
            return self._error(403, "protected")
        if entry["running"] == start:
            return self._error(409, "already_running" if start else "not_running")
        self.pldmgr_actions += 1
        if self.payload_lag:
            self._lag[slot] = (entry["running"], self.payload_lag)
        entry["running"] = start
        return self._json("ok", {"result": "ok"})
