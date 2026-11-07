"""PS5 (ps5-hass): Home Assistant integration for the ps5-hass payload."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApiError, CannotConnect, Ps5HassClient, Ps5HassError
from .const import CONF_INFO, CONF_TOKEN
from .coordinator import (
    AppsCoordinator,
    Ps5HassConfigEntry,
    Ps5HassData,
    StateCoordinator,
    clean_info,
)
from .playtime import PlaytimeCoordinator

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.MEDIA_PLAYER,
    Platform.NOTIFY,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.UPDATE,
]


async def async_setup_entry(hass: HomeAssistant, entry: Ps5HassConfigEntry) -> bool:
    """Set up a console; works with the console off (cached /info)."""
    client = Ps5HassClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_TOKEN],
    )
    info = dict(entry.data.get(CONF_INFO, {}))
    try:
        fresh = clean_info(await client.info())
    except (CannotConnect, ApiError):
        pass  # console off or in rest mode: the normal case
    else:
        if fresh != info:
            info = fresh
            hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_INFO: info})

    apps = AppsCoordinator(hass, entry, client)
    state = StateCoordinator(hass, entry, client, apps)
    entry.runtime_data = Ps5HassData(client, state, apps, info)
    await apps.async_refresh()
    await state.async_refresh()
    if info.get("capabilities", {}).get("playtime"):
        playtime = PlaytimeCoordinator(hass, entry, client)
        entry.runtime_data.playtime = playtime
        entry.async_on_unload(playtime.follow(state))
        await playtime.async_refresh()
        entry.async_on_unload(playtime.async_add_listener(lambda: None))
    # Coordinators only poll while someone listens; keep them polling even if
    # every entity is disabled (state changes drive the app events).
    entry.async_on_unload(apps.async_add_listener(lambda: None))
    entry.async_on_unload(state.async_add_listener(lambda: None))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: Ps5HassConfigEntry) -> bool:
    """Unload a console."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Give the pairing slot back; the console may well be off."""
    client = Ps5HassClient(
        async_get_clientsession(hass),
        entry.data[CONF_HOST],
        entry.data[CONF_PORT],
        entry.data[CONF_TOKEN],
        timeout=5,
    )
    try:
        await client.revoke()
    except Ps5HassError:
        pass
