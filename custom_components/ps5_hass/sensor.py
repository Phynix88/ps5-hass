"""Sensors: temperatures, free storage, uptime, fan, running app."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfInformation, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import Ps5HassConfigEntry, app_by_id
from .entity import Ps5HassEntity


def _free_gib(state: dict[str, Any]) -> float | None:
    mb = state["storage"]["data_free_mb"]
    return None if mb is None else round(mb / 1024, 1)


@dataclass(frozen=True, kw_only=True)
class Ps5SensorDescription(SensorEntityDescription):
    value: Callable[[dict[str, Any]], Any]


SENSORS = (
    Ps5SensorDescription(
        key="cpu_temperature",
        translation_key="cpu_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value=lambda s: s["temps"]["cpu"],
    ),
    Ps5SensorDescription(
        key="soc_temperature",
        translation_key="soc_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value=lambda s: s["temps"]["soc"],
    ),
    Ps5SensorDescription(
        key="storage_free",
        translation_key="storage_free",
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfInformation.GIBIBYTES,
        suggested_display_precision=1,
        value=_free_gib,
    ),
    Ps5SensorDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda s: s["uptime_s"],
    ),
    Ps5SensorDescription(
        key="fan_raw",
        translation_key="fan_raw",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value=lambda s: s["fan_raw"],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ps5HassConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    entities: list[SensorEntity] = [Ps5Sensor(entry, d) for d in SENSORS]
    entities.append(Ps5RunningAppSensor(entry))
    async_add_entities(entities)


class Ps5Sensor(Ps5HassEntity, SensorEntity):
    entity_description: Ps5SensorDescription

    def __init__(self, entry: Ps5HassConfigEntry, description: Ps5SensorDescription) -> None:
        super().__init__(entry, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        if not self.payload_up:
            return None
        return self.entity_description.value(self.coordinator.data.state)


class Ps5RunningAppSensor(Ps5HassEntity, SensorEntity):
    _attr_translation_key = "running_app"

    def __init__(self, entry: Ps5HassConfigEntry) -> None:
        super().__init__(entry, "running_app")

    @property
    def native_value(self) -> str | None:
        app = self.running
        return app["name"] if app else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        app = self.running
        if not app:
            return {"title_id": None, "kind": None}
        meta = app_by_id(self.data.apps.data, app["title_id"])
        return {"title_id": app["title_id"], "kind": meta["kind"] if meta else None}
