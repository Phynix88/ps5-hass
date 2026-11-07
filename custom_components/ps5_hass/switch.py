"""Fan control on/off (off = the console's own threshold) and keep awake."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify

from .api import ApiError, Ps5HassError
from .const import DOMAIN
from .coordinator import Ps5HassConfigEntry
from .entity import Ps5HassEntity, Ps5PayloadEntity, raise_api_error
from .payloads import listed_by_key
from .fan import DEFAULT_TARGET, FAN_MAX, fan_state, set_fan


async def async_setup_entry(
    hass: HomeAssistant,
    entry: Ps5HassConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    capabilities = entry.runtime_data.info.get("capabilities", {})
    entities: list[SwitchEntity] = []
    if capabilities.get("fan_control"):
        entities.append(Ps5FanControl(entry))
    if capabilities.get("keep_awake"):
        entities.append(Ps5KeepAwake(entry))
    async_add_entities(entities)

    payloads = entry.runtime_data.payloads
    if payloads is None:
        return

    # One switch per payload that is not protected, created when it first shows up.
    # A protected payload never gets one, also not when the flag turns up later.
    known: set[str] = set()

    @callback
    def add_new_payloads() -> None:
        new = {
            key: item
            for key, item in listed_by_key(payloads.data).items()
            if key not in known and not item.get("protected", True)
        }
        known.update(new)
        if new:
            async_add_entities(Ps5PayloadSwitch(entry, key, item) for key, item in new.items())

    entry.async_on_unload(payloads.async_add_listener(add_new_payloads))
    add_new_payloads()


class Ps5FanControl(Ps5HassEntity, SwitchEntity, RestoreEntity):
    """On = our threshold is applied; turning on uses the last one again."""

    _attr_translation_key = "fan_control"

    def __init__(self, entry: Ps5HassConfigEntry) -> None:
        super().__init__(entry, "fan_control")
        self._last_target: int = DEFAULT_TARGET

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and isinstance(last.attributes.get("last_target_c"), int):
            self._last_target = last.attributes["last_target_c"]

    @property
    def is_on(self) -> bool | None:
        target = fan_state(self).get("target_c")
        if target:
            self._last_target = target
        return target is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"last_target_c": self._last_target}

    async def async_turn_on(self, **kwargs: Any) -> None:
        stock = fan_state(self).get("stock_c")
        limit = min(FAN_MAX, stock) if stock else FAN_MAX
        await set_fan(self, min(self._last_target, limit))  # never above the factory value

    async def async_turn_off(self, **kwargs: Any) -> None:
        await set_fan(self, 0)


class Ps5KeepAwake(Ps5HassEntity, SwitchEntity):
    """While on, the payload keeps the console from powering down by itself."""

    _attr_translation_key = "keep_awake"

    def __init__(self, entry: Ps5HassConfigEntry) -> None:
        super().__init__(entry, "keep_awake")

    @property
    def is_on(self) -> bool | None:
        if not self.payload_up:
            return None
        value = self.coordinator.data.state.get("keep_awake")
        return value if isinstance(value, bool) else None

    async def _set(self, on: bool) -> None:
        try:
            await self.data.client.set_keep_awake(on)
        except Ps5HassError as err:
            raise_api_error(err)
        await self.coordinator.async_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)


class Ps5PayloadSwitch(Ps5PayloadEntity, SwitchEntity):
    """One payload of the Payload Manager: on while it runs. Protected ones cannot be switched.

    Identified by its key (the slot without the "@hash" part), so an update of the payload
    keeps the entity; start and stop use the slot as listed right now.
    """

    def __init__(self, entry: Ps5HassConfigEntry, key: str, item: dict[str, Any]) -> None:
        super().__init__(entry, f"payload_{slugify(key)}")  # the key has "/" in it
        self._key = key
        self._name = str(item.get("name") or key)

    def _listed(self) -> dict[str, Any] | None:
        return listed_by_key(self.coordinator.data).get(self._key)

    @property
    def suggested_object_id(self) -> str:
        return f"payload {slugify(self._name)}"  # language independent

    @property
    def name(self) -> str:
        listed = self._listed()
        if listed and listed.get("name"):
            self._name = str(listed["name"])
        return self._name  # a payload that drops out of the list keeps its name

    @property
    def available(self) -> bool:
        listed = self._listed()
        return super().available and listed is not None and not listed.get("protected", True)

    @property
    def is_on(self) -> bool | None:
        listed = self._listed()
        return bool(listed.get("running")) if listed else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        listed = self._listed()
        return {"version": listed.get("version") if listed else None}

    async def _set(self, on: bool) -> None:
        listed = self._listed()
        if listed is None:  # not listed any more: the Payload Manager would say 404 for it too
            raise_api_error(ApiError(404, {"error": "unknown_payload"}))
        console = self.entry.runtime_data.state.data
        if console is not None and console.state and console.state.get("app") is not None:
            # a game or app runs: no request at all (a payload older than v0.4.1 would obey)
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="app_running")
        client = self.entry.runtime_data.client
        try:
            if on:
                await client.payload_start(listed["slot"])
            else:
                await client.payload_stop(listed["slot"])
        except ApiError as err:
            if err.code != ("already_running" if on else "not_running"):
                raise_api_error(err)
            # changed elsewhere in the meantime: the refresh below shows how it is
        except Ps5HassError as err:
            raise_api_error(err)
        await self.coordinator.async_refresh_after_action()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
