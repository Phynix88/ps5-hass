"""The fake payload itself: it must speak the real API (schema-checked)."""

import aiohttp
import pytest

from .fake_payload import FakePayload


async def test_info_matches_schema(fake: FakePayload) -> None:
    async with aiohttp.ClientSession() as s:
        async with s.get(f"{fake.url}/api/v1/info") as r:
            assert r.status == 200
            data = await r.json()
    assert data["device_id"] == fake.device_id
    assert data["api"] == 1


async def test_schema_violation_detected(fake: FakePayload) -> None:
    fake.expect_schema_errors = True
    fake.state_override = {"in_app": "yes"}  # wrong type
    fake.tokens["c"] = "a" * 64
    async with aiohttp.ClientSession() as s:
        async with s.get(
            f"{fake.url}/api/v1/state", headers={"Authorization": "Bearer " + "a" * 64}
        ) as r:
            assert r.status == 500
    assert fake.schema_errors


async def test_post_requires_marker(fake: FakePayload) -> None:
    async with aiohttp.ClientSession() as s:
        async with s.post(f"{fake.url}/api/v1/pair/start", json={}) as r:
            assert r.status == 403
            assert (await r.json())["error"] == "browser_guard"


async def test_fake_info_permissions_validates_schema(fake: FakePayload) -> None:
    fake.power_actions = True
    info = fake.info()
    assert info["permissions"]["power_actions"] == {"allowed": True, "available": True}
    assert info["permissions"]["power_off"] == {"allowed": False, "available": False}
    fake._json("info", info)
    assert fake.schema_errors == []


async def test_fake_grant_routes_like_payload_v04(fake: FakePayload) -> None:
    token = fake.add_token()
    headers = {"Authorization": f"Bearer {token}", "X-PS5-Hass": "1"}
    async with aiohttp.ClientSession() as s:
        async def post(path: str, body: dict) -> tuple[int, dict]:
            async with s.post(f"{fake.url}/api/v1{path}", json=body, headers=headers) as r:
                return r.status, await r.json()

        status, body = await post("/grant/start", {"permissions": ["power_off"]})
        assert (status, body["error"]) == (409, "already_allowed")
        status, body = await post("/grant/finish", {"code": "123456"})
        assert (status, body["error"]) == (409, "no_code")
        status, body = await post("/grant/revoke", {"permissions": ["power_off"]})
        assert status == 200 and body["result"] == "ok"
