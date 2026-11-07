"""v0.2 entities: payload sensors, power off, fan control."""

from __future__ import annotations

import json
import pathlib

import pytest
from homeassistant.exceptions import HomeAssistantError

from .conftest import entry_for
from .helpers import tick

COMP = pathlib.Path(__file__).parent.parent / "custom_components" / "ps5_hass"


async def _setup(hass, fake):
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_payload_sensors(hass, fake, freezer) -> None:
    await _setup(hass, fake)
    st = hass.states.get("sensor.ps5_payloads")
    assert st.state == "4"
    assert st.attributes["names"] == ["ftpsrv.elf", "kstuff.elf", "shadowmountplus.elf", "websrv.elf"]
    assert hass.states.get("binary_sensor.ps5_jailbreak_kstuff").state == "on"
    assert hass.states.get("binary_sensor.ps5_shadowmount").state == "on"
    assert hass.states.get("binary_sensor.ps5_ftp_server").state == "on"
    assert hass.states.get("binary_sensor.ps5_websrv").state == "on"
    fake.payloads = ["kstuff.elf"]
    await tick(hass, freezer, 11)
    assert hass.states.get("binary_sensor.ps5_ftp_server").state == "off"
    assert hass.states.get("sensor.ps5_payloads").state == "1"


async def test_power_off_and_fan_absent_without_permission(hass, fake) -> None:
    await _setup(hass, fake)
    for entity_id in ("button.ps5_power_off", "number.ps5_fan_threshold", "switch.ps5_fan_control"):
        assert hass.states.get(entity_id) is None, entity_id
    # the current threshold is readable without permission
    assert hass.states.get("sensor.ps5_fan_threshold_current").state == "91"


async def test_power_off_button(hass, fake) -> None:
    fake.power_off = True
    await _setup(hass, fake)
    await hass.services.async_call("button", "press", {"entity_id": "button.ps5_power_off"}, blocking=True)
    assert fake.power_off_calls == 1


async def test_fan_number_and_switch(hass, fake) -> None:
    fake.fan_control = True
    await _setup(hass, fake)
    number = hass.states.get("number.ps5_fan_threshold")
    assert number.attributes["min"] == 45 and number.attributes["max"] == 85
    assert number.state == "unknown"  # console's own value
    assert hass.states.get("switch.ps5_fan_control").state == "off"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.ps5_fan_threshold", "value": 72}, blocking=True)
    assert fake.fan == {"target_c": 72, "current_c": 72, "stock_c": 91}
    assert hass.states.get("number.ps5_fan_threshold").state == "72"
    assert hass.states.get("switch.ps5_fan_control").state == "on"
    assert hass.states.get("sensor.ps5_fan_threshold_current").state == "72"
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.ps5_fan_control"}, blocking=True)
    assert fake.fan["target_c"] is None and fake.fan["current_c"] == 91
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.ps5_fan_control"}, blocking=True)
    assert fake.fan["target_c"] == 72  # last value again


async def test_fan_max_capped_by_stock(hass, fake) -> None:
    fake.fan_control = True
    fake.fan = {"target_c": None, "current_c": 70, "stock_c": 70}
    await _setup(hass, fake)
    assert hass.states.get("number.ps5_fan_threshold").attributes["max"] == 70


async def test_fan_error_translated(hass, fake) -> None:
    fake.fan_control = True
    await _setup(hass, fake)
    fake.fail["/fan"] = (409, {"error": "fan_above_stock"})
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "number", "set_value", {"entity_id": "number.ps5_fan_threshold", "value": 80}, blocking=True)
    assert err.value.translation_key == "fan_above_stock"


async def test_v01_payload_shows_unknown_not_off(hass, fake) -> None:
    fake.legacy_v01 = True
    await _setup(hass, fake)
    assert hass.states.get("binary_sensor.ps5_jailbreak_kstuff").state == "unknown"
    assert hass.states.get("sensor.ps5_payloads").state == "unknown"


async def test_payloads_unknown_outside_on(hass, fake, freezer) -> None:
    """Outside "on" the payload reports no payloads by design: unknown, not "none running"."""
    await _setup(hass, fake)
    fake.power_state = "going_to_rest"
    await tick(hass, freezer, 11)
    assert hass.states.get("binary_sensor.ps5_jailbreak_kstuff").state == "unknown"
    assert hass.states.get("sensor.ps5_payloads").state == "unknown"
    fake.power_state = "unknown"  # the console has the function but no reading yet: not "on"
    await tick(hass, freezer, 11)
    assert hass.states.get("binary_sensor.ps5_jailbreak_kstuff").state == "unknown"
    fake.power_state = "on"
    await tick(hass, freezer, 11)
    assert hass.states.get("binary_sensor.ps5_jailbreak_kstuff").state == "on"
    assert hass.states.get("sensor.ps5_payloads").state == "4"


async def test_payloads_known_without_power_state_function(hass, fake, freezer) -> None:
    fake.capabilities["power_state"] = False
    fake.power_state = "unknown"  # such a console counts as "on"
    await _setup(hass, fake)
    assert hass.states.get("binary_sensor.ps5_jailbreak_kstuff").state == "on"
    assert hass.states.get("sensor.ps5_payloads").state == "4"


async def test_fan_switch_on_capped_by_stock(hass, fake) -> None:
    fake.fan_control = True
    fake.fan = {"target_c": None, "current_c": 70, "stock_c": 70}
    await _setup(hass, fake)
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.ps5_fan_control"}, blocking=True)
    assert fake.fan["target_c"] == 70  # default 80 would be above this board's 70


@pytest.mark.parametrize(
    ("entity_id", "domain", "service", "data", "key"),
    [
        ("button.ps5_rest_mode", "button", "press", {}, "power_actions_disabled"),
        ("button.ps5_power_off", "button", "press", {}, "power_off_disabled"),
        ("number.ps5_fan_threshold", "number", "set_value", {"value": 72}, "fan_control_disabled"),
    ],
)
async def test_v03_payload_403_message(hass, fake, entity_id, domain, service, data, key) -> None:
    """A v0.3 payload that does not allow the action: tell the user to update it."""
    fake.power_actions = fake.power_off = fake.fan_control = True
    await _setup(hass, fake)
    fake.power_actions = fake.power_off = fake.fan_control = False
    fake.legacy_v03 = True
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            domain, service, {"entity_id": entity_id, **data}, blocking=True)
    assert err.value.translation_key == key
    for path, text in (
        ("strings.json", "Update the ps5-hass payload to v0.4; permissions are no longer needed."),
        ("translations/en.json", "Update the ps5-hass payload to v0.4; permissions are no longer needed."),
        ("translations/de.json", "Aktualisiere den ps5-hass-Payload auf v0.4; Freigaben sind nicht mehr nötig."),
    ):
        strings = json.loads((COMP / path).read_text())
        assert strings["exceptions"][key]["message"] == text, (path, key)
