"""Buttons: close the running app, rest mode, one start button per favourite."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Ps5HassError
from .const import DOMAIN, OPT_FAVORITE_NAMES, OPT_FAVORITES
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
    if data.info.get("capabilities", {}).get("power_off"):
        entities.append(Ps5PowerOffButton(entry))
    names: dict[str, str] = entry.options.get(OPT_FAVORITE_NAMES, {})
    for title_id in entry.options.get(OPT_FAVORITES, []):
        # always create the button (the console is usually off at startup);
        # the name was stored when the favourite was chosen
        app = app_by_id(data.apps.data, title_id)
        name = names.get(title_id) or (app["name"] if app else title_id)
        entities.append(Ps5FavoriteButton(entry, title_id, name))
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

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.data.apps.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        apps = self.data.apps.data
        installed = not apps or app_by_id(apps, self._title_id) is not None
        return super().available and installed

    async def async_press(self) -> None:
        try:
            await self.data.client.launch(self._title_id, replace_running=True)
        except Ps5HassError as err:
            raise_api_error(err)
        await self.coordinator.async_refresh()


class Ps5PowerOffButton(Ps5HassEntity, ButtonEntity):
    """Powers the console off (not rest mode); only when allowed on the console."""

    _attr_translation_key = "power_off"

    def __init__(self, entry: Ps5HassConfigEntry) -> None:
        super().__init__(entry, "power_off")

    async def async_press(self) -> None:
        try:
            await self.data.client.power_off()
        except Ps5HassError as err:
            raise_api_error(err)
        await self.coordinator.async_refresh()
