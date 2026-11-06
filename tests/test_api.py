"""Ps5HassClient against the fake payload."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import aiohttp
import pytest

from custom_components.ps5_hass.api import (
    ApiError,
    AppsResult,
    AuthError,
    CannotConnect,
    Ps5HassClient,
)

from .fake_payload import FakePayload

CLIENT = "0b7e2f1c-1111-4222-8333-944445555666"


@pytest.fixture
async def session() -> AsyncGenerator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as s:
        yield s


def client(fake: FakePayload, session: aiohttp.ClientSession, token: str | None = None) -> Ps5HassClient:
    return Ps5HassClient(session, "127.0.0.1", fake.port, token, timeout=10)


async def test_info(fake: FakePayload, session) -> None:
    info = await client(fake, session).info()
    assert info["device_id"] == fake.device_id


async def test_pair_flow_sets_token(fake: FakePayload, session) -> None:
    c = client(fake, session)
    assert await c.pair_start() == 120
    token = await c.pair_finish(fake.code, CLIENT, "Home Assistant")
    assert c.token == token == fake.tokens[CLIENT]
    assert (await c.state())["result"] == "ok"


async def test_wrong_code_attempts_left(fake: FakePayload, session) -> None:
    c = client(fake, session)
    await c.pair_start()
    with pytest.raises(ApiError) as err:
        await c.pair_finish("000000", CLIENT, "x")
    assert err.value.status == 401
    assert err.value.code == "wrong_code"
    assert err.value.attempts_left == 4
    assert not isinstance(err.value, AuthError)


async def test_unauthorized_is_auth_error(fake: FakePayload, session) -> None:
    with pytest.raises(AuthError):
        await client(fake, session, "f" * 64).state()


async def test_state_and_launch_and_close(fake: FakePayload, session) -> None:
    c = client(fake, session, fake.add_token())
    assert (await c.launch("PPSA00001")) == {"result": "ok", "already_running": False, "app_id": 100}
    assert (await c.state())["app"]["title_id"] == "PPSA00001"
    with pytest.raises(ApiError) as err:
        await c.launch("PPSA00002")
    assert err.value.code == "other_app_running"
    await c.launch("PPSA00002", replace_running=True)
    with pytest.raises(ApiError) as err:
        await c.close("PPSA00001")
    assert err.value.code == "title_mismatch"
    await c.close("PPSA00002")
    assert fake.running is None
    await c.launch("PPSA00001")
    await c.close()
    assert fake.running is None


async def test_apps_etag_304_returns_none(fake: FakePayload, session) -> None:
    c = client(fake, session, fake.add_token())
    res = await c.apps()
    assert isinstance(res, AppsResult)
    assert res.etag == fake.etag
    assert len(res.apps) == 6
    assert await c.apps(res.etag) is None
    fake.etag = "fedcba9876543210"
    assert (await c.apps(res.etag)).etag == "fedcba9876543210"


async def test_icon_bytes_and_type(fake: FakePayload, session) -> None:
    c = client(fake, session, fake.add_token())
    data, ctype = await c.icon("PPSA00001")
    assert data.startswith(b"\x89PNG")
    assert ctype == "image/png"
    with pytest.raises(ApiError) as err:
        await c.icon("PPSA99177")
    assert err.value.code == "not_found"


async def test_api_error_fields(fake: FakePayload, session) -> None:
    c = client(fake, session, fake.add_token())
    fake.fail["/apps/PPSA00001/launch"] = (
        500, {"error": "system_error", "rc": -2137784315, "rc_hex": "0x80940005"})
    with pytest.raises(ApiError) as err:
        await c.launch("PPSA00001")
    assert err.value.status == 500
    assert err.value.rc_hex == "0x80940005"
    fake.fail["/pair/start"] = (429, {"error": "rate_limited", "retry_after": 17})
    with pytest.raises(ApiError) as err:
        await c.pair_start()
    assert err.value.retry_after == 17


async def test_cannot_connect_when_down(fake: FakePayload, session) -> None:
    c = client(fake, session, fake.add_token())
    await fake.stop()
    with pytest.raises(CannotConnect):
        await c.state()


async def test_post_sends_guard_headers(fake: FakePayload, session) -> None:
    c = client(fake, session, fake.add_token())
    await c.standby() if fake.power_actions else None
    await c.launch("PPSA00001")
    method, path, headers, body = fake.calls[-1]
    assert (method, path) == ("POST", "/apps/PPSA00001/launch")
    assert headers["X-PS5-Hass"] == "1"
    assert headers["Content-Type"].startswith("application/json")
    assert body == {"replace_running": False}
    await c.revoke()
    assert fake.calls[-1][3] == {}
    assert not fake.tokens


async def test_icon_rejects_non_image(fake: FakePayload, session, monkeypatch) -> None:
    """A device posing as the console must not get HTML through HA's image proxy."""
    from aiohttp import web

    c = client(fake, session, fake.add_token())
    monkeypatch.setattr(fake, "_icon", lambda tid, req: web.Response(
        body=b"<script>alert(1)</script>", content_type="text/html"))
    with pytest.raises(ApiError) as err:
        await c.icon("PPSA00001")
    assert err.value.code == "invalid_icon"
    monkeypatch.setattr(fake, "_icon", lambda tid, req: web.Response(
        body=b"<html>", content_type="image/png"))  # lies about the type
    with pytest.raises(ApiError):
        await c.icon("PPSA00001")
    monkeypatch.setattr(fake, "_icon", lambda tid, req: web.Response(
        body=b"\x89PNG\r\n\x1a\n" + b"0" * (1024 * 1024 + 1), content_type="image/png"))
    with pytest.raises(ApiError):
        await c.icon("PPSA00001")


async def test_icon_rejects_bad_title_id(fake: FakePayload, session) -> None:
    c = client(fake, session, fake.add_token())
    with pytest.raises(ApiError) as err:
        await c.icon("../info")
    assert err.value.code == "bad_title_id"
    with pytest.raises(ApiError):
        await c.launch("ppsa00001")
