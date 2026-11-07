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


# --- v0.4 ---------------------------------------------------------------


async def _call(fake: FakePayload, method: str, path: str, body: dict | None = None):
    token = next(iter(fake.tokens.values()), None) or fake.add_token()
    headers = {"Authorization": f"Bearer {token}", "X-PS5-Hass": "1"}
    async with aiohttp.ClientSession() as s:
        async with s.request(method, f"{fake.url}/api/v1{path}", json=body, headers=headers) as r:
            return r.status, await r.json()


async def test_fake_state_v04_fields(fake: FakePayload) -> None:
    fake.drives = [{"name": "m2", "free_mb": 500000, "total_mb": 1000000}]
    fake.keep_awake = True
    status, body = await _call(fake, "GET", "/state")
    assert status == 200
    assert body["power_state"] == "on" and body["keep_awake"] is True
    assert body["soc_power"]["total_mw"] > 0 and body["soc_power"]["rails"]
    assert body["drives"] == fake.drives
    caps = fake.info()["capabilities"]
    for key in ("playtime", "keep_awake", "suspend", "soc_power", "focus", "power_state"):
        assert caps[key] is True
    fake.soc_power = None
    status, body = await _call(fake, "GET", "/state")
    assert status == 200 and body["soc_power"] is None


async def test_fake_outside_on_reads_nothing_and_refuses_actions(fake: FakePayload) -> None:
    fake.power_state = "rest"
    fake.running = ("PPSA00001", 100)
    fake.drives = [{"name": "m2", "free_mb": 1, "total_mb": 2048}]
    status, body = await _call(fake, "GET", "/state")
    assert status == 200
    assert body["power_state"] == "rest"
    assert body["in_app"] is False and body["app"] is None
    assert body["temps"] == {"cpu": None, "soc": None} and body["fan_raw"] is None
    assert body["soc_power"] is None and body["drives"] == [] and body["payloads"] == []
    assert set(body["storage"].values()) == {None}
    assert (await _call(fake, "GET", "/apps"))[0] == 200
    assert (await _call(fake, "GET", "/playtime"))[0] == 200
    status, body = await _call(fake, "GET", "/payloads")
    assert status == 200 and body["available"] is False and body["payloads"] == []
    fake.power_actions = fake.power_off = fake.fan_control = True
    for path, req in [
        ("/apps/PPSA00001/launch", {}),
        ("/apps/running/close", {}),
        ("/apps/running/suspend", {}),
        ("/power/standby", {}),
        ("/power/off", {}),
        ("/notify", {"message": "hi"}),
        ("/fan", {"threshold_c": 60}),
        ("/payloads/start", {"slot": fake.payload_list[0]["slot"]}),
        ("/payloads/stop", {"slot": fake.payload_list[0]["slot"]}),
        ("/keep_awake", {"on": True}),
    ]:
        status, body = await _call(fake, "POST", path, req)
        assert (status, body["error"]) == (409, "not_on"), path
    assert fake.running is not None and fake.standby_calls == 0 and fake.power_off_calls == 0
    status, body = await _call(fake, "POST", "/keep_awake", {"on": False})
    assert status == 200 and body == {"result": "ok", "keep_awake": False}


async def test_fake_keep_awake_and_suspend_need_capability(fake: FakePayload) -> None:
    fake.capabilities["keep_awake"] = False
    fake.capabilities["suspend"] = False
    fake.running = ("PPSA00001", 100)
    status, body = await _call(fake, "POST", "/keep_awake", {"on": True})
    assert (status, body["error"]) == (409, "not_available")
    status, body = await _call(fake, "POST", "/keep_awake", {"on": "yes"})
    assert (status, body["error"]) == (400, "bad_request")
    status, body = await _call(fake, "POST", "/apps/running/suspend", {})
    assert (status, body["error"]) == (409, "not_available")
    assert fake.running is not None


async def test_fake_playtime_since_validation(fake: FakePayload) -> None:
    for bad in ("abc", "", "123456789012", "-1"):
        status, body = await _call(fake, "GET", f"/playtime?since={bad}")
        assert (status, body["error"]) == (400, "bad_request"), bad
    status, body = await _call(fake, "GET", "/playtime?since=0")
    assert status == 200 and body["result"] == "ok"


async def test_fake_payload_control_capability_follows_first_call(fake: FakePayload) -> None:
    assert fake.info()["capabilities"]["payload_control"] is False
    await _call(fake, "GET", "/payloads")
    assert fake.info()["capabilities"]["payload_control"] is True
    fake.capabilities["payload_control"] = False  # a test can force it
    assert fake.info()["capabilities"]["payload_control"] is False


async def test_fake_legacy_v03_has_none_of_v04(fake: FakePayload) -> None:
    fake.legacy_v03 = True
    info = fake.info()
    assert not {"focus", "power_state", "suspend", "keep_awake", "soc_power", "playtime",
                "payload_control"} & set(info["capabilities"])
    status, body = await _call(fake, "GET", "/state")
    assert status == 200
    assert not {"power_state", "keep_awake", "soc_power", "drives"} & set(body)
    for method, path in [("GET", "/playtime"), ("GET", "/payloads"),
                         ("POST", "/keep_awake"), ("POST", "/apps/running/suspend")]:
        status, body = await _call(fake, method, path, {} if method == "POST" else None)
        assert (status, body["error"]) == (404, "not_found"), path
