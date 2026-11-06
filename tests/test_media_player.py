"""The media player: states, sources, actions, cover."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .conftest import entry_for
from .fake_payload import FakePayload
from .helpers import tick, tick_until

ENTITY = "media_player.ps5"
DDP = "custom_components.ps5_hass.coordinator.ddp_status"


async def _setup(hass: HomeAssistant, fake: FakePayload, **options):
    entry = entry_for(fake, **options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _call(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        "media_player", service, {"entity_id": ENTITY, **data}, blocking=True
    )


async def test_states_for_each_console_state(hass, fake, freezer) -> None:
    await _setup(hass, fake)
    assert hass.states.get(ENTITY).state == "idle"
    fake.running = ("PPSA00001", 24)
    await tick(hass, freezer, 11)
    st = hass.states.get(ENTITY)
    assert st.state == "playing"
    assert st.attributes["source"] == "Test Game One"
    assert st.attributes["media_title"] == "Test Game One"
    assert st.attributes["media_content_id"] == "PPSA00001"
    assert st.attributes["media_content_type"] == "game"
    ddp = AsyncMock(return_value="standby")
    fake.down = True
    with patch(DDP, ddp):
        # rest mode: media player off (STANDBY is deprecated), power sensor "standby"
        await tick_until(hass, freezer, lambda: hass.states.get("sensor.ps5_power").state == "standby")
        assert hass.states.get(ENTITY).state == "off"
        ddp.return_value = "on"
        await tick_until(hass, freezer, lambda: hass.states.get(ENTITY).state == "on")
        ddp.return_value = "off"
        await tick_until(hass, freezer, lambda: hass.states.get(ENTITY).state == "off")


async def test_source_list_games_first(hass, fake) -> None:
    await _setup(hass, fake)
    assert hass.states.get(ENTITY).attributes["source_list"] == [
        "Another Game",
        "Test Game One",
        "Hikari",
        "Orbit Store (ORBT00001)",
        "Orbit Store (PPSA99177)",
        "RetroArch",
    ]


async def test_duplicate_names_unique(hass, fake, freezer) -> None:
    await _setup(hass, fake)
    await _call(hass, "select_source", source="Orbit Store (PPSA99177)")
    assert fake.running[0] == "PPSA99177"
    await tick(hass, freezer, 11)
    assert hass.states.get(ENTITY).attributes["source"] == "Orbit Store (PPSA99177)"


async def test_only_games_option(hass, fake) -> None:
    await _setup(hass, fake, sources="games")
    assert hass.states.get(ENTITY).attributes["source_list"] == ["Another Game", "Test Game One"]


async def test_select_source_launches_with_replace(hass, fake) -> None:
    await _setup(hass, fake)
    fake.running = ("PPSA00001", 24)
    await _call(hass, "select_source", source="Hikari")
    assert fake.running[0] == "PPSA96708"
    assert fake.calls[-2][3] == {"replace_running": True}  # last call is the refresh


async def test_select_source_when_off_raises(hass, fake, freezer) -> None:
    await _setup(hass, fake)
    fake.down = True
    with patch(DDP, AsyncMock(return_value="standby")):
        await tick_until(hass, freezer, lambda: hass.states.get(ENTITY).state == "off")
        with pytest.raises(HomeAssistantError) as err:
            await _call(hass, "select_source", source="Hikari")
    assert err.value.translation_key == "console_off"


async def test_select_source_not_installed_error(hass, fake) -> None:
    await _setup(hass, fake)
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "select_source", source="Nope")
    assert err.value.translation_key == "unknown_source"
    fake.apps = [a for a in fake.apps if a["title_id"] != "PPSA96708"]
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "select_source", source="Hikari")
    assert err.value.translation_key == "not_installed"


async def test_system_error_shows_rc_hex(hass, fake) -> None:
    await _setup(hass, fake)
    fake.fail["/apps/PPSA96708/launch"] = (
        500, {"error": "system_error", "rc": -2137784315, "rc_hex": "0x80940005"})
    with pytest.raises(HomeAssistantError) as err:
        await _call(hass, "select_source", source="Hikari")
    assert err.value.translation_key == "system_error"
    assert err.value.translation_placeholders == {"rc_hex": "0x80940005"}


async def test_media_stop_closes(hass, fake, freezer) -> None:
    await _setup(hass, fake)
    fake.running = ("PPSA00001", 24)
    await tick(hass, freezer, 11)
    await _call(hass, "media_stop")
    assert fake.running is None
    assert hass.states.get(ENTITY).state == "idle"


async def test_turn_off_only_with_power_actions(hass, fake) -> None:
    await _setup(hass, fake)
    features = hass.states.get(ENTITY).attributes["supported_features"]
    assert not features & MediaPlayerEntityFeature.TURN_OFF
    assert features & MediaPlayerEntityFeature.SELECT_SOURCE
    assert features & MediaPlayerEntityFeature.STOP


async def test_turn_off_with_power_actions(hass, fake) -> None:
    fake.power_actions = True
    await _setup(hass, fake)
    features = hass.states.get(ENTITY).attributes["supported_features"]
    assert features & MediaPlayerEntityFeature.TURN_OFF
    await _call(hass, "turn_off")
    assert fake.standby_calls == 1


async def test_media_image_from_icon(hass, fake, freezer, hass_client) -> None:
    await _setup(hass, fake)
    assert "entity_picture" not in hass.states.get(ENTITY).attributes
    fake.running = ("PPSA00001", 24)
    await tick(hass, freezer, 11)
    picture = hass.states.get(ENTITY).attributes["entity_picture"]
    assert picture.startswith("/api/media_player_proxy/media_player.ps5")
    client = await hass_client()
    resp = await client.get(picture)
    assert resp.status == 200
    assert (await resp.read()).startswith(b"\x89PNG")
