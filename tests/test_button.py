"""Buttons: close app, rest mode, favourites."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import entry_for
from .helpers import tick


async def _setup(hass: HomeAssistant, fake, **options):
    entry = entry_for(fake, **options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call("button", "press", {"entity_id": entity_id}, blocking=True)


async def test_close_button_sends_title_id(hass, fake, freezer) -> None:
    await _setup(hass, fake)
    fake.running = ("PPSA00001", 24)
    await tick(hass, freezer, 11)
    await _press(hass, "button.ps5_close_app")
    assert fake.running is None
    close = [c for c in fake.calls if c[1] == "/apps/running/close"][-1]
    assert close[3] == {"title_id": "PPSA00001"}


async def test_close_button_mismatch(hass, fake, freezer) -> None:
    await _setup(hass, fake)
    fake.running = ("PPSA00001", 24)
    await tick(hass, freezer, 11)
    fake.running = ("PPSA96708", 25)  # switched on the console, HA not polled yet
    with pytest.raises(HomeAssistantError) as err:
        await _press(hass, "button.ps5_close_app")
    assert err.value.translation_key == "title_mismatch"
    assert fake.running == ("PPSA96708", 25)


async def test_standby_button_only_with_power_actions(hass, fake) -> None:
    await _setup(hass, fake)
    assert hass.states.get("button.ps5_rest_mode") is None


async def test_standby_button_with_power_actions(hass, fake) -> None:
    fake.power_actions = True
    await _setup(hass, fake)
    await _press(hass, "button.ps5_rest_mode")
    assert fake.standby_calls == 1


async def test_favorite_buttons_created_and_launch(hass, fake) -> None:
    await _setup(hass, fake, favorites=["PPSA96708", "PPSA99169"])
    assert hass.states.get("button.ps5_start_retroarch") is not None
    await _press(hass, "button.ps5_start_hikari")
    assert fake.running[0] == "PPSA96708"
    fake_call = [c for c in fake.calls if c[1] == "/apps/PPSA96708/launch"][-1]
    assert fake_call[3] == {"replace_running": True}
