"""Client for the ps5-hass payload API (/api/v1). No Home Assistant imports."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import aiohttp

GUARD_HEADERS = {"Content-Type": "application/json", "X-PS5-Hass": "1"}
TITLE_ID = re.compile(r"^[A-Z]{4}[0-9]{5}$")
ICON_MAX = 1024 * 1024
# Only real images may reach Home Assistant's image proxy (no HTML/SVG/script).
ICON_TYPES = {
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/jpeg": b"\xff\xd8\xff",
}


class Ps5HassError(Exception):
    """Base error."""


class CannotConnect(Ps5HassError):
    """The payload did not answer (console off, rest mode, payload not loaded)."""


class AuthError(Ps5HassError):
    """The token is missing or no longer valid (401 unauthorized)."""


class ApiError(Ps5HassError):
    """Any other error answer of the payload."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self.code: str = body.get("error", "unknown")
        self.message: str = body.get("message", "")
        self.attempts_left: int | None = body.get("attempts_left")
        self.retry_after: int | None = body.get("retry_after")
        self.rc_hex: str | None = body.get("rc_hex")
        super().__init__(f"{status} {self.code}: {self.message}")


@dataclass
class AppsResult:
    """The app list and its ETag."""

    etag: str
    apps: list[dict[str, Any]]


class Ps5HassClient:
    """Talks to one console."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self._session = session
        self._base = f"http://{host}:{port}/api/v1"
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self.token = token

    async def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        max_bytes: int | None = None,
    ) -> tuple[int, Any, dict[str, str], bytes]:
        hdrs: dict[str, str] = dict(headers or {})
        if self.token:
            hdrs["Authorization"] = f"Bearer {self.token}"
        if method == "POST":
            hdrs.update(GUARD_HEADERS)
        try:
            async with self._session.request(
                method,
                self._base + path,
                json=(body or {}) if method == "POST" else None,
                headers=hdrs,
                timeout=self._timeout,
            ) as resp:
                if max_bytes is None:
                    raw = await resp.read()
                else:  # read at most max_bytes + 1, so callers can tell "too big"
                    buf = bytearray()
                    async for chunk in resp.content.iter_chunked(65536):
                        buf += chunk
                        if len(buf) > max_bytes:
                            break
                    raw = bytes(buf)
                ctype = resp.headers.get("Content-Type", "")
                parsed: Any = None
                if ctype.startswith("application/json") and raw:
                    try:
                        parsed = json.loads(raw)
                    except ValueError as err:
                        raise CannotConnect(f"invalid JSON from {path}") from err
                return resp.status, parsed, dict(resp.headers), raw
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise CannotConnect(str(err) or type(err).__name__) from err

    @staticmethod
    def _check(status: int, parsed: Any) -> dict[str, Any]:
        if status == 200 and isinstance(parsed, dict):
            return parsed
        body = parsed if isinstance(parsed, dict) else {"error": "unknown"}
        if status == 401 and body.get("error") == "unauthorized":
            raise AuthError(body.get("message", "unauthorized"))
        raise ApiError(status, body)

    async def _json(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        status, parsed, _, _ = await self._request(method, path, body)
        return self._check(status, parsed)

    async def info(self) -> dict[str, Any]:
        return await self._json("GET", "/info")

    async def pair_start(self) -> int:
        return int((await self._json("POST", "/pair/start"))["expires_in"])

    async def pair_finish(self, code: str, client_id: str, label: str) -> str:
        res = await self._json(
            "POST", "/pair/finish", {"code": code, "client_id": client_id, "label": label}
        )
        self.token = res["token"]
        return self.token

    async def revoke(self) -> None:
        await self._json("POST", "/pair/revoke")

    async def state(self) -> dict[str, Any]:
        return await self._json("GET", "/state")

    async def apps(self, etag: str | None = None) -> AppsResult | None:
        headers = {"If-None-Match": f'"{etag}"'} if etag else None
        status, parsed, _, _ = await self._request("GET", "/apps", headers=headers)
        if status == 304:
            return None
        res = self._check(status, parsed)
        return AppsResult(res["etag"], res["apps"])

    @staticmethod
    def _title(title_id: str) -> str:
        if not TITLE_ID.match(title_id):
            raise ApiError(400, {"error": "bad_title_id", "message": "invalid title id"})
        return title_id

    async def icon(self, title_id: str) -> tuple[bytes, str]:
        """PNG or JPEG cover, at most 1 MB; anything else raises ApiError invalid_icon."""
        status, parsed, headers, raw = await self._request(
            "GET", f"/apps/{self._title(title_id)}/icon", max_bytes=ICON_MAX
        )
        if status != 200:
            self._check(status, parsed)
        ctype = headers.get("Content-Type", "").split(";")[0].strip().lower()
        magic = ICON_TYPES.get(ctype)
        if magic is None or not raw.startswith(magic) or len(raw) > ICON_MAX:
            raise ApiError(502, {"error": "invalid_icon", "message": "not a PNG/JPEG icon"})
        return raw, ctype

    async def launch(self, title_id: str, replace_running: bool = False) -> dict[str, Any]:
        self._title(title_id)
        return await self._json(
            "POST", f"/apps/{title_id}/launch", {"replace_running": replace_running}
        )

    async def close(self, title_id: str | None = None) -> None:
        await self._json(
            "POST", "/apps/running/close", {"title_id": title_id} if title_id else {}
        )

    async def standby(self) -> None:
        await self._json("POST", "/power/standby")

    # --- v0.2 -------------------------------------------------------------

    async def notify(self, message: str) -> None:
        await self._json("POST", "/notify", {"message": message})

    async def power_off(self) -> None:
        await self._json("POST", "/power/off")

    async def set_fan(self, threshold_c: int) -> dict[str, Any]:
        """45..85, or 0 = back to the console's own value. Returns the fan state."""
        return (await self._json("POST", "/fan", {"threshold_c": threshold_c}))["fan"]

    # --- v0.3 -------------------------------------------------------------

    async def grant_start(self, permissions: list[str]) -> int:
        """Ask the console to show a grant code on the TV; returns its lifetime in s."""
        res = await self._json("POST", "/grant/start", {"permissions": permissions})
        return int(res["expires_in"])

    async def grant_finish(self, code: str) -> dict[str, dict[str, bool]]:
        """Redeem the code from the TV; returns the console's permissions."""
        return (await self._json("POST", "/grant/finish", {"code": code}))["permissions"]

    async def grant_revoke(self, permissions: list[str]) -> dict[str, dict[str, bool]]:
        """Take permissions away (no code needed); returns the console's permissions."""
        res = await self._json("POST", "/grant/revoke", {"permissions": permissions})
        return res["permissions"]
