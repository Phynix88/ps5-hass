"""Payload switches: one per installed, unprotected payload of the Payload Manager."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .conftest import entry_for
from .helpers import tick

FTPSRV = "switch.ps5_payload_ftpsrv"
WEBSRV = "switch.ps5_payload_websrv"


def _requests(fake, path: str) -> list:
    return [c for c in fake.calls if c[1] == path]


async def _setup(hass: HomeAssistant, fake):
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _payload_switches(hass: HomeAssistant) -> set[str]:
    return {s.entity_id for s in hass.states.async_all("switch") if "_payload_" in s.entity_id}


async def test_switch_per_unprotected_payload(hass: HomeAssistant, fake) -> None:
    await _setup(hass, fake)
    assert _payload_switches(hass) == {FTPSRV, WEBSRV}
    ftp = hass.states.get(FTPSRV)
    assert ftp.state == "off"
    assert ftp.attributes["friendly_name"] == "PS5 ftpsrv"
    assert ftp.attributes["version"] == "1.2"
    assert hass.states.get(WEBSRV).attributes["version"] == ""
    registry = er.async_get(hass)
    entry = registry.async_get(FTPSRV)
    assert entry.unique_id == f"{fake.device_id}_payload_repo_ftpsrv"  # no "/", no hash


async def test_hash_change_keeps_the_entity(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake)
    registry = er.async_get(hass)
    unique_id = registry.async_get(FTPSRV).unique_id
    # the payload or the catalogue is updated: same payload, new hash in the slot
    fake.payload_list[0]["slot"] = "repo/ftpsrv@0f0f0f0f"
    fake.payload_list[0]["running"] = True
    await tick(hass, freezer, 31)
    assert _payload_switches(hass) == {FTPSRV, WEBSRV}  # no ..._2
    assert hass.states.get(FTPSRV).state == "on"
    assert registry.async_get(FTPSRV).unique_id == unique_id
    await hass.services.async_call("switch", "turn_off", {"entity_id": FTPSRV}, blocking=True)
    assert [c[3] for c in _requests(fake, "/payloads/stop")] == [{"slot": "repo/ftpsrv@0f0f0f0f"}]
    assert hass.states.get(FTPSRV).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": FTPSRV}, blocking=True)
    assert [c[3] for c in _requests(fake, "/payloads/start")] == [{"slot": "repo/ftpsrv@0f0f0f0f"}]


async def test_duplicate_key_gets_one_entity(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake)
    fake.payload_list.append({"slot": "repo/ftpsrv@99999999", "name": "ftpsrv", "version": "9",
                              "running": True, "protected": False})
    await tick(hass, freezer, 31)
    assert _payload_switches(hass) == {FTPSRV, WEBSRV}
    assert hass.states.get(FTPSRV).state == "off"  # the first one counts
    assert hass.states.get(FTPSRV).attributes["version"] == "1.2"


async def test_local_upload_without_hash(hass: HomeAssistant, fake, freezer) -> None:
    fake.payload_list.append({"slot": "upload/tool.elf", "name": "tool", "version": "",
                              "running": False, "protected": False})
    await _setup(hass, fake)
    assert hass.states.get("switch.ps5_payload_tool").state == "off"
    registry = er.async_get(hass)
    assert registry.async_get("switch.ps5_payload_tool").unique_id == f"{fake.device_id}_payload_upload_tool_elf"


async def test_keeps_its_name_when_dropped_from_the_list(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake)
    del fake.payload_list[0]
    await tick(hass, freezer, 31)
    state = hass.states.get(FTPSRV)
    assert state.state == "unavailable"
    assert state.attributes["friendly_name"] == "PS5 ftpsrv"
    await hass.services.async_call("switch", "turn_on", {"entity_id": FTPSRV}, blocking=True)
    assert not _requests(fake, "/payloads/start")  # an unavailable entity is not called


async def test_incomplete_entries_fail_safe(hass: HomeAssistant, fake) -> None:
    answer = {"result": "ok", "available": True, "payloads": [
        *fake.payload_list,
        {"name": "no slot", "running": False, "protected": False},
        {"slot": "repo/unmarked@aa", "name": "unmarked", "version": "", "running": False},  # no flag
    ]}
    with patch("custom_components.ps5_hass.api.Ps5HassClient.payloads", AsyncMock(return_value=answer)):
        await _setup(hass, fake)
    assert _payload_switches(hass) == {FTPSRV, WEBSRV}


async def test_console_off_at_startup(hass: HomeAssistant, fake, freezer) -> None:
    fake.power_state = "rest"
    await _setup(hass, fake)
    assert _payload_switches(hass) == set()
    assert not _requests(fake, "/payloads")
    fake.power_state = "on"
    await tick(hass, freezer, 11)
    assert _payload_switches(hass) == {FTPSRV, WEBSRV}
    assert hass.states.get(FTPSRV).state == "off"


async def test_state_follows_the_list(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake)
    fake.payload_list[0]["running"] = True
    await tick(hass, freezer, 31)
    assert hass.states.get(FTPSRV).state == "on"
    # a payload installed later gets its switch; polling again adds nothing
    fake.payload_list.append({"slot": "repo/orbit_tool@99aa", "name": "Orbit Tool", "version": "2",
                              "running": False, "protected": False})
    await tick(hass, freezer, 31)
    assert hass.states.get("switch.ps5_payload_orbit_tool").state == "off"
    await tick(hass, freezer, 31)
    assert len(_payload_switches(hass)) == 3


async def test_protected_never_created(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake)
    assert hass.states.get("switch.ps5_payload_kstuff") is None
    registry = er.async_get(hass)
    assert not [e for e in registry.entities.values() if e.domain == "switch" and "kstuff" in e.unique_id]

    # a protected payload that shows up later gets none either
    fake.payload_list.append({"slot": "repo/etaHEN@aa", "name": "etaHEN", "version": "",
                              "running": False, "protected": True})
    await tick(hass, freezer, 31)
    assert _payload_switches(hass) == {FTPSRV, WEBSRV}

    # one that becomes protected keeps its entity but can no longer be used
    fake.payload_list[0]["protected"] = True
    await tick(hass, freezer, 31)
    assert hass.states.get(FTPSRV).state == "unavailable"
    assert hass.states.get(WEBSRV).state == "off"


async def test_turn_on_starts_and_refreshes(hass: HomeAssistant, fake) -> None:
    await _setup(hass, fake)
    before = len(_requests(fake, "/payloads"))
    await hass.services.async_call("switch", "turn_on", {"entity_id": FTPSRV}, blocking=True)
    assert [c[3] for c in _requests(fake, "/payloads/start")] == [{"slot": "repo/ftpsrv@e8cce5d1"}]
    assert len(_requests(fake, "/payloads")) == before + 1  # refreshed at once
    assert hass.states.get(FTPSRV).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": FTPSRV}, blocking=True)
    assert [c[3] for c in _requests(fake, "/payloads/stop")] == [{"slot": "repo/ftpsrv@e8cce5d1"}]
    assert hass.states.get(FTPSRV).state == "off"


async def test_conflicts_are_silent(hass: HomeAssistant, fake) -> None:
    await _setup(hass, fake)
    fake.payload_list[0]["running"] = True  # started elsewhere since the last poll
    await hass.services.async_call("switch", "turn_on", {"entity_id": FTPSRV}, blocking=True)
    assert hass.states.get(FTPSRV).state == "on"
    fake.payload_list[0]["running"] = False  # stopped elsewhere
    await hass.services.async_call("switch", "turn_off", {"entity_id": FTPSRV}, blocking=True)
    assert hass.states.get(FTPSRV).state == "off"


async def test_unavailable_when_pldmgr_down(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake)
    fake.payload_manager = False
    with pytest.raises(HomeAssistantError) as err:  # before the next poll has noticed
        await hass.services.async_call("switch", "turn_on", {"entity_id": FTPSRV}, blocking=True)
    assert err.value.translation_key == "payload_manager_unavailable"
    await tick(hass, freezer, 31)
    assert hass.states.get(FTPSRV).state == "unavailable"
    fake.payload_manager = True
    await tick(hass, freezer, 31)
    assert hass.states.get(FTPSRV).state == "off"


async def test_errors_are_translated(hass: HomeAssistant, fake) -> None:
    await _setup(hass, fake)
    fake.power_state = "rest"
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("switch", "turn_on", {"entity_id": FTPSRV}, blocking=True)
    assert err.value.translation_key == "not_on"
    fake.power_state = "on"
    fake.payload_list[0]["protected"] = True  # protected since the last poll
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("switch", "turn_on", {"entity_id": FTPSRV}, blocking=True)
    assert err.value.translation_key == "protected"


async def test_no_requests_while_the_console_is_away(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake)
    fake.power_state = "rest"
    await tick(hass, freezer, 11)  # the state poll sees rest mode
    seen = len(_requests(fake, "/payloads"))
    await tick(hass, freezer, 31)
    await tick(hass, freezer, 31)
    assert len(_requests(fake, "/payloads")) == seen
    assert hass.states.get(FTPSRV).state == "unavailable"
    fake.power_state = "on"
    await tick(hass, freezer, 11)  # back on: asked again at once
    assert hass.states.get(FTPSRV).state == "off"


@pytest.mark.parametrize("legacy", ["legacy_v03", "legacy_v02"])
async def test_nothing_for_an_older_payload(hass: HomeAssistant, fake, freezer, legacy: str) -> None:
    setattr(fake, legacy, True)
    await _setup(hass, fake)
    await tick(hass, freezer, 31)
    assert _payload_switches(hass) == set()
    assert not [c for c in fake.calls if c[1].startswith("/payloads")]


@pytest.mark.parametrize("version", ["v0.4.0", "0.4.1", "v0.4.1", "0.3.9", "v0.1.0"])
async def test_no_switches_before_payload_0_4_2(hass: HomeAssistant, fake, freezer, caplog, version: str) -> None:
    # v0.4.0 and v0.4.1 do not see a minimised game: no switch, and /payloads is not even asked
    fake.version = version
    await _setup(hass, fake)
    await tick(hass, freezer, 31)
    assert _payload_switches(hass) == set()
    assert not [c for c in fake.calls if c[1].startswith("/payloads")]
    assert caplog.text.count("update the payload to v0.4.2 for payload switches") == 1


@pytest.mark.parametrize("version", ["v0.4.2", "0.4.2", "v0.4.2-test", "0.4.3", "v0.5.0", "v1.0.0", "dev", "host", ""])
async def test_switches_from_payload_0_4_2_and_for_dev_builds(
    hass: HomeAssistant, fake, caplog, version: str
) -> None:
    fake.version = version
    await _setup(hass, fake)
    assert _payload_switches(hass) == {FTPSRV, WEBSRV}
    assert "update the payload to v0.4.2" not in caplog.text


def test_payload_switches_supported() -> None:
    from custom_components.ps5_hass.payloads import payload_switches_supported

    for old in ("v0.4.1", "0.4.0", "v0.3.0", "0.4.1-test"):
        assert not payload_switches_supported({"version": old}), old
    for new in ("v0.4.2", "0.4.2-test", "v0.10.0", "v0.4.10", "dev", "host", "", None, 5):
        assert payload_switches_supported({"version": new}), new
    assert payload_switches_supported({})  # an old entry without a version
    from custom_components.ps5_hass.coordinator import info_needs_reload
    # an update of the payload across the limit changes the entities
    assert info_needs_reload({"version": "v0.4.1"}, {"version": "v0.4.2"})
    assert not info_needs_reload({"version": "v0.4.2"}, {"version": "v0.4.3"})


def test_payload_control_flip_is_no_reload() -> None:
    from custom_components.ps5_hass.coordinator import info_needs_reload

    off = {"capabilities": {"fan": True, "payload_control": False}}
    on = {"capabilities": {"fan": True, "payload_control": True}}
    assert not info_needs_reload(off, on)
    assert info_needs_reload({"capabilities": {"fan": True}}, off)  # a v0.3 payload became v0.4
    assert info_needs_reload(off, {"capabilities": {"fan": False, "payload_control": False}})


async def test_running_app_refuses_start_and_stop(hass: HomeAssistant, fake) -> None:
    await _setup(hass, fake)
    # the state has not been polled since: the switch still asks, the payload refuses
    fake.running = ("PPSA00001", 100)  # a game runs
    for service in ("turn_on", "turn_off"):
        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call("switch", service, {"entity_id": FTPSRV}, blocking=True)
        assert err.value.translation_key == "app_running"
    assert fake.pldmgr_actions == 0  # nothing reached the Payload Manager
    assert hass.states.get(FTPSRV).state == "off"
    fake.running = None  # the game is closed
    await hass.services.async_call("switch", "turn_on", {"entity_id": FTPSRV}, blocking=True)
    assert fake.pldmgr_actions == 1
    assert hass.states.get(FTPSRV).state == "on"


async def test_cheatrunner_has_a_switch_ps5debug_has_none(hass: HomeAssistant, fake, freezer) -> None:
    # CheatRunner is controllable again since payload v0.4.2 (only with no game in memory);
    # ps5debug stays protected
    fake.payload_list.append({"slot": "repo/CheatRunner@22222222", "name": "CheatRunner", "version": "0.18.1",
                              "running": True, "protected": False})
    await _setup(hass, fake)
    assert hass.states.get("switch.ps5_payload_cheatrunner").state == "on"
    fake.payload_list.append({"slot": "repo/ps5debug@33333333", "name": "ps5debug", "version": "",
                              "running": False, "protected": True})
    await tick(hass, freezer, 31)
    assert _payload_switches(hass) == {FTPSRV, WEBSRV, "switch.ps5_payload_cheatrunner"}
    # an existing payload that is now listed as ps5debug keeps its entity but is unavailable
    # (the payload protects it by its name; the fake does the same)
    fake.payload_list[1]["name"] = "ps5debug"
    await tick(hass, freezer, 31)
    assert hass.states.get(WEBSRV).state == "unavailable"


async def test_second_refresh_after_5_s(hass: HomeAssistant, fake, freezer) -> None:
    # right after a start the Payload Manager does not list the new process yet
    fake.payload_lag = 1
    await _setup(hass, fake)
    before = len(_requests(fake, "/payloads"))
    await hass.services.async_call("switch", "turn_on", {"entity_id": FTPSRV}, blocking=True)
    assert len(_requests(fake, "/payloads")) == before + 1  # refreshed at once
    assert hass.states.get(FTPSRV).state == "off"  # not listed as running yet
    await tick(hass, freezer, 4)
    assert len(_requests(fake, "/payloads")) == before + 1  # not yet
    await tick(hass, freezer, 2)  # 5 s after the start
    assert len(_requests(fake, "/payloads")) == before + 2
    assert hass.states.get(FTPSRV).state == "on"
    # the same for a stop
    await hass.services.async_call("switch", "turn_off", {"entity_id": FTPSRV}, blocking=True)
    assert hass.states.get(FTPSRV).state == "on"
    await tick(hass, freezer, 6)
    assert hass.states.get(FTPSRV).state == "off"


async def test_second_refresh_is_cancelled_on_unload(hass: HomeAssistant, fake, freezer) -> None:
    fake.payload_lag = 1
    entry = await _setup(hass, fake)
    await hass.services.async_call("switch", "turn_on", {"entity_id": FTPSRV}, blocking=True)
    seen = len(_requests(fake, "/payloads"))
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 10)
    assert len(_requests(fake, "/payloads")) == seen


async def test_minimised_game_is_refused_by_the_payload(hass: HomeAssistant, fake) -> None:
    # no running app in the state (the console reports none), but the payload sees the game in memory
    await _setup(hass, fake)
    fake.minimised = True
    for service in ("turn_on", "turn_off"):
        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call("switch", service, {"entity_id": FTPSRV}, blocking=True)
        assert err.value.translation_key == "app_running"
    assert fake.pldmgr_actions == 0
    fake.minimised = False
    await hass.services.async_call("switch", "turn_on", {"entity_id": FTPSRV}, blocking=True)
    assert fake.pldmgr_actions == 1


async def test_known_running_app_refuses_without_a_request(hass: HomeAssistant, fake, freezer) -> None:
    # also protects against a payload older than v0.4.1, which would obey
    await _setup(hass, fake)
    fake.running = ("PPSA00001", 100)
    await tick(hass, freezer, 11)  # the state coordinator sees the game
    for service in ("turn_on", "turn_off"):
        with pytest.raises(HomeAssistantError) as err:
            await hass.services.async_call("switch", service, {"entity_id": FTPSRV}, blocking=True)
        assert err.value.translation_key == "app_running"
    assert _requests(fake, "/payloads/start") == []
    assert _requests(fake, "/payloads/stop") == []
    fake.running = None
    await tick(hass, freezer, 11)
    await hass.services.async_call("switch", "turn_on", {"entity_id": FTPSRV}, blocking=True)
    assert len(_requests(fake, "/payloads/start")) == 1
