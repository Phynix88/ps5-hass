"""Sensors."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import entry_for
from .helpers import tick, tick_until

DDP = "custom_components.ps5_hass.coordinator.ddp_status"


async def _setup(hass, fake):
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensor_values_and_units(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake)
    cpu = hass.states.get("sensor.ps5_cpu_temperature")
    assert cpu.state == "41" and cpu.attributes["unit_of_measurement"] == "°C"
    assert hass.states.get("sensor.ps5_soc_temperature").state == "38"
    free = hass.states.get("sensor.ps5_storage_free")
    assert free.state == "74.9" and free.attributes["unit_of_measurement"] == "GiB"
    app = hass.states.get("sensor.ps5_running_app")
    assert app.state == "unknown"
    fake.running = ("PPSA00001", 24)
    await tick(hass, freezer, 11)
    app = hass.states.get("sensor.ps5_running_app")
    assert app.state == "Test Game One"
    assert app.attributes["title_id"] == "PPSA00001"
    assert app.attributes["kind"] == "game"


async def test_sensor_null_is_unknown(hass, fake, freezer) -> None:
    await _setup(hass, fake)
    fake.state_override = {"temps": {"cpu": None, "soc": 38}}
    await tick(hass, freezer, 11)
    assert hass.states.get("sensor.ps5_cpu_temperature").state == "unknown"


async def test_disabled_by_default(hass, fake) -> None:
    await _setup(hass, fake)
    registry = er.async_get(hass)
    for entity_id in ("sensor.ps5_uptime", "sensor.ps5_fan_raw"):
        reg = registry.async_get(entity_id)
        assert reg is not None and reg.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_sensors_unavailable_when_payload_down(hass, fake, freezer) -> None:
    await _setup(hass, fake)
    fake.down = True
    with patch(DDP, AsyncMock(return_value="standby")):
        await tick_until(
            hass, freezer, lambda: hass.states.get("sensor.ps5_cpu_temperature").state == "unavailable"
        )
    assert hass.states.get("sensor.ps5_storage_free").state == "unavailable"


async def test_power_state_rest_shows_standby_immediately(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake)
    assert hass.states.get("sensor.ps5_power").state == "on"
    for power_state in ("going_to_rest", "rest"):
        fake.power_state = "on"
        await tick(hass, freezer, 11)
        fake.power_state = power_state
        await tick(hass, freezer, 11)  # one poll, no 30 s hold, the payload still answers
        assert hass.states.get("sensor.ps5_power").state == "standby", power_state
        assert hass.states.get("media_player.ps5").state == "off", power_state
    fake.power_state = "unknown"
    await tick(hass, freezer, 11)
    assert hass.states.get("sensor.ps5_power").state == "on"
    fake.power_state = "on"
    await tick(hass, freezer, 11)
    assert hass.states.get("sensor.ps5_power").state == "on"
