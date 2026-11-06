"""Config flow: find or enter the console, pair with the code shown on the TV."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
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
    OPT_FAVORITE_NAMES,
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


PERMISSIONS = ("power_actions", "power_off", "fan_control")


class Ps5HassOptionsFlow(OptionsFlowWithReload):
    """Menu: source list and favourites (saving reloads the entry), or the
    console permissions (granted with a code on the TV, design v0.3 §6)."""

    def __init__(self) -> None:
        self._names: dict[str, str] = {}
        self._pending: list[str] = []  # permissions waiting for the TV code
        self._perms: dict[str, dict[str, bool]] | None = None  # what the form shows
        self._placeholders: dict[str, str] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["sources", "permissions"])

    async def async_step_sources(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        old_names: dict[str, str] = dict(entry.options.get(OPT_FAVORITE_NAMES, {}))
        if user_input is not None:
            # remember the names, so the buttons keep them while the console is off
            favorites = user_input.get(OPT_FAVORITES, [])
            names = {
                tid: self._names.get(tid) or old_names.get(tid) or tid for tid in favorites
            }
            return self.async_create_entry(data={**user_input, OPT_FAVORITE_NAMES: names})
        favorites: list[str] = list(entry.options.get(OPT_FAVORITES, []))
        apps = await self._apps()
        names = {a["title_id"]: a["name"] for a in apps}
        self._names = names
        options = [
            SelectOptionDict(value=tid, label=f"{name} ({tid})")
            for tid, name in sorted(names.items(), key=lambda kv: kv[1].casefold())
        ]
        options += [
            SelectOptionDict(value=tid, label=f"{old_names.get(tid, tid)} ({tid})")
            for tid in favorites
            if tid not in names
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
        return self.async_show_form(step_id="sources", data_schema=schema)

    # --- permissions (v0.3) -------------------------------------------------

    def _client(self) -> Ps5HassClient:
        entry = self.config_entry
        return Ps5HassClient(
            async_get_clientsession(self.hass),
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
            entry.data[CONF_TOKEN],
        )

    def _permissions_form(
        self, perms: dict[str, dict[str, bool]], errors: dict[str, str]
    ) -> ConfigFlowResult:
        schema = vol.Schema(
            {
                vol.Optional(name, default=perms[name]["allowed"]): bool
                for name in PERMISSIONS
                if perms.get(name, {}).get("available")
            }
        )
        self._placeholders.setdefault("attempts_left", "5")
        self._placeholders.setdefault("retry_after", "30")
        return self.async_show_form(
            step_id="permissions",
            data_schema=schema,
            errors=errors,
            description_placeholders=dict(self._placeholders),
        )

    async def async_step_permissions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        stored = self.config_entry.data.get(CONF_INFO, {}).get("permissions")
        if user_input is not None and self._perms is not None:
            return await self._apply_permissions(self._perms, user_input)
        try:
            info = await self._client().info()
        except (CannotConnect, ApiError, AuthError):
            if stored is None:
                return self.async_abort(reason="cannot_connect")
            return self._permissions_form(stored, {"base": "cannot_connect"})
        perms = info.get("permissions")
        if perms is None:
            return self.async_abort(reason="payload_too_old")
        self._perms = perms
        if user_input is None:
            return self._permissions_form(perms, {})
        return await self._apply_permissions(perms, user_input)

    async def _apply_permissions(
        self, perms: dict[str, dict[str, bool]], user_input: dict[str, Any]
    ) -> ConfigFlowResult:
        """Revoke and grant against the permissions the form was built from."""
        allowed = {n for n in PERMISSIONS if perms[n]["allowed"]}
        shown = [n for n in PERMISSIONS if perms[n]["available"]]
        wanted = {n for n in shown if user_input.get(n)}
        revoke = [n for n in shown if n in allowed and n not in wanted]
        grant = [n for n in shown if n in wanted and n not in allowed]
        if not revoke and not grant:
            # nothing to do: no call, no reload
            return self.async_create_entry(data=dict(self.config_entry.options))
        try:
            if revoke:
                perms = self._perms = await self._client().grant_revoke(revoke)
                # the console changed: entities must follow even if the grant
                # below fails or the dialog is closed
                await self._store_info(perms)
            if not grant:
                return await self._permissions_done()
            self._pending = grant
            await self._client().grant_start(grant)
        except CannotConnect:
            return self._permissions_form(perms, {"base": "cannot_connect"})
        except (ApiError, AuthError) as err:
            return await self._grant_failed(err, lambda e: self._permissions_form(perms, e))
        return self._grant_form({})

    async def _grant_failed(
        self, err: ApiError | AuthError, form: Callable[[dict[str, str]], ConfigFlowResult]
    ) -> ConfigFlowResult:
        """Abort, finish or show `form` with an error, depending on the answer."""
        if isinstance(err, AuthError):
            self.config_entry.async_start_reauth(self.hass)
            return self.async_abort(reason="reauth_required")
        if err.code == "already_allowed":
            return await self._permissions_done()
        if err.code == "locked":
            minutes = max(1, round((err.retry_after or 0) / 60))
            return self.async_abort(
                reason="locked", description_placeholders={"minutes": str(minutes)}
            )
        if err.code in ("notify_failed", "random_unavailable"):
            return self.async_abort(reason="cannot_show_code")
        return form(self._grant_error(err))

    def _grant_error(self, err: ApiError) -> dict[str, str]:
        if err.code == "wrong_code":
            self._placeholders["attempts_left"] = str(err.attempts_left or 0)
            return {"base": "wrong_code"}
        if err.code in ("no_code", "code_exhausted"):
            return {"base": "code_expired"}
        if err.code == "rate_limited":
            self._placeholders["retry_after"] = str(err.retry_after or 30)
            return {"base": "rate_limited"}
        if err.code == "not_available":
            return {"base": "not_available"}
        if err.code == "bad_request":
            return {"base": "invalid_code"}
        if err.code == "internal":
            return {"base": "save_failed"}
        return {"base": "cannot_connect"}

    def _grant_form(self, errors: dict[str, str]) -> ConfigFlowResult:
        self._placeholders.setdefault("attempts_left", "5")
        self._placeholders.setdefault("retry_after", "30")
        schema = vol.Schema(
            {
                vol.Optional("code", default=""): str,
                vol.Optional("new_code", default=False): bool,
            }
        )
        return self.async_show_form(
            step_id="grant_code",
            data_schema=schema,
            errors=errors,
            description_placeholders=dict(self._placeholders),
        )

    async def async_step_grant_code(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is None:
            return self._grant_form({})
        code = user_input.get("code", "").strip()
        if not user_input.get("new_code") and not (len(code) == 6 and code.isdigit()):
            return self._grant_form({"base": "invalid_code"})
        try:
            if user_input.get("new_code"):
                await self._client().grant_start(self._pending)
                return self._grant_form({})
            await self._client().grant_finish(code)
        except CannotConnect:
            return self._grant_form({"base": "cannot_connect"})
        except (ApiError, AuthError) as err:
            return await self._grant_failed(err, self._grant_form)
        return await self._permissions_done()

    async def _store_info(self, perms: dict[str, dict[str, bool]] | None = None) -> None:
        """Store the console's new /info (or at least `perms`) in the entry and
        schedule a reload: entities depend on it, and OptionsFlowWithReload does
        not reload when the options themselves stay the same."""
        entry = self.config_entry
        try:
            info = _clean_info(await self._client().info())
        except (CannotConnect, ApiError, AuthError):
            info = None
            if perms is not None:
                info = {**entry.data.get(CONF_INFO, {}), "permissions": perms}
        if info is not None:
            self.hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_INFO: info}
            )
            if hasattr(entry, "runtime_data"):
                entry.runtime_data.info = info
        self.hass.config_entries.async_schedule_reload(entry.entry_id)

    async def _permissions_done(self) -> ConfigFlowResult:
        await self._store_info()
        return self.async_create_entry(data=dict(self.config_entry.options))

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
