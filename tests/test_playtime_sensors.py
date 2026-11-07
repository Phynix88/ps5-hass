"""Play time sensors: today, this week, one per game."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.ps5_hass.api import CannotConnect
from custom_components.ps5_hass.playtime import week_request_start

from .conftest import entry_for
from .helpers import tick

BERLIN = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=BERLIN)  # a Wednesday
TODAY = "sensor.ps5_playtime_today"
WEEK = "sensor.ps5_playtime_week"


def _ts(*args: int) -> int:
    return int(datetime(*args, tzinfo=BERLIN).timestamp())


def _title(title_id: str, name: str, kind: str = "game", total_s: int = 0, console_s=None) -> dict:
    return {"title_id": title_id, "name": name, "kind": kind, "total_s": total_s,
            "console_total_s": console_s}


async def _setup(hass: HomeAssistant, fake, freezer):
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(NOW)
    fake.playtime_titles = [
        _title("PPSA00001", "Orbit Racer", total_s=5400, console_s=7200),
        _title("PPSA00002", "Garden Quest", total_s=600),
        _title("PPSA00003", "Media Hub", kind="app", total_s=9000),
    ]
    fake.playtime_sessions = [
        {"title_id": "PPSA00001", "kind": "game", "start": _ts(2026, 11, 2, 20), "duration_s": 3600},
        {"title_id": "PPSA00001", "kind": "game", "start": _ts(2026, 11, 4, 9), "duration_s": 1200},
        {"title_id": "PPSA00003", "kind": "app", "start": _ts(2026, 11, 4, 10), "duration_s": 3000},
    ]
    fake.playtime_current = {"title_id": "PPSA00002", "kind": "game", "start": _ts(2026, 11, 4, 11),
                             "duration_s": 600}
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_sensors_created_per_game(hass: HomeAssistant, fake, freezer) -> None:
    entry = await _setup(hass, fake, freezer)
    today = hass.states.get(TODAY)
    assert today.state == "30.0"  # 20 min + the running 10 min; the app does not count
    assert today.attributes["unit_of_measurement"] == "min"
    assert today.attributes["device_class"] == "duration"
    assert today.attributes["state_class"] == "total_increasing"
    assert hass.states.get(WEEK).state == "90.0"  # + 60 min on Monday
    orbit = hass.states.get("sensor.ps5_playtime_ppsa00001")
    assert orbit.state == "90.0"
    assert orbit.attributes["friendly_name"] == "PS5 Orbit Racer"
    assert orbit.attributes["state_class"] == "total_increasing"
    assert hass.states.get("sensor.ps5_playtime_ppsa00002").state == "10.0"
    assert hass.states.get("sensor.ps5_playtime_ppsa00003") is None  # apps get no sensor

    registry = er.async_get(hass)
    ids = {e.unique_id for e in registry.entities.values() if "_playtime_" in e.unique_id}
    assert ids == {f"{fake.device_id}_playtime_{k}" for k in ("today", "week", "PPSA00001", "PPSA00002")}

    # a game first played later gets its sensor; repeated updates and a reload add none
    fake.playtime_titles.append(_title("PPSA00004", "Star Harbor", total_s=60))
    await tick(hass, freezer, 61)
    assert hass.states.get("sensor.ps5_playtime_ppsa00004").state == "1.0"
    await tick(hass, freezer, 61)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    await tick(hass, freezer, 61)
    ids = [e.unique_id for e in registry.entities.values() if "_playtime_" in e.unique_id]
    assert len(ids) == len(set(ids)) == 5
    assert hass.states.get("sensor.ps5_playtime_ppsa00004").state == "1.0"


async def test_console_total_attribute(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake, freezer)
    assert hass.states.get("sensor.ps5_playtime_ppsa00001").attributes["console_total_min"] == 120.0
    assert hass.states.get("sensor.ps5_playtime_ppsa00002").attributes["console_total_min"] is None


async def test_refresh_when_the_app_changes(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake, freezer)

    def asked() -> int:
        return len([c for c in fake.calls if c[1] == "/playtime"])

    before = asked()
    fake.playtime_titles[1]["total_s"] = 1200
    fake.running = ("PPSA00002", 7)
    await tick(hass, freezer, 11)  # one state poll, far less than a minute
    assert asked() == before + 1
    assert hass.states.get("sensor.ps5_playtime_ppsa00002").state == "20.0"
    # the same app again: nothing extra until a minute after that refresh
    await tick(hass, freezer, 40)
    assert asked() == before + 1
    await tick(hass, freezer, 21)
    assert asked() == before + 2


async def test_values_held_while_the_payload_is_away(hass: HomeAssistant, fake, freezer) -> None:
    entry = await _setup(hass, fake, freezer)
    client = entry.runtime_data.client
    with patch.object(client, "playtime", AsyncMock(side_effect=CannotConnect("away"))):
        await tick(hass, freezer, 61)
        assert hass.states.get(TODAY).state == "30.0"
        assert hass.states.get("sensor.ps5_playtime_ppsa00001").state == "90.0"
        # midnight passes while the console is off: today starts again, the week goes on
        freezer.move_to(datetime(2026, 11, 5, 0, 5, tzinfo=BERLIN))
        await tick(hass, freezer, 61)
        assert hass.states.get(TODAY).state == "0.0"
        assert hass.states.get(WEEK).state == "90.0"
        assert hass.states.get("sensor.ps5_playtime_ppsa00001").state == "90.0"


async def test_values_never_drop_within_a_day(hass: HomeAssistant, fake, freezer) -> None:
    """The payload may lose up to a minute (checkpoint, update, power cut): total_increasing must not drop."""
    await _setup(hass, fake, freezer)
    fake.playtime_current["duration_s"] = 540  # one minute lost
    fake.playtime_titles[0]["total_s"] = 5340
    await tick(hass, freezer, 61)
    assert hass.states.get(TODAY).state == "30.0"
    assert hass.states.get(WEEK).state == "90.0"
    assert hass.states.get("sensor.ps5_playtime_ppsa00001").state == "90.0"
    # and it grows again from the higher of the two
    fake.playtime_current["duration_s"] = 900
    fake.playtime_titles[0]["total_s"] = 5460
    await tick(hass, freezer, 61)
    assert hass.states.get(TODAY).state == "35.0"
    assert hass.states.get(WEEK).state == "95.0"
    assert hass.states.get("sensor.ps5_playtime_ppsa00001").state == "91.0"


async def test_game_total_kept_when_the_title_is_gone(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake, freezer)
    fake.playtime_titles = [t for t in fake.playtime_titles if t["title_id"] != "PPSA00001"]
    await tick(hass, freezer, 61)
    assert hass.states.get("sensor.ps5_playtime_ppsa00001").state == "90.0"


async def test_day_change_resets_today(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake, freezer)
    fake.playtime_current = None
    freezer.move_to(datetime(2026, 11, 5, 0, 5, tzinfo=BERLIN))  # Thursday
    await tick(hass, freezer, 61)
    assert hass.states.get(TODAY).state == "0.0"
    assert hass.states.get(WEEK).state == "90.0"  # the week keeps its peak


async def test_week_change_resets_week(hass: HomeAssistant, fake, freezer) -> None:
    await _setup(hass, fake, freezer)
    fake.playtime_current = None
    freezer.move_to(datetime(2026, 11, 9, 0, 5, tzinfo=BERLIN))  # Monday
    await tick(hass, freezer, 61)
    assert hass.states.get(TODAY).state == "0.0"
    assert hass.states.get(WEEK).state == "0.0"


async def test_unavailable_until_the_first_answer(hass: HomeAssistant, fake, freezer) -> None:
    with patch("custom_components.ps5_hass.api.Ps5HassClient.playtime",
               AsyncMock(side_effect=CannotConnect("away"))):
        await _setup(hass, fake, freezer)
        assert hass.states.get(TODAY).state == "unavailable"
    await tick(hass, freezer, 61)
    assert hass.states.get(TODAY).state == "30.0"


@pytest.mark.parametrize("legacy", ["legacy_v03", "legacy_v02"])
async def test_no_playtime_without_capability(hass: HomeAssistant, fake, freezer, legacy: str) -> None:
    setattr(fake, legacy, True)
    await _setup(hass, fake, freezer)
    assert hass.states.get(TODAY) is None
    assert not [c for c in fake.calls if c[1] == "/playtime"]
    fake.capabilities["playtime"] = False


def test_request_starts_a_day_before_monday() -> None:
    since = week_request_start(NOW, BERLIN)
    assert datetime.fromtimestamp(since, BERLIN) == datetime(2026, 11, 1, 0, 0, tzinfo=BERLIN)
