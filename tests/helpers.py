"""Test helpers that need Home Assistant."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed


async def tick(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    """Move time forward and let scheduled updates run."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done(wait_background_tasks=True)


async def tick_until(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    condition,
    step: float = 10,
    max_steps: int = 20,
) -> int:
    """Tick until condition() is true; returns the number of ticks. Fails after max_steps."""
    for n in range(1, max_steps + 1):
        await tick(hass, freezer, step)
        if condition():
            return n
    raise AssertionError("condition not reached")
