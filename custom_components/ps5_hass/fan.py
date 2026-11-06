"""Shared helpers for the fan entities."""

from __future__ import annotations

from typing import Any

from .api import Ps5HassError
from .entity import Ps5HassEntity, raise_api_error

FAN_MIN = 45
FAN_MAX = 85
DEFAULT_TARGET = 80  # used when fan control is switched on without an earlier value


def fan_state(entity: Ps5HassEntity) -> dict[str, Any]:
    """The /state fan object, or {} while the payload is away."""
    if not entity.payload_up:
        return {}
    return entity.coordinator.data.state.get("fan") or {}


async def set_fan(entity: Ps5HassEntity, threshold_c: int) -> None:
    """Set the threshold (0 = console's own) and refresh the state."""
    try:
        await entity.data.client.set_fan(threshold_c)
    except Ps5HassError as err:
        raise_api_error(err)
    await entity.coordinator.async_refresh()
