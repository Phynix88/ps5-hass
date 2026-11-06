"""Fan threshold (only when fan_control is allowed on the console)."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import Ps5HassConfigEntry
from .entity import Ps5HassEntity
from .fan import FAN_MAX, FAN_MIN, fan_state, set_fan


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ps5HassConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    if entry.runtime_data.info.get("capabilities", {}).get("fan_control"):
        async_add_entities([Ps5FanThreshold(entry)])


class Ps5FanThreshold(Ps5HassEntity, NumberEntity):
    """Temperature above which the system fan speeds up. Unknown = console's own."""

    _attr_translation_key = "fan_threshold"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = FAN_MIN
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    def __init__(self, entry: Ps5HassConfigEntry) -> None:
        super().__init__(entry, "fan_threshold")

    @property
    def native_max_value(self) -> float:
        stock = fan_state(self).get("stock_c")
        return min(FAN_MAX, stock) if stock else FAN_MAX

    @property
    def native_value(self) -> float | None:
        return fan_state(self).get("target_c")

    async def async_set_native_value(self, value: float) -> None:
        await set_fan(self, int(value))
