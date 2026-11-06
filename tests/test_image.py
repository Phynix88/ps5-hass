"""Cover image of the running app."""

from __future__ import annotations

from .conftest import entry_for
from .fake_payload import tiny_png
from .helpers import tick

ENTITY = "image.ps5_cover"


async def _setup(hass, fake):
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_cover_image_follows_app(hass, fake, freezer, hass_client) -> None:
    await _setup(hass, fake)
    fake.running = ("PPSA00001", 24)
    await tick(hass, freezer, 11)
    first = hass.states.get(ENTITY)
    client = await hass_client()
    resp = await client.get(first.attributes["entity_picture"])
    assert resp.status == 200
    assert await resp.read() == tiny_png(0)
    fake.running = ("PPSA00002", 25)
    await tick(hass, freezer, 11)
    second = hass.states.get(ENTITY)
    assert second.state != first.state  # image_last_updated changed
    resp = await client.get(second.attributes["entity_picture"])
    assert await resp.read() == tiny_png(1)


async def test_cover_none_when_idle(hass, fake, hass_client) -> None:
    await _setup(hass, fake)
    state = hass.states.get(ENTITY)
    client = await hass_client()
    resp = await client.get(state.attributes["entity_picture"])
    assert resp.status == 500 or resp.status == 404
