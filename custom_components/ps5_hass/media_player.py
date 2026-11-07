"""The console as a media player: what runs, which titles exist, switch and stop."""

from __future__ import annotations

from typing import Any

from homeassistant.components.media_player import (
    BrowseMedia,
    MediaClass,
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

# MediaPlayerState.STANDBY is deprecated in HA; rest mode shows as off here and
# as "standby" in the power sensor.
POWER_STATES = {
    "on": MediaPlayerState.ON,
    "standby": MediaPlayerState.OFF,
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
        features = (
            MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.BROWSE_MEDIA
            | MediaPlayerEntityFeature.PLAY_MEDIA
        )
        if self.data.info.get("power_actions"):
            features |= MediaPlayerEntityFeature.TURN_OFF
        self._attr_supported_features = features

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.data.apps.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        # off/rest mode are states, not unavailability; a failed update (401, 500) is
        return self.coordinator.last_update_success

    @property
    def _only_games(self) -> bool:
        return self.entry.options.get(OPT_SOURCES, "all") == "games"

    @property
    def state(self) -> MediaPlayerState:
        console = self.coordinator.data
        if console is None:
            return MediaPlayerState.OFF
        if not console.payload or console.power == "standby":
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
        if not app:  # never close an app the user has not seen
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="no_app")
        try:
            await self.data.client.close(app["title_id"])
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

    # --- media browser: games and apps as tiles with covers -----------------

    def _folder(self, key: str, title: str, children: list[BrowseMedia] | None) -> BrowseMedia:
        return BrowseMedia(
            title=title,
            media_class=MediaClass.DIRECTORY,
            children_media_class=MediaClass.GAME if key == "games" else MediaClass.APP,
            media_content_id=key,
            media_content_type=key,
            can_play=False,
            can_expand=True,
            children=children,
        )

    def _tiles(self, games: bool) -> list[BrowseMedia]:
        apps = self.data.apps.data or []
        tiles = []
        for name, title_id in source_names(apps).items():
            meta = app_by_id(apps, title_id)
            if meta is None or (meta["kind"] == "game") != games:
                continue
            tiles.append(
                BrowseMedia(
                    title=name,
                    media_class=MediaClass.GAME if games else MediaClass.APP,
                    media_content_id=title_id,
                    media_content_type="app",
                    can_play=True,
                    can_expand=False,
                    thumbnail=self.get_browse_image_url("app", title_id)
                    if meta["has_icon"]
                    else None,
                )
            )
        return tiles

    async def async_browse_media(
        self, media_content_type: str | None = None, media_content_id: str | None = None
    ) -> BrowseMedia:
        german = (self.hass.config.language or "").startswith("de")
        games_title = "Spiele" if german else "Games"
        if media_content_id == "games":
            return self._folder("games", games_title, self._tiles(True))
        if media_content_id == "apps":
            return self._folder("apps", "Apps", self._tiles(False))
        children = []
        if self.payload_up:  # nothing can be started while the console is away
            children = [
                self._folder("games", games_title, None),
                self._folder("apps", "Apps", None),
            ]
        return BrowseMedia(
            title="PS5" if self.payload_up else ("PS5 (aus)" if german else "PS5 (off)"),
            media_class=MediaClass.DIRECTORY,
            media_content_id="root",
            media_content_type="root",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def async_get_browse_image(
        self,
        media_content_type: str,
        media_content_id: str,
        media_image_id: str | None = None,
    ) -> tuple[bytes | None, str | None]:
        try:
            return await self.data.client.icon(media_content_id)
        except Ps5HassError:
            return None, None

    async def async_play_media(self, media_type: str, media_id: str, **kwargs: Any) -> None:
        self._require_payload()
        if app_by_id(self.data.apps.data, media_id) is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="unknown_source",
                translation_placeholders={"source": media_id},
            )
        try:
            await self.data.client.launch(media_id, replace_running=True)
        except Ps5HassError as err:
            raise_api_error(err)
        await self.coordinator.async_refresh()
