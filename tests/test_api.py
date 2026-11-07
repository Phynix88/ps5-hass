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


# --- v0.4 ---------------------------------------------------------------


async def test_playtime(fake: FakePayload, session) -> None:
    fake.playtime_titles = [
        {"title_id": "PPSA00001", "name": "Game", "kind": "game", "total_s": 5400,
         "console_total_s": None},
    ]
    fake.playtime_sessions = [
        {"title_id": "PPSA00001", "kind": "game", "start": 1790000000, "duration_s": 1800},
    ]
    fake.playtime_current = {
        "title_id": "PPSA00001", "kind": "game", "start": 1790003000, "duration_s": 95,
    }
    c = client(fake, session, fake.add_token())
    res = await c.playtime()
    assert res["foreground"] is True
    assert res["titles"][0]["total_s"] == 5400
    assert res["current"]["duration_s"] == 95
    assert fake.calls[-1][:2] == ("GET", "/playtime")
    # since is passed on, and the fake filters finished sessions that end before it
    res = await c.playtime(since=1790002000)
    assert res["sessions"] == []
    assert len(res["titles"]) == 1
    res = await c.playtime(since=1790000000)
    assert len(res["sessions"]) == 1


async def test_playtime_bad_since(fake: FakePayload, session) -> None:
    c = client(fake, session, fake.add_token())
    with pytest.raises(ApiError) as err:
        await c.playtime(since=-5)
    assert (err.value.status, err.value.code) == (400, "bad_request")


async def test_set_keep_awake(fake: FakePayload, session) -> None:
    c = client(fake, session, fake.add_token())
    assert await c.set_keep_awake(True) is True
    assert fake.keep_awake is True
    assert fake.calls[-1][3] == {"on": True}
    assert await c.set_keep_awake(False) is False
    assert fake.keep_awake is False


async def test_set_keep_awake_not_on(fake: FakePayload, session) -> None:
    fake.power_state = "rest"
    c = client(fake, session, fake.add_token())
    with pytest.raises(ApiError) as err:
        await c.set_keep_awake(True)
    assert (err.value.status, err.value.code) == (409, "not_on")
    assert await c.set_keep_awake(False) is False  # switching off is always allowed


async def test_suspend(fake: FakePayload, session) -> None:
    fake.running = ("PPSA00001", 100)
    c = client(fake, session, fake.add_token())
    assert await c.suspend() is None
    assert fake.running is None
    assert fake.calls[-1][:2] == ("POST", "/apps/running/suspend")


async def test_suspend_not_running(fake: FakePayload, session) -> None:
    c = client(fake, session, fake.add_token())
    with pytest.raises(ApiError) as err:
        await c.suspend()
    assert (err.value.status, err.value.code) == (409, "not_running")


async def test_payloads(fake: FakePayload, session) -> None:
    c = client(fake, session, fake.add_token())
    assert fake.info()["capabilities"]["payload_control"] is False  # not before the first call
    res = await c.payloads()
    assert res["available"] is True
    assert {"slot", "name", "version", "running", "protected"} <= set(res["payloads"][0])
    assert any(p["protected"] for p in res["payloads"])
    assert fake.info()["capabilities"]["payload_control"] is True


async def test_payloads_manager_unreachable(fake: FakePayload, session) -> None:
    fake.payload_manager = False
    c = client(fake, session, fake.add_token())
    assert await c.payloads() == {"result": "ok", "available": False, "payloads": []}
    assert fake.info()["capabilities"]["payload_control"] is False


async def test_payload_start_stop(fake: FakePayload, session) -> None:
    c = client(fake, session, fake.add_token())
    slot = fake.payload_list[0]["slot"]
    assert not fake.payload_list[0]["running"]
    assert await c.payload_start(slot) is None
    assert fake.payload_list[0]["running"] is True
    assert fake.calls[-1][3] == {"slot": slot}
    with pytest.raises(ApiError) as err:
        await c.payload_start(slot)
    assert (err.value.status, err.value.code) == (409, "already_running")
    assert await c.payload_stop(slot) is None
    assert fake.payload_list[0]["running"] is False
    with pytest.raises(ApiError) as err:
        await c.payload_stop(slot)
    assert (err.value.status, err.value.code) == (409, "not_running")


async def test_payload_start_errors(fake: FakePayload, session) -> None:
    c = client(fake, session, fake.add_token())
    protected = next(p["slot"] for p in fake.payload_list if p["protected"])
    for call in (c.payload_start, c.payload_stop):
        with pytest.raises(ApiError) as err:
            await call(protected)
        assert (err.value.status, err.value.code) == (403, "protected")
        with pytest.raises(ApiError) as err:
            await call("repo/nothing@00000000")
        assert (err.value.status, err.value.code) == (404, "unknown_payload")
    assert [p["running"] for p in fake.payload_list] == [False, False, True]  # kstuff untouched
    fake.payload_manager = False
    with pytest.raises(ApiError) as err:
        await c.payload_start(fake.payload_list[0]["slot"])
    assert (err.value.status, err.value.code) == (503, "payload_manager_unavailable")
    fake.payload_manager = True
    fake.power_state = "rest"
    with pytest.raises(ApiError) as err:
        await c.payload_stop(fake.payload_list[0]["slot"])
    assert (err.value.status, err.value.code) == (409, "not_on")
