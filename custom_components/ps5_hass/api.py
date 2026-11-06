"""Client for the ps5-hass payload API (/api/v1). No Home Assistant imports."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import aiohttp

GUARD_HEADERS = {"Content-Type": "application/json", "X-PS5-Hass": "1"}


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
                raw = await resp.read()
                ctype = resp.headers.get("Content-Type", "")
                parsed: Any = None
                if ctype.startswith("application/json") and raw:
                    try:
                        parsed = await resp.json(content_type=None)
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

    async def icon(self, title_id: str) -> tuple[bytes, str]:
        status, parsed, headers, raw = await self._request("GET", f"/apps/{title_id}/icon")
        if status != 200:
            self._check(status, parsed)
        return raw, headers.get("Content-Type", "image/png")

    async def launch(self, title_id: str, replace_running: bool = False) -> dict[str, Any]:
        return await self._json(
            "POST", f"/apps/{title_id}/launch", {"replace_running": replace_running}
        )

    async def close(self, title_id: str | None = None) -> None:
        await self._json(
            "POST", "/apps/running/close", {"title_id": title_id} if title_id else {}
        )

    async def standby(self) -> None:
        await self._json("POST", "/power/standby")
