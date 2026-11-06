"""Media browser: games and apps as tiles with covers; tapping starts them."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    MediaPlayerEntityFeature,
)

from .conftest import entry_for
from .helpers import tick_until

ENTITY = "media_player.ps5"
DDP = "custom_components.ps5_hass.coordinator.ddp_status"


async def _setup(hass, fake):
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def _browse(hass_ws_client, hass, content_type=None, content_id=None):
    client = await hass_ws_client(hass)
    msg = {"id": 1, "type": "media_player/browse_media", "entity_id": ENTITY}
    if content_type:
        msg["media_content_type"] = content_type
        msg["media_content_id"] = content_id
    await client.send_json(msg)
    return await client.receive_json()


async def test_browse_root_and_children(hass, fake, hass_ws_client) -> None:
    await _setup(hass, fake)
    features = hass.states.get(ENTITY).attributes["supported_features"]
    assert features & MediaPlayerEntityFeature.BROWSE_MEDIA
    assert features & MediaPlayerEntityFeature.PLAY_MEDIA
    root = await _browse(hass_ws_client, hass)
    assert root["success"], root
    titles = [c["title"] for c in root["result"]["children"]]
    assert titles == ["Spiele", "Apps"] or titles == ["Games", "Apps"]
    games = await _browse(hass_ws_client, hass, "games", "games")
    names = [c["title"] for c in games["result"]["children"]]
    assert names == ["Another Game", "Test Game One"]
    child = games["result"]["children"][1]
    assert child["media_content_id"] == "PPSA00001"
    assert child["can_play"] is True
    assert child["thumbnail"].startswith("/api/media_player_proxy/media_player.ps5/browse_media/")
    apps = await _browse(hass_ws_client, hass, "apps", "apps")
    app_names = [c["title"] for c in apps["result"]["children"]]
    assert "Orbit Store (PPSA99177)" in app_names
    no_icon = [c for c in apps["result"]["children"] if c["media_content_id"] == "PPSA99177"][0]
    assert no_icon["thumbnail"] is None


async def test_browse_thumbnail_served(hass, fake, hass_ws_client, hass_client) -> None:
    await _setup(hass, fake)
    games = await _browse(hass_ws_client, hass, "games", "games")
    thumb = games["result"]["children"][0]["thumbnail"]
    client = await hass_client()
    resp = await client.get(thumb)
    assert resp.status == 200
    assert (await resp.read()).startswith(b"\x89PNG")


async def test_play_media_launches(hass, fake) -> None:
    await _setup(hass, fake)
    fake.running = ("PPSA00002", 7)
    await hass.services.async_call(
        "media_player", "play_media",
        {"entity_id": ENTITY, ATTR_MEDIA_CONTENT_TYPE: "app", ATTR_MEDIA_CONTENT_ID: "PPSA00001"},
        blocking=True)
    assert fake.running[0] == "PPSA00001"


async def test_browse_when_off_empty_with_hint(hass, fake, freezer, hass_ws_client) -> None:
    await _setup(hass, fake)
    fake.down = True
    with patch(DDP, AsyncMock(return_value="off")):
        await tick_until(hass, freezer, lambda: hass.states.get(ENTITY).state == "off")
    fake.apps = []  # even a known list is not offered while the console is off
    root = await _browse(hass_ws_client, hass)
    assert root["success"], root
    assert root["result"]["children"] == []
    assert root["result"]["not_shown"] == 0
