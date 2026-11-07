"""v0.4 entities: keep awake, SoC power, drives; none of them for an older payload."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .conftest import entry_for
from .helpers import tick

KEEP_AWAKE = "switch.ps5_keep_awake"
SOC_POWER = "sensor.ps5_soc_power"
V04_ENTITIES = (KEEP_AWAKE, SOC_POWER, "button.ps5_minimize")


async def _setup(hass: HomeAssistant, fake):
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_keep_awake_switch_roundtrip(hass: HomeAssistant, fake) -> None:
    await _setup(hass, fake)
    assert hass.states.get(KEEP_AWAKE).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": KEEP_AWAKE}, blocking=True)
    assert fake.keep_awake is True
    assert hass.states.get(KEEP_AWAKE).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": KEEP_AWAKE}, blocking=True)
    assert fake.keep_awake is False
    assert hass.states.get(KEEP_AWAKE).state == "off"


async def test_keep_awake_not_on(hass: HomeAssistant, fake) -> None:
    await _setup(hass, fake)
    fake.power_state = "rest"
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("switch", "turn_on", {"entity_id": KEEP_AWAKE}, blocking=True)
    assert err.value.translation_key == "not_on"
    assert fake.keep_awake is False


async def test_soc_power_watts(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake)
    st = hass.states.get(SOC_POWER)
    assert st.state == "48.0"
    assert st.attributes["unit_of_measurement"] == "W"
    assert st.attributes["device_class"] == "power"
    assert st.attributes["state_class"] == "measurement"
    assert st.attributes["note"] == "SoC only, not the wall socket"
    fake.soc_power = {"total_mw": 60123, "rails": []}
    await tick(hass, freezer, 11)
    assert hass.states.get(SOC_POWER).state == "60.1"
    fake.soc_power = None
    await tick(hass, freezer, 11)
    assert hass.states.get(SOC_POWER).state == "unknown"


async def test_drive_sensor_created_on_first_seen(hass: HomeAssistant, fake, freezer) -> None:
    entry = await _setup(hass, fake)
    registry = er.async_get(hass)
    assert not [e for e in registry.entities.values() if "_drive_" in e.unique_id]
    fake.drives = [
        {"name": "m2", "free_mb": 512000, "total_mb": 1024000},
        {"name": "usb0", "free_mb": 2048, "total_mb": 4096},
    ]
    await tick(hass, freezer, 11)
    m2 = hass.states.get("sensor.ps5_storage_free_m2")
    assert m2.state == "500.0" and m2.attributes["unit_of_measurement"] == "GiB"
    assert m2.attributes["friendly_name"] == "PS5 M.2 storage free"
    assert m2.attributes["total_gib"] == 1000.0
    usb = hass.states.get("sensor.ps5_storage_free_usb0")
    assert usb.state == "2.0" and usb.attributes["friendly_name"] == "PS5 USB 0 storage free"
    entity = registry.async_get("sensor.ps5_storage_free_m2")
    assert entity.unique_id == f"{fake.device_id}_drive_m2"
    # one drive pulled: unavailable, the other stays; plugged in again: same entity
    fake.drives = [{"name": "usb0", "free_mb": 1024, "total_mb": 4096}]
    await tick(hass, freezer, 11)
    assert hass.states.get("sensor.ps5_storage_free_m2").state == "unavailable"
    assert hass.states.get("sensor.ps5_storage_free_usb0").state == "1.0"
    fake.drives = [{"name": "m2", "free_mb": 1024000, "total_mb": 1024000},
                   {"name": "usb0", "free_mb": 1024, "total_mb": 4096}]
    await tick(hass, freezer, 11)
    assert hass.states.get("sensor.ps5_storage_free_m2").state == "1000.0"
    assert len([e for e in registry.entities.values() if e.unique_id.endswith("_drive_m2")]) == 1
    # a drive that is present at setup is created there
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.ps5_storage_free_m2").state == "1000.0"


async def test_extended_drive_name(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake)
    fake.drives = [{"name": "extended", "free_mb": 1024, "total_mb": 2048}]
    await tick(hass, freezer, 11)
    st = hass.states.get("sensor.ps5_storage_free_extended")
    assert st.attributes["friendly_name"] == "PS5 Extended storage free"


@pytest.mark.parametrize("legacy", ["legacy_v03", "legacy_v02"])
async def test_no_entities_without_capabilities(hass: HomeAssistant, fake, legacy: str) -> None:
    setattr(fake, legacy, True)
    await _setup(hass, fake)
    for entity_id in V04_ENTITIES:
        assert hass.states.get(entity_id) is None, entity_id
    assert not [c for c in fake.calls if c[1] in ("/keep_awake", "/apps/running/suspend")]
    assert not [e for e in er.async_get(hass).entities.values() if "_drive_" in e.unique_id]


async def test_single_capabilities(hass: HomeAssistant, fake) -> None:
    fake.capabilities["keep_awake"] = False
    fake.capabilities["suspend"] = False
    await _setup(hass, fake)
    assert hass.states.get(KEEP_AWAKE) is None
    assert hass.states.get("button.ps5_minimize") is None
    assert hass.states.get(SOC_POWER) is not None
