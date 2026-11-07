"""Coordinators: console state (10 s) and app list (ETag)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ApiError, AuthError, CannotConnect, Ps5HassClient
from .const import APPS_INTERVAL, CONF_INFO, DDP_PORT, DOMAIN, HOLD_SECONDS, STATE_INTERVAL
from .ddp import ddp_status

if TYPE_CHECKING:
    from .playtime import PlaytimeCoordinator

_LOGGER = logging.getLogger(__package__)

# /info fields whose change needs a reload (entities depend on them)
RELOAD_FIELDS = ("power_actions", "capabilities")

# /state.power_state values that mean rest mode (payload v0.4)
REST_STATES = ("going_to_rest", "rest")


@dataclass
class ConsoleState:
    """What the integration knows about the console right now."""

    power: Literal["on", "standby", "off"]
    payload: bool  # the payload answered
    state: dict[str, Any] | None  # /state answer when payload is True


@dataclass
class Ps5HassData:
    """Runtime data of a config entry."""

    client: Ps5HassClient
    state: StateCoordinator
    apps: AppsCoordinator
    info: dict[str, Any]
    playtime: PlaytimeCoordinator | None = None  # only with capabilities.playtime


type Ps5HassConfigEntry = ConfigEntry[Ps5HassData]


def app_by_id(apps: list[dict[str, Any]] | None, title_id: str) -> dict[str, Any] | None:
    """The app entry for a title id."""
    return next((a for a in apps or [] if a["title_id"] == title_id), None)


def clean_info(info: dict[str, Any]) -> dict[str, Any]:
    """/info without the result field, as stored in the entry."""
    return {k: v for k, v in info.items() if k != "result"}


def info_needs_reload(old: dict[str, Any], new: dict[str, Any]) -> bool:
    """True when entities would change with the new /info."""
    return any(old.get(k) != new.get(k) for k in RELOAD_FIELDS)


class AppsCoordinator(DataUpdateCoordinator[list[dict[str, Any]]]):
    """Installed apps; refreshed every 10 min and when the console reports a new ETag."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: Ps5HassClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} apps",
            update_interval=timedelta(seconds=APPS_INTERVAL),
        )
        self.client = client
        self.etag: str | None = None

    async def _async_update_data(self) -> list[dict[str, Any]]:
        try:
            result = await self.client.apps(self.etag)
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (CannotConnect, ApiError):
            return self.data or []  # console off is normal: keep the last list
        if result is None:  # 304
            return self.data or []
        self.etag = result.etag
        return result.apps


class StateCoordinator(DataUpdateCoordinator[ConsoleState]):
    """Payload state, with Sony's status query when the payload is away."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: Ps5HassClient,
        apps: AppsCoordinator,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} state",
            update_interval=timedelta(seconds=STATE_INTERVAL),
        )
        self.client = client
        self.apps = apps
        self._failing_since: datetime | None = None
        self._reachable: bool | None = None
        self._check_info = False  # set after any failed poll
        self._payload_uptime: int | None = None

    async def _async_update_data(self) -> ConsoleState:
        try:
            state = await self.client.state()
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except CannotConnect:
            return await self._unreachable()
        except ApiError as err:
            raise UpdateFailed(f"payload answered {err}") from err

        self._failing_since = None
        if self._reachable is False:
            _LOGGER.info("PS5 payload at %s is reachable again", self.client_host)
        uptime = state.get("payload_uptime_s")
        restarted = (
            isinstance(uptime, int)
            and self._payload_uptime is not None
            and uptime < self._payload_uptime
        )
        self._payload_uptime = uptime if isinstance(uptime, int) else None
        if self._check_info or restarted:
            # a gap of any length may hide a payload restart with other settings
            self._check_info = False
            await self._refresh_info()
        self._reachable = True
        if state.get("apps_etag") != self.apps.etag:
            await self.apps.async_request_refresh()
        # rest mode shows at once; the payload keeps answering for a while, no hold needed
        resting = state.get("power_state") in REST_STATES
        return ConsoleState("standby" if resting else "on", True, state)

    @property
    def client_host(self) -> str:
        assert self.config_entry is not None
        return self.config_entry.data[CONF_HOST]

    async def _unreachable(self) -> ConsoleState:
        self._check_info = True
        now = dt_util.utcnow()
        if self._failing_since is None:
            self._failing_since = now
        held = now - self._failing_since < timedelta(seconds=HOLD_SECONDS)
        if held and self.data is not None and self.data.payload:
            return self.data  # short gap, e.g. going to rest mode
        if self._reachable is not False:
            _LOGGER.info("PS5 payload at %s is not reachable", self.client_host)
        self._reachable = False
        power = await ddp_status(self.client_host, DDP_PORT)
        return ConsoleState(power, False, None)

    async def _refresh_info(self) -> None:
        """Payload came back: it may have restarted with other settings."""
        entry = self.config_entry
        assert entry is not None
        try:
            info = clean_info(await self.client.info())
        except (CannotConnect, ApiError):
            return
        old = entry.data.get(CONF_INFO, {})
        if info == old:
            return
        if hasattr(entry, "runtime_data"):
            entry.runtime_data.info = info
        # no update listener: storing the new /info does not reload by itself
        self.hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_INFO: info})
        if info_needs_reload(old, info):
            self.hass.config_entries.async_schedule_reload(entry.entry_id)
