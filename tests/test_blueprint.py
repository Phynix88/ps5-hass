"""The play time limit blueprint: it loads with HA's schema and does what its comments say."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from homeassistant.components.automation.config import AUTOMATION_BLUEPRINT_SCHEMA
from homeassistant.components.blueprint import models
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import yaml as yaml_util
from pytest_homeassistant_custom_component.common import async_mock_service

BLUEPRINT = Path(__file__).parent.parent / "blueprints/automation/ps5_hass/playtime_limit.yaml"

SENSOR = "sensor.fake_playtime"
NOTIFY = "notify.fake_tv"
CLOSE = "button.fake_close"
MINIMIZE = "button.fake_minimize"
REST = "button.fake_rest"
BUTTONS = {"close": CLOSE, "minimize": MINIMIZE, "rest": REST}

INPUTS = {
    "playtime_sensor",
    "limit_minutes",
    "warn_minutes",
    "notify_entity",
    "action",
    "close_button",
    "minimize_button",
    "rest_button",
}


def test_blueprint_loads_with_ha_schema() -> None:
    blueprint = models.Blueprint(
        yaml_util.load_yaml(str(BLUEPRINT)),
        expected_domain="automation",
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )
    assert not blueprint.validate()
    assert INPUTS <= set(blueprint.inputs)
    assert blueprint.inputs["limit_minutes"]["default"] == 120
    assert blueprint.inputs["warn_minutes"]["default"] == 15
    options = blueprint.inputs["action"]["selector"]["select"]["options"]
    assert {o["value"] for o in options} == {"close", "minimize", "rest"}
    for name, domain in (
        ("close_button", "button"),
        ("minimize_button", "button"),
        ("rest_button", "button"),
        ("playtime_sensor", "sensor"),
        ("notify_entity", "notify"),
    ):
        (entity_filter,) = blueprint.inputs[name]["selector"]["entity"]["filter"]
        assert entity_filter["domain"] in (domain, [domain])


async def _setup(hass: HomeAssistant, tmp_path: Path, **inputs):
    """The blueprint in a scratch config dir, one automation made from it, the services mocked."""
    target = tmp_path / "blueprints/automation/ps5_hass/playtime_limit.yaml"
    target.parent.mkdir(parents=True)
    shutil.copy(BLUEPRINT, target)
    hass.config.config_dir = str(tmp_path)
    presses = async_mock_service(hass, "button", "press")
    messages = async_mock_service(hass, "notify", "send_message")
    hass.states.async_set(SENSOR, "0.0")
    config = {
        "playtime_sensor": SENSOR,
        "limit_minutes": 120,
        "warn_minutes": 15,
        "notify_entity": NOTIFY,
        "action": "close",
        "close_button": CLOSE,
        "minimize_button": MINIMIZE,
        "rest_button": REST,
        **inputs,
    }
    assert await async_setup_component(
        hass,
        "automation",
        {"automation": {"use_blueprint": {"path": "ps5_hass/playtime_limit.yaml", "input": config}}},
    )
    await hass.async_block_till_done()
    return presses, messages


async def _minutes(hass: HomeAssistant, value: str) -> None:
    hass.states.async_set(SENSOR, value)
    await hass.async_block_till_done()


def _pressed(presses) -> list[str]:
    return [e for c in presses for e in _ids(c)]


def _ids(call) -> list[str]:
    ids = call.data["entity_id"]
    return [ids] if isinstance(ids, str) else list(ids)


async def test_warns_at_limit_minus_warn_minutes(hass: HomeAssistant, tmp_path: Path) -> None:
    presses, messages = await _setup(hass, tmp_path)
    for value in ("30.0", "104.9"):
        await _minutes(hass, value)
    assert not messages
    await _minutes(hass, "105.0")
    assert len(messages) == 1
    assert _ids(messages[0]) == [NOTIFY]
    assert messages[0].data["title"] == "PS5"
    assert "15 minutes" in messages[0].data["message"]
    assert not presses
    await _minutes(hass, "106.0")
    assert len(messages) == 1


async def test_warning_follows_the_inputs(hass: HomeAssistant, tmp_path: Path) -> None:
    presses, messages = await _setup(hass, tmp_path, limit_minutes=60, warn_minutes=5)
    await _minutes(hass, "54.9")
    assert not messages
    await _minutes(hass, "55.0")
    assert len(messages) == 1
    assert "5 minutes" in messages[0].data["message"]


async def test_no_warning_without_warn_minutes(hass: HomeAssistant, tmp_path: Path) -> None:
    presses, messages = await _setup(hass, tmp_path, warn_minutes=0)
    await _minutes(hass, "119.0")
    assert not messages
    await _minutes(hass, "120.0")
    assert _pressed(presses) == [CLOSE]


@pytest.mark.parametrize("action", ["close", "minimize", "rest"])
async def test_runs_the_chosen_action_at_the_limit(hass: HomeAssistant, tmp_path: Path, action: str) -> None:
    presses, messages = await _setup(hass, tmp_path, action=action)
    await _minutes(hass, "105.0")
    assert len(messages) == 1  # the warning
    await _minutes(hass, "119.0")
    assert not presses
    await _minutes(hass, "120.0")
    assert _pressed(presses) == [BUTTONS[action]]
    assert len(messages) == 1  # the crossing itself says nothing more


async def test_no_second_warning_the_same_day(hass: HomeAssistant, tmp_path: Path) -> None:
    presses, messages = await _setup(hass, tmp_path)
    await _minutes(hass, "105.0")
    assert len(messages) == 1
    # the sensor drops out and comes back (restart, Wi-Fi), values wobble, attributes change
    hass.states.async_set(SENSOR, "unavailable")
    await hass.async_block_till_done()
    await _minutes(hass, "108.0")
    hass.states.async_set(SENSOR, "108.0", {"unit_of_measurement": "min"})
    await hass.async_block_till_done()
    await _minutes(hass, "unknown")
    await _minutes(hass, "110.0")
    assert len(messages) == 1
    assert not presses


async def test_warns_again_on_the_next_day(hass: HomeAssistant, tmp_path: Path) -> None:
    presses, messages = await _setup(hass, tmp_path)
    await _minutes(hass, "105.0")
    await _minutes(hass, "120.0")
    assert len(messages) == 1
    assert _pressed(presses) == [CLOSE]
    await _minutes(hass, "0.0")  # midnight: the sensor starts again
    await _minutes(hass, "105.0")
    assert len(messages) == 2
    await _minutes(hass, "120.0")
    assert _pressed(presses) == [CLOSE, CLOSE]


async def test_action_again_when_a_game_starts_after_the_limit(hass: HomeAssistant, tmp_path: Path) -> None:
    presses, messages = await _setup(hass, tmp_path)
    await _minutes(hass, "119.5")
    await _minutes(hass, "120.0")
    assert _pressed(presses) == [CLOSE]
    messages.clear()

    # the closed game's last seconds arrive late: no new action, no notice
    await _minutes(hass, "120.1")
    assert _pressed(presses) == [CLOSE]
    assert not messages

    # a new game: the sensor grows by about a minute at the next update
    await _minutes(hass, "121.1")
    assert _pressed(presses) == [CLOSE, CLOSE]
    assert len(messages) == 1
    assert _ids(messages[0]) == [NOTIFY]
    assert messages[0].data["title"] == "PS5"
    assert messages[0].data["message"] == "Play time for today is over"


async def test_jump_over_warning_and_limit_only_acts(hass: HomeAssistant, tmp_path: Path) -> None:
    presses, messages = await _setup(hass, tmp_path, action="rest")
    await _minutes(hass, "100.0")
    await _minutes(hass, "125.0")
    assert _pressed(presses) == [REST]
    assert not messages


async def test_a_sensor_that_comes_back_over_the_limit_does_nothing(hass: HomeAssistant, tmp_path: Path) -> None:
    """Restart of Home Assistant: no warning and no action for a value that was already there."""
    presses, messages = await _setup(hass, tmp_path)
    hass.states.async_set(SENSOR, "unavailable")
    await hass.async_block_till_done()
    await _minutes(hass, "130.0")
    assert not presses
    assert not messages


async def test_messages_can_be_changed(hass: HomeAssistant, tmp_path: Path) -> None:
    presses, messages = await _setup(
        hass,
        tmp_path,
        warn_message="Noch {{ warn_minutes | int }} Minuten",
        over_message="Spielzeit für heute vorbei",
    )
    await _minutes(hass, "105.0")
    assert messages[0].data["message"] == "Noch 15 Minuten"
    await _minutes(hass, "120.0")
    await _minutes(hass, "121.0")
    assert messages[1].data["message"] == "Spielzeit für heute vorbei"


async def test_action_without_its_button_presses_nothing(hass: HomeAssistant, tmp_path: Path, caplog) -> None:
    presses, messages = await _setup(hass, tmp_path, action="rest", rest_button="")
    await _minutes(hass, "119.0")
    await _minutes(hass, "120.0")
    assert not presses
    assert "button for the chosen action is not set" in caplog.text
