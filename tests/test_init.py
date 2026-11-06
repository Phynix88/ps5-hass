"""Setup and unload, online and offline."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import entry_for
from .fake_payload import FakePayload

DDP = "custom_components.ps5_hass.coordinator.ddp_status"


async def test_setup_online(hass: HomeAssistant, fake: FakePayload) -> None:
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    data = entry.runtime_data
    assert data.state.data.power == "on" and data.state.data.payload
    assert len(data.apps.data) == 6
    assert data.info["device_id"] == fake.device_id


async def test_setup_offline_uses_cached_info(hass: HomeAssistant, fake: FakePayload) -> None:
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    fake.down = True
    with patch(DDP, AsyncMock(return_value="standby")):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    data = entry.runtime_data
    assert data.state.data.power == "standby"
    assert data.state.data.payload is False
    assert data.info == entry.data["info"]
    assert data.apps.data == []


async def test_unload(hass: HomeAssistant, fake: FakePayload) -> None:
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
