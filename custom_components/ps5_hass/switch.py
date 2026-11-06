"""Fan control on/off: off = the console's own threshold."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .coordinator import Ps5HassConfigEntry
from .entity import Ps5HassEntity
from .fan import DEFAULT_TARGET, FAN_MAX, fan_state, set_fan


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ps5HassConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if entry.runtime_data.info.get("capabilities", {}).get("fan_control"):
        async_add_entities([Ps5FanControl(entry)])


class Ps5FanControl(Ps5HassEntity, SwitchEntity, RestoreEntity):
    """On = our threshold is applied; turning on uses the last one again."""

    _attr_translation_key = "fan_control"

    def __init__(self, entry: Ps5HassConfigEntry) -> None:
        super().__init__(entry, "fan_control")
        self._last_target: int = DEFAULT_TARGET

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and isinstance(last.attributes.get("last_target_c"), int):
            self._last_target = last.attributes["last_target_c"]

    @property
    def is_on(self) -> bool | None:
        target = fan_state(self).get("target_c")
        if target:
            self._last_target = target
        return target is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_target_c": self._last_target}

    async def async_turn_on(self, **kwargs: Any) -> None:
        stock = fan_state(self).get("stock_c")
        limit = min(FAN_MAX, stock) if stock else FAN_MAX
        await set_fan(self, min(self._last_target, limit))  # never above the factory value

    async def async_turn_off(self, **kwargs: Any) -> None:
        await set_fan(self, 0)
