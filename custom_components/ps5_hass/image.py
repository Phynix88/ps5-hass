"""Cover of the running app."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import Ps5HassError
from .coordinator import Ps5HassConfigEntry
from .entity import Ps5HassEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ps5HassConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([Ps5CoverImage(hass, entry)])


class Ps5CoverImage(Ps5HassEntity, ImageEntity):
    _attr_translation_key = "cover"

    def __init__(self, hass: HomeAssistant, entry: Ps5HassConfigEntry) -> None:
        Ps5HassEntity.__init__(self, entry, "cover")
        ImageEntity.__init__(self, hass)
        self._title_id: str | None = self._current()
        self._attr_image_last_updated = dt_util.utcnow()

    def _current(self) -> str | None:
        app = self.running
        return app["title_id"] if app else None

    @callback
    def _handle_coordinator_update(self) -> None:
        current = self._current()
        if current != self._title_id and self.payload_up:
            self._title_id = current
            self._cached_image = None
            self._attr_image_last_updated = dt_util.utcnow()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        if not self._title_id:
            return None
        try:
            data, ctype = await self.data.client.icon(self._title_id)
        except Ps5HassError:
            return None
        self._attr_content_type = ctype
        return data
