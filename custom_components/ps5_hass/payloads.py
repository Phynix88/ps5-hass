"""The payloads the Payload Manager has installed (for the payload switches)."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from awesomeversion import AwesomeVersion, AwesomeVersionException
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import ApiError, AuthError, CannotConnect, Ps5HassClient
from .const import DOMAIN, PAYLOADS_INTERVAL, PAYLOADS_RECHECK_DELAY

if TYPE_CHECKING:
    from .coordinator import ConsoleState, StateCoordinator

_LOGGER = logging.getLogger(__package__)

NONE: dict[str, Any] = {"available": False, "payloads": []}

# Payloads before v0.4.2 do not see a game that is minimised (still in memory) and would
# switch payloads under it, which hung a console.
MIN_PAYLOAD_VERSION = "0.4.2"
_VERSION_CORE = re.compile(r"v?(\d+(?:\.\d+)*)")


def payload_switches_supported(info: dict[str, Any]) -> bool:
    """False for a payload older than v0.4.2.

    "dev" and "host" builds, anything unparsable and a missing version count as new
    enough; a suffix such as "-test" is ignored.
    """
    version = info.get("version")
    match = _VERSION_CORE.match(version) if isinstance(version, str) else None
    if match is None:
        return True
    try:
        return AwesomeVersion(match.group(1)) >= AwesomeVersion(MIN_PAYLOAD_VERSION)
    except AwesomeVersionException:
        return True


def payload_key(slot: str) -> str:
    """The part of a slot that survives updates: "repo/ftpsrv@e8cce5d1" -> "repo/ftpsrv".

    The hash changes with the payload or the catalogue; a slot without "@" (a local upload)
    is its own key.
    """
    return slot.partition("@")[0] or slot


def listed_by_key(data: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """The listed payloads by key. Entries without a slot are skipped; of several with one key the first wins."""
    out: dict[str, dict[str, Any]] = {}
    for item in (data or {}).get("payloads", []):
        slot = item.get("slot") if isinstance(item, dict) else None
        if isinstance(slot, str) and slot:
            out.setdefault(payload_key(slot), item)
    return out


class PayloadsCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """/payloads every 30 s and at once when the console is on and the payload answers again.

    `data` is the last answer: `{"available": bool, "payloads": [...]}`. Nothing is asked
    while the payload is away or the console is not `on` (the payload would only say
    `available` false); a failed request keeps the last answer.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: Ps5HassClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} payloads",
            update_interval=timedelta(seconds=PAYLOADS_INTERVAL),
        )
        self.client = client
        self.state: StateCoordinator | None = None
        self._seen: bool | None = None
        self._recheck: CALLBACK_TYPE | None = None

    async def async_refresh_after_action(self) -> None:
        """Refresh at once and again after a few seconds, after a payload was started or stopped.

        Right after a start the Payload Manager does not list the new process yet, so the
        first answer can still show the old state.
        """
        await self.async_refresh()
        self._cancel_recheck()
        self._recheck = async_call_later(self.hass, PAYLOADS_RECHECK_DELAY, self._recheck_now)

    async def _recheck_now(self, _now: object) -> None:
        self._recheck = None
        await self.async_refresh()

    def _cancel_recheck(self) -> None:
        if self._recheck is not None:
            self._recheck()
            self._recheck = None

    async def async_shutdown(self) -> None:
        self._cancel_recheck()
        await super().async_shutdown()

    def follow(self, state: StateCoordinator) -> Callable[[], None]:
        """React to the console going away or coming back; returns the unsubscribe."""
        self.state = state
        self._seen = self.console_on(state.data)
        return state.async_add_listener(self._state_changed)

    @staticmethod
    def console_on(console: ConsoleState | None) -> bool:
        return console is not None and console.payload and console.power == "on"

    @property
    def usable(self) -> bool:
        """The console is on and the payload answers."""
        return self.state is None or self.console_on(self.state.data)

    @callback
    def _state_changed(self) -> None:
        assert self.state is not None
        now = self.console_on(self.state.data)
        if now == self._seen:
            return
        self._seen = now
        if now:
            self.hass.async_create_task(self.async_request_refresh())
        else:
            self.async_update_listeners()  # the switches are unavailable until it is back

    async def _async_update_data(self) -> dict[str, Any]:
        data = self.data or NONE
        if not self.usable:
            return data
        try:
            answer = await self.client.payloads()
        except AuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (CannotConnect, ApiError):
            return data  # keep what we have
        payloads = answer.get("payloads")
        return {
            "available": bool(answer.get("available")),
            "payloads": payloads if isinstance(payloads, list) else [],
        }
