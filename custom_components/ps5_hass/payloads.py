"""The payloads the Payload Manager has installed (for the payload switches)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import ApiError, AuthError, CannotConnect, Ps5HassClient
from .const import DOMAIN, PAYLOADS_INTERVAL

if TYPE_CHECKING:
    from .coordinator import ConsoleState, StateCoordinator

_LOGGER = logging.getLogger(__package__)

NONE: dict[str, Any] = {"available": False, "payloads": []}


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
