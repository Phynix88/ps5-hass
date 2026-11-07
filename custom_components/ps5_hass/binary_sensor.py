"""Which payloads run: jailbreak (kstuff), ShadowMount, FTP, websrv."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import Ps5HassConfigEntry
from .entity import Ps5HassEntity

# key -> process name reported in /state.payloads (checked on FW 13.60)
PAYLOADS = {
    "kstuff": "kstuff.elf",
    "shadowmount": "shadowmountplus.elf",
    "ftpsrv": "ftpsrv.elf",
    "websrv": "websrv.elf",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ps5HassConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities(Ps5PayloadRunning(entry, key, name) for key, name in PAYLOADS.items())


class Ps5PayloadRunning(Ps5HassEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry: Ps5HassConfigEntry, key: str, process: str) -> None:
        super().__init__(entry, f"payload_{key}")
        self._attr_translation_key = f"payload_{key}"
        self._process = process

    @property
    def is_on(self) -> bool | None:
        names = self.payload_names  # None: unknown, not "not running"
        return None if names is None else self._process in names
