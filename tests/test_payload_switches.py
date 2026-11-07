"""Payload switches: one per installed, unprotected payload of the Payload Manager."""

from __future__ import annotations

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
    assert entry.unique_id == f"{fake.device_id}_payload_repo_ftpsrv_e8cce5d1"  # no "/" or "@"


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


def test_payload_control_flip_is_no_reload() -> None:
    from custom_components.ps5_hass.coordinator import info_needs_reload

    off = {"capabilities": {"fan": True, "payload_control": False}}
    on = {"capabilities": {"fan": True, "payload_control": True}}
    assert not info_needs_reload(off, on)
    assert info_needs_reload({"capabilities": {"fan": True}}, off)  # a v0.3 payload became v0.4
    assert info_needs_reload(off, {"capabilities": {"fan": False, "payload_control": False}})
