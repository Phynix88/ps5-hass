"""Base entity, source names and error translation."""

from __future__ import annotations

from collections import Counter
from typing import Any, NoReturn

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ApiError, AuthError, CannotConnect, Ps5HassError
from .const import DEVICE_NAME, DOMAIN, MANUFACTURER
from .coordinator import Ps5HassConfigEntry, Ps5HassData, StateCoordinator

# payload error codes with their own message; everything else -> request_failed
KNOWN_ERRORS = {
    "not_installed",
    "no_user",
    "power_actions_disabled",
    "other_app_running",
    "title_mismatch",
    "no_app",
}


def raise_api_error(err: Ps5HassError) -> NoReturn:
    """Turn a client error into a translated HomeAssistantError."""
    if isinstance(err, CannotConnect):
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="console_off") from err
    if isinstance(err, AuthError):
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="auth_failed") from err
    assert isinstance(err, ApiError)
    if err.code == "system_error":
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="system_error",
            translation_placeholders={"rc_hex": err.rc_hex or "?"},
        ) from err
    if err.code in KNOWN_ERRORS:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key=err.code) from err
    raise HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="request_failed",
        translation_placeholders={"code": err.code},
    ) from err


def source_names(apps: list[dict[str, Any]] | None, only_games: bool = False) -> dict[str, str]:
    """Display name -> title id. Games first, then apps, each alphabetical.

    Names that occur more than once get the title id appended so every entry is
    unique (e.g. two "Orbit Store" builds).
    """
    selected = [a for a in apps or [] if not only_games or a["kind"] == "game"]
    counts = Counter(a["name"] for a in apps or [])
    ordered = sorted(selected, key=lambda a: (a["kind"] != "game", a["name"].casefold(), a["title_id"]))
    return {
        (f"{a['name']} ({a['title_id']})" if counts[a["name"]] > 1 else a["name"]): a["title_id"]
        for a in ordered
    }


def display_name(apps: list[dict[str, Any]] | None, title_id: str, fallback: str) -> str:
    """The unique display name of a title, as used in the source list."""
    for name, tid in source_names(apps).items():
        if tid == title_id:
            return name
    return fallback


class Ps5HassEntity(CoordinatorEntity[StateCoordinator]):
    """Common base: one device per console, available while the payload answers."""

    _attr_has_entity_name = True

    def __init__(self, entry: Ps5HassConfigEntry, key: str) -> None:
        super().__init__(entry.runtime_data.state)
        self.entry = entry
        info = entry.runtime_data.info
        device_id = info["device_id"]
        self._attr_unique_id = f"{device_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=DEVICE_NAME,
            manufacturer=MANUFACTURER,
            model=info.get("model") or None,
            sw_version=info.get("firmware") or None,
        )

    @property
    def data(self) -> Ps5HassData:
        return self.entry.runtime_data

    @property
    def payload_up(self) -> bool:
        return self.coordinator.data is not None and self.coordinator.data.payload

    @property
    def available(self) -> bool:
        return self.payload_up

    @property
    def running(self) -> dict[str, Any] | None:
        """{title_id, name, app_id} of the running app, or None."""
        if not self.payload_up:
            return None
        return self.coordinator.data.state.get("app")
