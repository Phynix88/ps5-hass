"""Update notice: is a newer ps5-hass payload on GitHub?"""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from typing import Any

from aiohttp import ClientTimeout
from awesomeversion import AwesomeVersionCompareException
from homeassistant.components.update import UpdateEntity
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import PAYLOAD_RELEASE_INTERVAL, PAYLOAD_RELEASE_URL
from .coordinator import Ps5HassConfigEntry
from .entity import Ps5HassEntity

_LOGGER = logging.getLogger(__package__)

PAYLOAD_ID = "ps5-hass"  # id of our entry in payloads.json
REQUEST_TIMEOUT = 10  # s
RELEASES_PAGE = "https://github.com/Phynix88/ps5-hass-payload/releases/tag/v"
LOOKS_LIKE_VERSION = re.compile(r"\d")  # "dev" and "host" builds are not versions

# Shown with the update; installing happens on the console, not here.
HOW_TO_UPDATE = {
    "en": "Replace ps5-hass.elf in the Payload Manager on the console.",
    "de": "ps5-hass.elf im Payload Manager auf der Konsole ersetzen.",
}


def strip_v(version: str) -> str:
    """'v0.3.1' -> '0.3.1'."""
    return version.removeprefix("v")


class PayloadReleaseCoordinator(DataUpdateCoordinator[str | None]):
    """Latest payload version on GitHub (without leading v); None while unknown."""

    def __init__(self, hass: HomeAssistant, entry: Ps5HassConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name="PS5 payload release",
            update_interval=timedelta(seconds=PAYLOAD_RELEASE_INTERVAL),
        )
        self._failing = False

    async def _fetch(self) -> str:
        session = async_get_clientsession(self.hass)
        async with session.get(
            PAYLOAD_RELEASE_URL, timeout=ClientTimeout(total=REQUEST_TIMEOUT)
        ) as response:
            response.raise_for_status()
            feed: Any = await response.json(content_type=None)
        payloads = feed["payloads"]
        for item in payloads:
            if item["id"] == PAYLOAD_ID:
                version = item["version"]
                if isinstance(version, str) and version:
                    return strip_v(version)
        raise ValueError(f"no {PAYLOAD_ID} entry with a version")

    async def _async_update_data(self) -> str | None:
        """Never raises: an unreachable GitHub is 'unknown', logged once per outage."""
        try:
            latest = await self._fetch()
        except Exception as err:  # noqa: BLE001 - whatever goes wrong, this check must not matter
            if not self._failing:
                self._failing = True
                _LOGGER.warning(
                    "Cannot check GitHub for a newer ps5-hass payload (%s: %s); "
                    "trying again every %d hours",
                    type(err).__name__,
                    err,
                    PAYLOAD_RELEASE_INTERVAL // 3600,
                )
            return None
        self._failing = False
        return latest


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ps5HassConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = PayloadReleaseCoordinator(hass, entry)
    async_add_entities([Ps5PayloadUpdate(entry, coordinator)])
    # GitHub being slow must not hold up the setup of the entry
    entry.async_create_background_task(hass, coordinator.async_refresh(), "ps5_hass_release_check")


class Ps5PayloadUpdate(Ps5HassEntity, UpdateEntity):
    """Installed payload (from /info, cached while the console is off) vs. GitHub. No install."""

    _attr_translation_key = "payload"
    _attr_title = "ps5-hass payload"

    def __init__(self, entry: Ps5HassConfigEntry, release: PayloadReleaseCoordinator) -> None:
        super().__init__(entry, "payload_update")
        self.release = release

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.release.async_add_listener(self._release_updated))

    @callback
    def _release_updated(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        return True  # the installed version is known while the console is off

    @property
    def installed_version(self) -> str | None:
        version = self.data.info.get("version")
        return strip_v(version) if isinstance(version, str) and version else None

    @property
    def latest_version(self) -> str | None:
        return self.release.data

    @property
    def release_url(self) -> str | None:
        latest = self.latest_version
        return f"{RELEASES_PAGE}{latest}" if latest else None

    @property
    def release_summary(self) -> str | None:
        if self.state != STATE_ON:  # HA has no translation for release summaries
            return None
        return HOW_TO_UPDATE.get(self.hass.config.language.split("-")[0], HOW_TO_UPDATE["en"])

    def version_is_newer(self, latest_version: str, installed_version: str) -> bool:
        """Dev and host builds, or anything unparsable, never claim an update."""
        if not all(map(LOOKS_LIKE_VERSION.match, (latest_version, installed_version))):
            return False
        try:
            return super().version_is_newer(latest_version, installed_version)
        except AwesomeVersionCompareException:
            return False
