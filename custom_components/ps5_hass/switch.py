"""Fan control on/off (off = the console's own threshold) and keep awake."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import slugify

from .api import ApiError, Ps5HassError
from .coordinator import Ps5HassConfigEntry
from .entity import Ps5HassEntity, Ps5PayloadEntity, raise_api_error
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
        listed = (payloads.data or {}).get("payloads", [])
        new = [p["slot"] for p in listed if not p["protected"] and p["slot"] not in known]
        known.update(new)
        if new:
            async_add_entities(Ps5PayloadSwitch(entry, slot) for slot in new)

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
    """One payload of the Payload Manager: on while it runs. Protected ones cannot be switched."""

    def __init__(self, entry: Ps5HassConfigEntry, slot: str) -> None:
        # the slot has "/" and "@" in it (repo/name@hash)
        super().__init__(entry, f"payload_{slugify(slot)}")
        self._slot = slot

    def _listed(self) -> dict[str, Any] | None:
        listed = (self.coordinator.data or {}).get("payloads", [])
        return next((p for p in listed if p["slot"] == self._slot), None)

    @property
    def suggested_object_id(self) -> str:
        return f"payload {slugify(self._listed()['name'] if self._listed() else self._slot)}"

    @property
    def name(self) -> str:
        listed = self._listed()
        return listed["name"] if listed else self._slot

    @property
    def available(self) -> bool:
        listed = self._listed()
        return super().available and listed is not None and not listed["protected"]

    @property
    def is_on(self) -> bool | None:
        listed = self._listed()
        return listed["running"] if listed else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        listed = self._listed()
        return {"version": listed["version"] if listed else None}

    async def _set(self, on: bool) -> None:
        client = self.entry.runtime_data.client
        try:
            if on:
                await client.payload_start(self._slot)
            else:
                await client.payload_stop(self._slot)
        except ApiError as err:
            if err.code != ("already_running" if on else "not_running"):
                raise_api_error(err)
            # changed elsewhere in the meantime: the refresh below shows how it is
        except Ps5HassError as err:
            raise_api_error(err)
        await self.coordinator.async_refresh()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
