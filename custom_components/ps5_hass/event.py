"""App started/stopped events."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import Ps5HassConfigEntry, app_by_id
from .entity import Ps5HassEntity

_UNSET = object()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ps5HassConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([Ps5AppEvent(entry)])


class Ps5AppEvent(Ps5HassEntity, EventEntity):
    """Fires when the running app changes (not when the payload goes away)."""

    _attr_translation_key = "app"
    _attr_event_types = ["started", "stopped"]

    def __init__(self, entry: Ps5HassConfigEntry) -> None:
        super().__init__(entry, "app")
        self._last: Any = _UNSET  # running app dict of the last update

    def _data(self, app: dict[str, Any]) -> dict[str, Any]:
        meta = app_by_id(self.data.apps.data, app["title_id"])
        return {
            "title_id": app["title_id"],
            "name": app["name"],
            "kind": meta["kind"] if meta else None,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.payload_up:
            current = self.running
            last = self._last
            self._last = current
            if last is not _UNSET:
                last_id = last["title_id"] if last else None
                current_id = current["title_id"] if current else None
                if last_id != current_id:
                    if last:
                        self._trigger_event("stopped", self._data(last))
                        self.async_write_ha_state()
                    if current:
                        self._trigger_event("started", self._data(current))
        super()._handle_coordinator_update()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if self.payload_up:
            self._last = self.running
