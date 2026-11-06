"""Regression tests: favourites while the console is off, re-reading /info, rest mode, errors."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import entry_for
from .helpers import tick, tick_until

DDP = "custom_components.ps5_hass.coordinator.ddp_status"
NAMES = {"PPSA96708": "Hikari"}


async def _setup(hass: HomeAssistant, entry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_favorite_buttons_exist_when_console_off_at_startup(hass, fake, freezer) -> None:
    entry = entry_for(fake, favorites=["PPSA96708"], favorite_names=NAMES)
    fake.down = True
    with patch(DDP, AsyncMock(return_value="standby")):
        await _setup(hass, entry)
    state = hass.states.get("button.ps5_start_hikari")
    assert state is not None and state.state == "unavailable"
    fake.down = False
    await tick_until(hass, freezer, lambda: hass.states.get("button.ps5_start_hikari").state != "unavailable")
    await hass.services.async_call("button", "press", {"entity_id": "button.ps5_start_hikari"}, blocking=True)
    assert fake.running[0] == "PPSA96708"


async def test_uninstalled_favorite_unavailable(hass, fake) -> None:
    entry = entry_for(fake, favorites=["NOPE00001"], favorite_names={"NOPE00001": "Gone"})
    await _setup(hass, entry)
    assert hass.states.get("button.ps5_start_gone").state == "unavailable"


async def test_quick_restart_rereads_info(hass, fake, freezer) -> None:
    entry = entry_for(fake)
    await _setup(hass, entry)
    fake.down = True
    await tick(hass, freezer, 11)  # one failed poll, well inside the 30 s hold
    fake.power_actions = True
    fake.down = False
    await tick_until(hass, freezer, lambda: entry.data["info"]["power_actions"] is True)
    await hass.async_block_till_done()
    assert hass.states.get("button.ps5_rest_mode") is not None


async def test_info_change_outside_reload_fields_does_not_reload(hass, fake, freezer) -> None:
    entry = entry_for(fake)
    await _setup(hass, entry)
    first = entry.runtime_data
    fake.down = True
    await tick(hass, freezer, 11)
    fake.down = False
    original = fake.info

    def info():
        d = original()
        d["last_error"] = "2026-11-06T14:00:00Z something"
        return d

    fake.info = info
    await tick_until(hass, freezer, lambda: entry.runtime_data.info.get("last_error") is not None)
    await hass.async_block_till_done()
    assert entry.runtime_data is first


async def test_entities_unavailable_after_auth_failure(hass, fake, freezer) -> None:
    entry = entry_for(fake)
    await _setup(hass, entry)
    fake.running = ("PPSA00001", 24)
    await tick(hass, freezer, 11)
    assert hass.states.get("media_player.ps5").state == "playing"
    fake.tokens.clear()
    await tick(hass, freezer, 11)
    assert hass.states.get("sensor.ps5_cpu_temperature").state == "unavailable"
    assert hass.states.get("media_player.ps5").state == "unavailable"


async def test_rest_mode_shown_as_off_with_power_sensor(hass, fake, freezer) -> None:
    entry = entry_for(fake)
    await _setup(hass, entry)
    assert hass.states.get("sensor.ps5_power").state == "on"
    fake.down = True
    with patch(DDP, AsyncMock(return_value="standby")):
        await tick_until(hass, freezer, lambda: hass.states.get("sensor.ps5_power").state == "standby")
    assert hass.states.get("media_player.ps5").state == "off"


async def test_media_stop_when_idle_raises_no_app(hass, fake) -> None:
    entry = entry_for(fake)
    await _setup(hass, entry)
    fake.running = ("PPSA00001", 24)  # running on the console, HA still shows idle
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "media_player", "media_stop", {"entity_id": "media_player.ps5"}, blocking=True)
    assert err.value.translation_key == "no_app"
    assert fake.running == ("PPSA00001", 24)
