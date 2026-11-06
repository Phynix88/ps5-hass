"""State and app list coordinators."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.core import HomeAssistant

from custom_components.ps5_hass.const import DOMAIN

from .conftest import entry_for
from .fake_payload import FakePayload
from homeassistant.util import dt as dt_util

from .helpers import tick, tick_until

DDP = "custom_components.ps5_hass.coordinator.ddp_status"


async def _setup(hass: HomeAssistant, fake: FakePayload):
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_hold_30s_then_ddp_standby(hass, fake, freezer: FrozenDateTimeFactory) -> None:
    entry = await _setup(hass, fake)
    coordinator = entry.runtime_data.state
    fake.down = True
    held_for = []

    async def ddp(*args, **kwargs):
        held_for.append(dt_util.utcnow() - coordinator._failing_since)
        return "standby"

    with patch(DDP, ddp):
        await tick_until(hass, freezer, lambda: coordinator._failing_since is not None)
        assert coordinator.data.payload is True  # held right after the first failure
        await tick_until(hass, freezer, lambda: not coordinator.data.payload)
    state = coordinator.data
    assert (state.power, state.payload, state.state) == ("standby", False, None)
    assert held_for and held_for[0].total_seconds() >= 30


@pytest.mark.parametrize(("answer", "power"), [("on", "on"), ("off", "off")])
async def test_ddp_answers(hass, fake, freezer, answer: str, power: str) -> None:
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    fake.down = True
    with patch(DDP, AsyncMock(return_value=answer)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.runtime_data.state.data.power == power
    assert entry.runtime_data.state.data.payload is False


async def test_back_online_reads_info_and_reloads_on_power_actions_change(
    hass, fake, freezer
) -> None:
    entry = await _setup(hass, fake)
    first = entry.runtime_data
    fake.down = True
    with patch(DDP, AsyncMock(return_value="on")):
        await tick_until(hass, freezer, lambda: not entry.runtime_data.state.data.payload)
        fake.power_actions = True
        fake.down = False
        await tick_until(hass, freezer, lambda: entry.runtime_data is not first)
        await hass.async_block_till_done()
    assert entry.data["info"]["power_actions"] is True
    assert entry.runtime_data.info["power_actions"] is True


async def test_401_starts_reauth_once(hass, fake, freezer) -> None:
    await _setup(hass, fake)
    fake.tokens.clear()
    for _ in range(3):
        await tick(hass, freezer, 11)
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert len(list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))) == 1


async def test_apps_refresh_on_etag_change(hass, fake, freezer) -> None:
    entry = await _setup(hass, fake)
    fake.apps.append({"title_id": "NEWA00001", "name": "New", "version": None,
                      "kind": "app", "category": None, "has_icon": False})
    fake.etag = "fedcba9876543210"
    await tick_until(
        hass, freezer,
        lambda: any(a["title_id"] == "NEWA00001" for a in entry.runtime_data.apps.data),
        max_steps=3,
    )


async def test_apps_304_keeps_list(hass, fake, freezer) -> None:
    entry = await _setup(hass, fake)
    before = entry.runtime_data.apps.data
    await tick(hass, freezer, 601)
    conditional = [c for c in fake.calls if c[1] == "/apps" and "If-None-Match" in c[2]]
    assert conditional
    assert entry.runtime_data.apps.data == before


async def test_unavailable_logged_once(hass, fake, freezer, caplog) -> None:
    caplog.set_level(logging.INFO, logger="custom_components.ps5_hass")
    entry = await _setup(hass, fake)
    coordinator = entry.runtime_data.state
    fake.down = True
    with patch(DDP, AsyncMock(return_value="standby")):
        await tick_until(hass, freezer, lambda: not coordinator.data.payload)
        for _ in range(4):
            await tick(hass, freezer, 10)
        fake.down = False
        await tick_until(hass, freezer, lambda: coordinator.data.payload)
        for _ in range(3):
            await tick(hass, freezer, 10)
    msgs = [r.getMessage() for r in caplog.records if r.name.startswith("custom_components.ps5_hass")]
    assert sum("not reachable" in m for m in msgs) == 1
    assert sum("reachable again" in m for m in msgs) == 1
    assert not [r for r in caplog.records
                if r.name.startswith("custom_components.ps5_hass") and r.levelno >= logging.WARNING]
