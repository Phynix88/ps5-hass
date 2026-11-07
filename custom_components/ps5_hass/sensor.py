"""Sensors: temperatures, free storage, drives, SoC power, uptime, fan, running app."""

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
from homeassistant.const import (
    EntityCategory,
    UnitOfInformation,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import Ps5HassConfigEntry, app_by_id
from .entity import Ps5HassEntity, Ps5PlaytimeEntity
from .playtime import games


def _free_gib(state: dict[str, Any]) -> float | None:
    return _gib(state["storage"]["data_free_mb"])


def _soc_watts(state: dict[str, Any]) -> float | None:
    mw = (state.get("soc_power") or {}).get("total_mw")
    return None if mw is None else round(mw / 1000, 1)


def _gib(mb: int | None) -> float | None:
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
        key="fan_threshold_current",
        translation_key="fan_threshold_current",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value=lambda s: (s.get("fan") or {}).get("current_c"),
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


SOC_POWER = Ps5SensorDescription(
    key="soc_power",
    translation_key="soc_power",
    device_class=SensorDeviceClass.POWER,
    state_class=SensorStateClass.MEASUREMENT,
    native_unit_of_measurement=UnitOfPower.WATT,
    suggested_display_precision=1,
    value=_soc_watts,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ps5HassConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    entities: list[SensorEntity] = [Ps5Sensor(entry, d) for d in SENSORS]
    entities.append(Ps5RunningAppSensor(entry))
    entities.append(Ps5PowerSensor(entry))
    entities.append(Ps5PayloadsSensor(entry))
    if entry.runtime_data.info.get("capabilities", {}).get("soc_power"):
        entities.append(Ps5SocPowerSensor(entry, SOC_POWER))
    async_add_entities(entities)

    # Drives come and go with the console: a sensor is created when its name first
    # shows up in /state and stays (unavailable while the drive is missing).
    known: set[str] = set()

    @callback
    def add_new_drives() -> None:
        console = entry.runtime_data.state.data
        drives = console.state.get("drives") if console and console.payload else None
        new = [d["name"] for d in drives or [] if d["name"] not in known]
        known.update(new)
        if new:
            async_add_entities(Ps5DriveSensor(entry, name) for name in new)

    entry.async_on_unload(entry.runtime_data.state.async_add_listener(add_new_drives))
    add_new_drives()

    playtime = entry.runtime_data.playtime
    if playtime is None:
        return
    async_add_entities([Ps5PlaytimeSensor(entry, "today"), Ps5PlaytimeSensor(entry, "week")])

    # One sensor per game, created when the game first shows up in /playtime.
    known_games: set[str] = set()

    @callback
    def add_new_games() -> None:
        new = [g["title_id"] for g in games(playtime.data) if g["title_id"] not in known_games]
        known_games.update(new)
        if new:
            async_add_entities(Ps5GamePlaytimeSensor(entry, title_id) for title_id in new)

    entry.async_on_unload(playtime.async_add_listener(add_new_games))
    add_new_games()


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


class Ps5SocPowerSensor(Ps5Sensor):
    """Power drawn by the chip, from the console's own sensor."""

    _attr_extra_state_attributes = {"note": "SoC only, not the wall socket"}


class Ps5DriveSensor(Ps5HassEntity, SensorEntity):
    """Free space of one external or extended drive (m2, extended, usb0..usb7)."""

    _attr_device_class = SensorDeviceClass.DATA_SIZE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfInformation.GIBIBYTES
    _attr_suggested_display_precision = 1

    def __init__(self, entry: Ps5HassConfigEntry, name: str) -> None:
        super().__init__(entry, f"drive_{name}")
        self._drive = name
        if name == "m2":
            self._attr_translation_key = "drive_m2"
        elif name == "extended":
            self._attr_translation_key = "drive_extended"
        elif name.startswith("usb") and name[3:].isdigit():
            self._attr_translation_key = "drive_usb"
            self._attr_translation_placeholders = {"n": name[3:]}
        else:
            self._attr_translation_key = "drive_other"
            self._attr_translation_placeholders = {"name": name}

    @property
    def suggested_object_id(self) -> str:
        return f"storage free {self._drive}"  # sensor.ps5_storage_free_m2

    def _info(self) -> dict[str, Any] | None:
        if not self.payload_up:
            return None
        drives = self.coordinator.data.state.get("drives") or []
        return next((d for d in drives if d["name"] == self._drive), None)

    @property
    def available(self) -> bool:
        return super().available and self._info() is not None

    @property
    def native_value(self) -> float | None:
        info = self._info()
        return _gib(info["free_mb"]) if info else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        info = self._info()
        return {"total_gib": _gib(info["total_mb"]) if info else None}


class _PlaytimeMinutes(Ps5PlaytimeEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_suggested_display_precision = 0


class Ps5PlaytimeSensor(_PlaytimeMinutes):
    """Game time today or this week, in HA's time zone (resets at midnight / on Monday)."""

    def __init__(self, entry: Ps5HassConfigEntry, period: str) -> None:
        super().__init__(entry, f"playtime_{period}")
        self._period = period
        self._attr_translation_key = f"playtime_{period}"

    @property
    def suggested_object_id(self) -> str:
        return f"playtime {self._period}"  # sensor.ps5_playtime_today

    @property
    def native_value(self) -> float | None:
        seconds = (self.coordinator.data or {}).get(f"{self._period}_s")
        return None if seconds is None else round(seconds / 60, 1)


class Ps5GamePlaytimeSensor(_PlaytimeMinutes):
    """Total play time of one game; long-term statistics give the per-day and per-week values."""

    def __init__(self, entry: Ps5HassConfigEntry, title_id: str) -> None:
        super().__init__(entry, f"playtime_{title_id}")
        self._title_id = title_id

    @property
    def suggested_object_id(self) -> str:
        return f"playtime {self._title_id.lower()}"  # language independent

    def _title(self) -> dict[str, Any] | None:
        return next((t for t in games(self.coordinator.data) if t["title_id"] == self._title_id), None)

    @property
    def name(self) -> str:
        title = self._title()
        return title["name"] if title else self._title_id

    @property
    def native_value(self) -> float | None:
        title = self._title()
        return None if title is None else round(title["total_s"] / 60, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        title = self._title()
        console_s = title.get("console_total_s") if title else None
        return {
            "title_id": self._title_id,
            "console_total_min": None if console_s is None else round(console_s / 60, 1),
        }


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


class Ps5PowerSensor(Ps5HassEntity, SensorEntity):
    """on / standby (rest mode) / off, also while the payload is away."""

    _attr_translation_key = "power"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["on", "standby", "off"]

    def __init__(self, entry: Ps5HassConfigEntry) -> None:
        super().__init__(entry, "power")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        return data.power if data else None


class Ps5PayloadsSensor(Ps5HassEntity, SensorEntity):
    """Number of running payloads; the names as attribute."""

    _attr_translation_key = "payloads"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, entry: Ps5HassConfigEntry) -> None:
        super().__init__(entry, "payloads")

    def _names(self) -> list[str] | None:
        if not self.payload_up:
            return None
        names = self.coordinator.data.state.get("payloads")
        return None if names is None else list(names)  # None: payload older than v0.2

    @property
    def native_value(self) -> int | None:
        names = self._names()
        return None if names is None else len(names)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"names": self._names() or []}
