"""Diagnostics never contain the token."""

from __future__ import annotations

import json

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from .conftest import entry_for


async def test_diagnostics_redacts_token_and_client_id(hass: HomeAssistant, fake, hass_client) -> None:
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    text = json.dumps(diag)
    assert entry.data["token"] not in text
    assert entry.data["client_id"] not in text
    assert diag["entry"]["data"]["token"] == "**REDACTED**"
    assert diag["info"]["device_id"] == fake.device_id
    assert diag["console"]["power"] == "on"
    assert diag["apps"]["count"] == 6
