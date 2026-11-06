"""Config flow: find or enter the console, pair with the code shown on the TV."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .api import ApiError, AuthError, CannotConnect, Ps5HassClient
from .const import (
    CONF_CLIENT_ID,
    CONF_INFO,
    CONF_TOKEN,
    DEFAULT_PORT,
    DEVICE_NAME,
    DOMAIN,
    LABEL,
    OPT_FAVORITES,
    OPT_SOURCES,
)

ABORT_CODES = {
    "locked": "locked",
    "notify_failed": "cannot_show_code",
    "random_unavailable": "cannot_show_code",
    "token_limit": "token_limit",
}


def _clean_info(info: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in info.items() if k != "result"}


class Ps5HassConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ps5-hass."""

    VERSION = 1

    def __init__(self) -> None:
        self._host = ""
        self._port = DEFAULT_PORT
        self._info: dict[str, Any] = {}
        self._client_id = str(uuid.uuid4())
        self._client: Ps5HassClient | None = None
        self._placeholders: dict[str, str] = {}

    def _make_client(self) -> Ps5HassClient:
        self._client = Ps5HassClient(
            async_get_clientsession(self.hass), self._host, self._port
        )
        return self._client

    # --- entry points ------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host = user_input[CONF_HOST].strip()
            self._port = user_input[CONF_PORT]
            try:
                self._info = await self._make_client().info()
            except (CannotConnect, ApiError, AuthError):
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(self._info["device_id"])
                self._abort_if_unique_id_configured(
                    updates={CONF_HOST: self._host, CONF_PORT: self._port}
                )
                return await self._start_pairing()
        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._host): str,
                vol.Required(CONF_PORT, default=self._port): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        props = discovery_info.properties
        if props.get("api") != "1":
            return self.async_abort(reason="unsupported_api")
        device_id = props.get("id")
        if not device_id:
            return self.async_abort(reason="cannot_connect")
        self._host = discovery_info.host
        self._port = discovery_info.port or DEFAULT_PORT
        await self.async_set_unique_id(device_id)
        self._abort_if_unique_id_configured(
            updates={CONF_HOST: self._host, CONF_PORT: self._port}
        )
        name = discovery_info.name.split("._ps5-hass", 1)[0]
        self.context["title_placeholders"] = {"name": name}
        self._placeholders = {"name": name}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="zeroconf_confirm", description_placeholders=self._placeholders
            )
        try:
            self._info = await self._make_client().info()
        except (CannotConnect, ApiError, AuthError):
            return self.async_abort(reason="cannot_connect")
        return await self._start_pairing()

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        self._host = entry_data[CONF_HOST]
        self._port = entry_data[CONF_PORT]
        self._client_id = entry_data[CONF_CLIENT_ID]
        self._info = dict(entry_data.get(CONF_INFO, {}))
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        self._make_client()
        return await self._start_pairing()

    # --- pairing -----------------------------------------------------------

    async def _start_pairing(self) -> ConfigFlowResult:
        return await self._request_code()

    async def _request_code(self) -> ConfigFlowResult:
        """Show a new code on the TV, then the code form (or an abort)."""
        assert self._client is not None
        try:
            await self._client.pair_start()
        except CannotConnect:
            return self._pair_form({"base": "cannot_connect"})
        except ApiError as err:
            return self._on_api_error(err)
        return self._pair_form({})

    def _on_api_error(self, err: ApiError) -> ConfigFlowResult:
        """Abort for fatal pairing errors, else the code form with an error."""
        if err.code in ABORT_CODES:
            minutes = max(1, round((err.retry_after or 0) / 60))
            return self.async_abort(
                reason=ABORT_CODES[err.code],
                description_placeholders={"minutes": str(minutes)},
            )
        return self._pair_form(self._form_error(err))

    def _form_error(self, err: ApiError) -> dict[str, str]:
        if err.code == "wrong_code":
            self._placeholders["attempts_left"] = str(err.attempts_left or 0)
            return {"base": "wrong_code"}
        if err.code in ("no_code", "code_exhausted"):
            return {"base": "code_expired"}
        if err.code == "rate_limited":
            self._placeholders["retry_after"] = str(err.retry_after or 30)
            return {"base": "rate_limited"}
        return {"base": "cannot_connect"}

    def _pair_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        self._placeholders.setdefault("attempts_left", "5")
        self._placeholders.setdefault("retry_after", "30")
        schema = vol.Schema(
            {
                vol.Optional("code", default=""): str,
                vol.Optional("new_code", default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="pair",
            data_schema=schema,
            errors=errors,
            description_placeholders=self._placeholders,
        )

    async def async_step_pair(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self._pair_form({})
        if self._client is None:
            self._make_client()
        assert self._client is not None
        if user_input.get("new_code"):
            return await self._request_code()
        try:
            token = await self._client.pair_finish(
                user_input.get("code", "").strip(), self._client_id, LABEL
            )
        except CannotConnect:
            return self._pair_form({"base": "cannot_connect"})
        except ApiError as err:
            return self._on_api_error(err)
        try:
            self._info = await self._client.info()
        except (CannotConnect, ApiError, AuthError):
            pass  # keep what we had; setup refreshes it
        data = {
            CONF_HOST: self._host,
            CONF_PORT: self._port,
            CONF_TOKEN: token,
            CONF_CLIENT_ID: self._client_id,
            CONF_INFO: _clean_info(self._info),
        }
        if self.source == "reauth":
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data
            )
        return self.async_create_entry(title=DEVICE_NAME, data=data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return Ps5HassOptionsFlow()


class Ps5HassOptionsFlow(OptionsFlow):
    """Source list filter and favourite apps."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        entry = self.config_entry
        favorites: list[str] = list(entry.options.get(OPT_FAVORITES, []))
        apps = await self._apps()
        names = {a["title_id"]: a["name"] for a in apps}
        options = [
            SelectOptionDict(value=tid, label=f"{name} ({tid})")
            for tid, name in sorted(names.items(), key=lambda kv: kv[1].casefold())
        ]
        options += [
            SelectOptionDict(value=tid, label=tid) for tid in favorites if tid not in names
        ]
        schema = vol.Schema(
            {
                vol.Required(
                    OPT_SOURCES, default=entry.options.get(OPT_SOURCES, "all")
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=["all", "games"],
                        translation_key="sources",
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional(OPT_FAVORITES, default=favorites): SelectSelector(
                    SelectSelectorConfig(
                        options=options, multiple=True, mode=SelectSelectorMode.DROPDOWN
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def _apps(self) -> list[dict[str, Any]]:
        """App list from the running integration, else straight from the console."""
        data = getattr(self.config_entry, "runtime_data", None)
        apps = getattr(getattr(data, "apps", None), "data", None)
        if apps:
            return list(apps)
        entry = self.config_entry
        client = Ps5HassClient(
            async_get_clientsession(self.hass),
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
            entry.data[CONF_TOKEN],
        )
        try:
            result = await client.apps()
        except (CannotConnect, ApiError, AuthError):
            return []
        return result.apps if result else []
