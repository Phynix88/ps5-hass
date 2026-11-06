"""notify.ps5: messages on the TV."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from .conftest import entry_for
from .helpers import tick_until

ENTITY = "notify.ps5"
DDP = "custom_components.ps5_hass.coordinator.ddp_status"


async def _setup(hass, fake):
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _send(hass, **data) -> None:
    await hass.services.async_call(
        "notify", "send_message", {"entity_id": ENTITY, **data}, blocking=True)


async def test_notify_sends(hass, fake) -> None:
    await _setup(hass, fake)
    await _send(hass, message="Essen ist fertig")
    await _send(hass, message="kommt gleich", title="Paket")
    assert fake.notifications == ["Essen ist fertig", "Paket: kommt gleich"]


async def test_notify_rate_limited_error(hass, fake) -> None:
    await _setup(hass, fake)
    for _ in range(5):
        await _send(hass, message="x")
    with pytest.raises(HomeAssistantError) as err:
        await _send(hass, message="x")
    assert err.value.translation_key == "rate_limited"
    assert err.value.translation_placeholders == {"retry_after": "12"}


async def test_notify_unavailable_when_off(hass, fake, freezer) -> None:
    await _setup(hass, fake)
    fake.down = True
    with patch(DDP, AsyncMock(return_value="off")):
        await tick_until(hass, freezer, lambda: hass.states.get(ENTITY).state == "unavailable")
