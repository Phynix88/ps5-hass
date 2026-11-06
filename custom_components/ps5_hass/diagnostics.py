"""Diagnostics: what a bug report needs, without the token."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_CLIENT_ID, CONF_TOKEN
from .coordinator import Ps5HassConfigEntry

TO_REDACT = {CONF_TOKEN, CONF_CLIENT_ID}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: Ps5HassConfigEntry
) -> dict[str, Any]:
    data = entry.runtime_data
    console = data.state.data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "info": data.info,
        "console": {
            "power": console.power if console else None,
            "payload": console.payload if console else None,
            "state": console.state if console else None,
            "last_update_success": data.state.last_update_success,
        },
        "apps": {
            "count": len(data.apps.data or []),
            "etag": data.apps.etag,
        },
    }
