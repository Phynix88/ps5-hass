"""App started/stopped events."""

from __future__ import annotations

from homeassistant.core import callback

from .conftest import entry_for
from .helpers import tick

ENTITY = "event.ps5_app"


async def _setup(hass, fake):
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


def _events(hass, seen):
    st = hass.states.get(ENTITY)
    if st.state not in ("unknown", "unavailable") and (st.state, st.attributes.get("event_type")) not in [
        (s[0], s[1]) for s in seen
    ]:
        seen.append((st.state, st.attributes["event_type"], st.attributes.get("title_id")))


async def test_no_event_on_startup(hass, fake) -> None:
    fake.running = ("PPSA00001", 24)
    await _setup(hass, fake)
    assert hass.states.get(ENTITY).state == "unknown"


async def test_event_started_stopped_and_switch(hass, fake, freezer) -> None:
    await _setup(hass, fake)
    fired = []

    @callback
    def listen(event) -> None:  # a callback runs in order on the loop
        if event.data["entity_id"] == ENTITY:
            attrs = event.data["new_state"].attributes
            fired.append((attrs.get("event_type"), attrs.get("title_id")))

    hass.bus.async_listen("state_changed", listen)
    fake.running = ("PPSA00001", 24)
    await tick(hass, freezer, 11)
    fake.running = ("PPSA96708", 25)
    await tick(hass, freezer, 11)
    fake.running = None
    await tick(hass, freezer, 11)
    assert fired == [
        ("started", "PPSA00001"),
        ("stopped", "PPSA00001"),
        ("started", "PPSA96708"),
        ("stopped", "PPSA96708"),
    ]
    st = hass.states.get(ENTITY)
    assert st.attributes["name"] == "Hikari"
    assert st.attributes["kind"] == "app"
