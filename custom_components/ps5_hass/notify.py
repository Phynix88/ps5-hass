"""Messages on the TV: notify.ps5."""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity, NotifyEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Ps5HassError
from .coordinator import Ps5HassConfigEntry
from .entity import Ps5HassEntity, raise_api_error


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ps5HassConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([Ps5Notify(entry)])


class Ps5Notify(Ps5HassEntity, NotifyEntity):
    """Shows a text as a notification on the console's screen."""

    _attr_name = None
    _attr_supported_features = NotifyEntityFeature.TITLE

    def __init__(self, entry: Ps5HassConfigEntry) -> None:
        super().__init__(entry, "notify")

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        text = f"{title}: {message}" if title else message
        try:
            await self.data.client.notify(text)
        except Ps5HassError as err:
            raise_api_error(err)
