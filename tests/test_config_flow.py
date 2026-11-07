"""Config flow: setup, pairing, zeroconf, reauth, options, removal."""

from __future__ import annotations

from ipaddress import ip_address

from homeassistant.config_entries import SOURCE_USER, SOURCE_ZEROCONF
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from custom_components.ps5_hass.const import (
    CONF_CLIENT_ID,
    CONF_INFO,
    CONF_TOKEN,
    DOMAIN,
    OPT_FAVORITES,
    OPT_SOURCES,
)

from .conftest import entry_for
from .fake_payload import FakePayload


async def _to_pair_step(hass: HomeAssistant, fake: FakePayload) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port}
    )
    assert result["type"] is FlowResultType.FORM, result
    assert result["step_id"] == "pair"
    return result


def _zc(fake: FakePayload, api: str = "1", port: int | None = None) -> ZeroconfServiceInfo:
    return ZeroconfServiceInfo(
        ip_address=ip_address("127.0.0.1"),
        ip_addresses=[ip_address("127.0.0.1")],
        port=port or fake.port,
        hostname="ps5-hass-001122.local.",
        type="_ps5-hass._tcp.local.",
        name="PS5 001122._ps5-hass._tcp.local.",
        properties={"id": fake.device_id, "api": api, "version": "0.1.0", "paired": "0"},
    )


async def test_user_flow_pairs_and_creates_entry(hass: HomeAssistant, fake: FakePayload) -> None:
    result = await _to_pair_step(hass, fake)
    assert any(c[1] == "/pair/start" for c in fake.calls)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": fake.code})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "PS5"
    data = result["data"]
    assert data[CONF_HOST] == "127.0.0.1" and data[CONF_PORT] == fake.port
    assert data[CONF_TOKEN] == fake.tokens[data[CONF_CLIENT_ID]]
    assert data[CONF_INFO]["device_id"] == fake.device_id
    assert "result" not in data[CONF_INFO]
    assert result["result"].unique_id == fake.device_id


async def test_user_cannot_connect(hass: HomeAssistant, fake: FakePayload) -> None:
    await fake.stop()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_wrong_code_shows_attempts(hass: HomeAssistant, fake: FakePayload) -> None:
    result = await _to_pair_step(hass, fake)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "000000"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "wrong_code"}
    assert result["description_placeholders"]["attempts_left"] == "4"


async def test_new_code_checkbox_restarts(hass: HomeAssistant, fake: FakePayload) -> None:
    result = await _to_pair_step(hass, fake)
    starts = sum(1 for c in fake.calls if c[1] == "/pair/start")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"code": "", "new_code": True}
    )
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "pair"
    assert sum(1 for c in fake.calls if c[1] == "/pair/start") == starts + 1
    assert not result["errors"]


async def test_code_exhausted_then_new_code(hass: HomeAssistant, fake: FakePayload) -> None:
    result = await _to_pair_step(hass, fake)
    for _ in range(5):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "000000"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": fake.code})
    assert result["errors"] == {"base": "code_expired"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"code": "", "new_code": True}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": fake.code})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_rate_limited_shows_wait(hass: HomeAssistant, fake: FakePayload) -> None:
    result = await _to_pair_step(hass, fake)
    fake.fail["/pair/start"] = (429, {"error": "rate_limited", "retry_after": 21})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"code": "", "new_code": True}
    )
    assert result["errors"] == {"base": "rate_limited"}
    assert result["description_placeholders"]["retry_after"] == "21"


async def test_not_on_asks_to_turn_the_console_on(hass: HomeAssistant, fake: FakePayload) -> None:
    """Pairing shows its code on the TV: outside "on" the payload answers 409 not_on."""
    result = await _to_pair_step(hass, fake)
    fake.fail["/pair/start"] = (409, {"error": "not_on"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"code": "", "new_code": True}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_on"}
    fake.fail["/pair/finish"] = (409, {"error": "not_on"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "123456"})
    assert result["errors"] == {"base": "not_on"}


async def test_locked_aborts(hass: HomeAssistant, fake: FakePayload) -> None:
    result = await _to_pair_step(hass, fake)
    fake.fail["/pair/finish"] = (429, {"error": "locked", "retry_after": 900})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": "000000"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "locked"
    assert result["description_placeholders"]["minutes"] == "15"


async def test_notify_failed_aborts(hass: HomeAssistant, fake: FakePayload) -> None:
    fake.fail["/pair/start"] = (500, {"error": "notify_failed"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "127.0.0.1", CONF_PORT: fake.port}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_show_code"


async def test_token_limit_aborts(hass: HomeAssistant, fake: FakePayload) -> None:
    result = await _to_pair_step(hass, fake)
    fake.fail["/pair/finish"] = (409, {"error": "token_limit"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": fake.code})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "token_limit"


async def test_zeroconf_new_device_confirm_and_pair(hass: HomeAssistant, fake: FakePayload) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zc(fake)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "zeroconf_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pair"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": fake.code})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_PORT] == fake.port


async def test_zeroconf_known_device_updates_host_port(hass: HomeAssistant, fake: FakePayload) -> None:
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zc(fake, port=8182)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_PORT] == 8182


async def test_zeroconf_api_2_aborts(hass: HomeAssistant, fake: FakePayload) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_ZEROCONF}, data=_zc(fake, api="2")
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unsupported_api"


async def test_reauth_keeps_client_id(hass: HomeAssistant, fake: FakePayload) -> None:
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    client_id = entry.data[CONF_CLIENT_ID]
    fake.tokens.clear()  # console reset
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "pair"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"code": fake.code})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_CLIENT_ID] == client_id
    assert entry.data[CONF_TOKEN] == fake.tokens[client_id]


async def test_options_single_step(hass: HomeAssistant, fake: FakePayload) -> None:
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert set(result["data_schema"].schema) == {OPT_SOURCES, OPT_FAVORITES}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {OPT_SOURCES: "games", OPT_FAVORITES: ["PPSA96708", "PPSA99169"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        OPT_SOURCES: "games",
        OPT_FAVORITES: ["PPSA96708", "PPSA99169"],
        "favorite_names": {"PPSA96708": "Hikari", "PPSA99169": "RetroArch"},
    }


async def test_remove_entry_revokes_and_ignores_errors(hass: HomeAssistant, fake: FakePayload) -> None:
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await hass.config_entries.async_remove(entry.entry_id)
    assert not fake.tokens
    # console off: removal still works
    entry2 = entry_for(fake)
    entry2.add_to_hass(hass)
    await fake.stop()
    await hass.config_entries.async_remove(entry2.entry_id)
    assert hass.config_entries.async_get_entry(entry2.entry_id) is None
