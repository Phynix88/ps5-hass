"""Buttons: close the running app, rest mode, one start button per favourite."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Ps5HassError
from .const import DOMAIN, OPT_FAVORITES
from .coordinator import Ps5HassConfigEntry, app_by_id
from .entity import Ps5HassEntity, raise_api_error


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ps5HassConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    data = entry.runtime_data
    entities: list[ButtonEntity] = [Ps5CloseAppButton(entry)]
    if data.info.get("power_actions"):
        entities.append(Ps5RestModeButton(entry))
    for title_id in entry.options.get(OPT_FAVORITES, []):
        app = app_by_id(data.apps.data, title_id)
        if app:  # favourites that are no longer installed get no button
            entities.append(Ps5FavoriteButton(entry, title_id, app["name"]))
    async_add_entities(entities)


class Ps5CloseAppButton(Ps5HassEntity, ButtonEntity):
    _attr_translation_key = "close_app"

    def __init__(self, entry: Ps5HassConfigEntry) -> None:
        super().__init__(entry, "close_app")

    async def async_press(self) -> None:
        app = self.running
        if not app:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_app")
        try:
            # the shown title id: never close an app the user has not seen
            await self.data.client.close(app["title_id"])
        except Ps5HassError as err:
            raise_api_error(err)
        await self.coordinator.async_refresh()


class Ps5RestModeButton(Ps5HassEntity, ButtonEntity):
    _attr_translation_key = "rest_mode"

    def __init__(self, entry: Ps5HassConfigEntry) -> None:
        super().__init__(entry, "rest_mode")

    async def async_press(self) -> None:
        try:
            await self.data.client.standby()
        except Ps5HassError as err:
            raise_api_error(err)
        await self.coordinator.async_refresh()


class Ps5FavoriteButton(Ps5HassEntity, ButtonEntity):
    _attr_translation_key = "launch_favorite"

    def __init__(self, entry: Ps5HassConfigEntry, title_id: str, name: str) -> None:
        super().__init__(entry, f"launch_{title_id.lower()}")
        self._title_id = title_id
        self._attr_translation_placeholders = {"name": name}

    async def async_press(self) -> None:
        try:
            await self.data.client.launch(self._title_id, replace_running=True)
        except Ps5HassError as err:
            raise_api_error(err)
        await self.coordinator.async_refresh()
