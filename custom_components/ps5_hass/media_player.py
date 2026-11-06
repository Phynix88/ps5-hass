"""The console as a media player: what runs, which titles exist, switch and stop."""

from __future__ import annotations

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Ps5HassError
from .const import DOMAIN, OPT_SOURCES
from .coordinator import Ps5HassConfigEntry, app_by_id
from .entity import Ps5HassEntity, display_name, raise_api_error, source_names

POWER_STATES = {
    "on": MediaPlayerState.ON,
    "standby": MediaPlayerState.STANDBY,
    "off": MediaPlayerState.OFF,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ps5HassConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([Ps5MediaPlayer(entry)])


class Ps5MediaPlayer(Ps5HassEntity, MediaPlayerEntity):
    """Always available: shows off/standby/on when the payload is away."""

    _attr_name = None
    _attr_media_image_remotely_accessible = False

    def __init__(self, entry: Ps5HassConfigEntry) -> None:
        super().__init__(entry, "media_player")
        features = MediaPlayerEntityFeature.SELECT_SOURCE | MediaPlayerEntityFeature.STOP
        if self.data.info.get("power_actions"):
            features |= MediaPlayerEntityFeature.TURN_OFF
        self._attr_supported_features = features

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.data.apps.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return True

    @property
    def _only_games(self) -> bool:
        return self.entry.options.get(OPT_SOURCES, "all") == "games"

    @property
    def state(self) -> MediaPlayerState:
        console = self.coordinator.data
        if console is None:
            return MediaPlayerState.OFF
        if not console.payload:
            return POWER_STATES[console.power]
        return MediaPlayerState.PLAYING if self.running else MediaPlayerState.IDLE

    @property
    def source_list(self) -> list[str]:
        return list(source_names(self.data.apps.data, self._only_games))

    @property
    def source(self) -> str | None:
        app = self.running
        if not app:
            return None
        return display_name(self.data.apps.data, app["title_id"], app["name"])

    @property
    def media_title(self) -> str | None:
        app = self.running
        return app["name"] if app else None

    @property
    def media_content_id(self) -> str | None:
        app = self.running
        return app["title_id"] if app else None

    @property
    def media_content_type(self) -> MediaType | None:
        app = self.running
        if not app:
            return None
        meta = app_by_id(self.data.apps.data, app["title_id"])
        return MediaType.GAME if meta and meta["kind"] == "game" else MediaType.APP

    @property
    def media_image_hash(self) -> str | None:
        app = self.running
        if not app:
            return None
        meta = app_by_id(self.data.apps.data, app["title_id"])
        return app["title_id"] if meta and meta["has_icon"] else None

    async def async_get_media_image(self) -> tuple[bytes | None, str | None]:
        app = self.running
        if not app:
            return None, None
        try:
            return await self.data.client.icon(app["title_id"])
        except Ps5HassError:
            return None, None

    def _require_payload(self) -> None:
        if not self.payload_up:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="console_off")

    async def async_select_source(self, source: str) -> None:
        self._require_payload()
        title_id = source_names(self.data.apps.data).get(source)
        if title_id is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unknown_source",
                translation_placeholders={"source": source},
            )
        try:
            await self.data.client.launch(title_id, replace_running=True)
        except Ps5HassError as err:
            raise_api_error(err)
        await self.coordinator.async_refresh()

    async def async_media_stop(self) -> None:
        self._require_payload()
        app = self.running
        try:
            await self.data.client.close(app["title_id"] if app else None)
        except Ps5HassError as err:
            raise_api_error(err)
        await self.coordinator.async_refresh()

    async def async_turn_off(self) -> None:
        self._require_payload()
        try:
            await self.data.client.standby()
        except Ps5HassError as err:
            raise_api_error(err)
        await self.coordinator.async_refresh()
