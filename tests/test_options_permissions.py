"""Options → console permissions with a code on the TV."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ps5_hass.const import CONF_INFO, OPT_FAVORITES, OPT_SOURCES

from .conftest import entry_for
from .fake_payload import FakePayload


async def _setup(hass: HomeAssistant, fake: FakePayload, **options):
    entry = entry_for(fake, **options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _open_permissions(hass: HomeAssistant, entry):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "permissions"}
    )


def _grant_calls(fake: FakePayload) -> list[str]:
    return [path for _, path, _, _ in fake.calls if path.startswith("/grant/")]


async def test_menu_has_sources_and_permissions(hass: HomeAssistant, fake: FakePayload) -> None:
    entry = await _setup(hass, fake)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["sources", "permissions"]


async def test_permissions_prefilled_and_unavailable_hidden(
    hass: HomeAssistant, fake: FakePayload
) -> None:
    fake.power_actions = True
    fake.available["fan_control"] = False
    entry = await _setup(hass, fake)
    result = await _open_permissions(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "permissions"
    fields = {str(k): k for k in result["data_schema"].schema}
    assert set(fields) == {"power_actions", "power_off"}
    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults == {"power_actions": True, "power_off": False}


async def test_revoke_only_no_code(hass: HomeAssistant, fake: FakePayload) -> None:
    fake.power_off = True
    entry = await _setup(hass, fake, **{OPT_SOURCES: "games", OPT_FAVORITES: []})
    assert hass.states.get("button.ps5_power_off") is not None
    result = await _open_permissions(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"power_actions": False, "power_off": False, "fan_control": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert _grant_calls(fake) == ["/grant/revoke"]
    assert fake.power_off is False
    assert entry.options == {OPT_SOURCES: "games", OPT_FAVORITES: []}
    assert entry.data[CONF_INFO]["permissions"]["power_off"]["allowed"] is False
    state = hass.states.get("button.ps5_power_off")
    assert state is None or state.state == "unavailable"  # not created after the reload


async def test_grant_with_code(hass: HomeAssistant, fake: FakePayload) -> None:
    entry = await _setup(hass, fake)
    assert hass.states.get("button.ps5_power_off") is None
    result = await _open_permissions(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"power_actions": False, "power_off": True, "fan_control": False}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "grant_code"
    assert fake.grant_pending == ["power_off"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"code": "000000", "new_code": False}
    )
    assert result["errors"] == {"base": "wrong_code"}
    assert result["description_placeholders"]["attempts_left"] == "4"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"code": fake.grant_code, "new_code": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert fake.power_off is True
    assert hass.states.get("button.ps5_power_off") is not None


async def test_grant_new_code_and_rate_limited(hass: HomeAssistant, fake: FakePayload) -> None:
    entry = await _setup(hass, fake)
    result = await _open_permissions(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"power_actions": True, "power_off": False, "fan_control": False}
    )
    assert result["step_id"] == "grant_code"
    fake.fail["/grant/start"] = (429, {"error": "rate_limited", "retry_after": 12})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"code": "", "new_code": True}
    )
    assert result["errors"] == {"base": "rate_limited"}
    assert result["description_placeholders"]["retry_after"] == "12"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"code": "", "new_code": True}
    )
    assert result["errors"] == {}
    assert _grant_calls(fake).count("/grant/start") == 3  # first, failed, again


async def test_grant_locked_aborts(hass: HomeAssistant, fake: FakePayload) -> None:
    entry = await _setup(hass, fake)
    result = await _open_permissions(hass, entry)
    fake.fail["/grant/start"] = (429, {"error": "locked", "retry_after": 900})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"power_actions": True, "power_off": False, "fan_control": False}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "locked"
    assert result["description_placeholders"]["minutes"] == "15"


async def test_mixed_grant_and_revoke(hass: HomeAssistant, fake: FakePayload) -> None:
    fake.fan_control = True
    entry = await _setup(hass, fake)
    result = await _open_permissions(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"power_actions": True, "power_off": False, "fan_control": False}
    )
    assert result["step_id"] == "grant_code"
    assert fake.fan_control is False  # revoked at once
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"code": fake.grant_code, "new_code": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert fake.power_actions is True
    assert _grant_calls(fake) == ["/grant/revoke", "/grant/start", "/grant/finish"]


async def test_nothing_changed_no_calls(hass: HomeAssistant, fake: FakePayload) -> None:
    fake.power_off = True
    entry = await _setup(hass, fake, **{OPT_SOURCES: "all"})
    result = await _open_permissions(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"power_actions": False, "power_off": True, "fan_control": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert _grant_calls(fake) == []
    assert entry.options == {OPT_SOURCES: "all"}


async def test_payload_too_old_aborts(hass: HomeAssistant, fake: FakePayload) -> None:
    fake.legacy_v02 = True
    entry = await _setup(hass, fake)
    result = await _open_permissions(hass, entry)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "payload_too_old"


async def test_permissions_console_off(hass: HomeAssistant, fake: FakePayload) -> None:
    entry = await _setup(hass, fake, **{OPT_SOURCES: "games"})
    fake.down = True
    result = await _open_permissions(hass, entry)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.options == {OPT_SOURCES: "games"}


async def test_permissions_change_triggers_reload(hass: HomeAssistant, fake: FakePayload) -> None:
    from custom_components.ps5_hass.coordinator import info_needs_reload

    old = fake.info()
    fake.available["power_off"] = False  # only /info.permissions changes
    assert old["capabilities"] == fake.info()["capabilities"]
    assert info_needs_reload(old, fake.info())


async def test_rate_limited_on_permissions_form_has_placeholder(
    hass: HomeAssistant, fake: FakePayload
) -> None:
    entry = await _setup(hass, fake)
    result = await _open_permissions(hass, entry)
    fake.fail["/grant/start"] = (429, {"error": "rate_limited", "retry_after": 12})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"power_actions": True, "power_off": False, "fan_control": False}
    )
    assert result["step_id"] == "permissions"
    assert result["errors"] == {"base": "rate_limited"}
    assert result["description_placeholders"]["retry_after"] == "12"


async def test_mixed_grant_fails_after_revoke_uses_new_state(
    hass: HomeAssistant, fake: FakePayload
) -> None:
    fake.fan_control = True
    entry = await _setup(hass, fake)
    result = await _open_permissions(hass, entry)
    fake.fail["/grant/start"] = (429, {"error": "rate_limited", "retry_after": 12})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"power_actions": True, "power_off": False, "fan_control": False}
    )
    assert fake.fan_control is False
    defaults = {str(k): k.default() for k in result["data_schema"].schema}
    assert defaults["fan_control"] is False  # not shown as ticked again
    assert entry.data[CONF_INFO]["permissions"]["fan_control"]["allowed"] is False


def test_permissions_description_explains_hidden_ones() -> None:
    import json
    import pathlib

    comp = pathlib.Path(__file__).parent.parent / "custom_components" / "ps5_hass"
    for name in ("strings.json", "translations/en.json"):
        text = json.loads((comp / name).read_text())["options"]["step"]["permissions"]["description"]
        assert "cannot do are not shown" in text
    de = json.loads((comp / "translations/de.json").read_text())
    assert "nicht angezeigt" in de["options"]["step"]["permissions"]["description"]


async def _tick_power_actions(hass, entry):
    result = await _open_permissions(hass, entry)
    return await hass.config_entries.options.async_configure(
        result["flow_id"], {"power_actions": True, "power_off": False, "fan_control": False}
    )


async def test_grant_notify_failed_aborts_cannot_show_code(hass, fake) -> None:
    entry = await _setup(hass, fake)
    fake.fail["/grant/start"] = (500, {"error": "notify_failed"})
    result = await _tick_power_actions(hass, entry)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_show_code"


async def test_grant_not_available_error(hass, fake) -> None:
    entry = await _setup(hass, fake)
    fake.fail["/grant/start"] = (409, {"error": "not_available"})
    result = await _tick_power_actions(hass, entry)
    assert result["errors"] == {"base": "not_available"}


async def test_grant_code_must_be_6_digits(hass, fake) -> None:
    entry = await _setup(hass, fake)
    result = await _tick_power_actions(hass, entry)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"code": "12345", "new_code": False}
    )
    assert result["errors"] == {"base": "invalid_code"}
    assert "/grant/finish" not in _grant_calls(fake)


async def test_grant_finish_save_failed(hass, fake) -> None:
    entry = await _setup(hass, fake)
    result = await _tick_power_actions(hass, entry)
    fake.fail["/grant/finish"] = (500, {"error": "internal"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"code": fake.grant_code, "new_code": False}
    )
    assert result["errors"] == {"base": "save_failed"}


async def test_grant_auth_error_starts_reauth(hass, fake) -> None:
    from homeassistant.config_entries import SOURCE_REAUTH

    entry = await _setup(hass, fake)
    result = await _open_permissions(hass, entry)
    fake.tokens.clear()
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"power_actions": True, "power_off": False, "fan_control": False}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_required"
    await hass.async_block_till_done()
    assert len(list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))) == 1


async def test_already_allowed_after_new_code_finishes(hass, fake) -> None:
    entry = await _setup(hass, fake)
    result = await _tick_power_actions(hass, entry)
    fake.fail["/grant/start"] = (409, {"error": "already_allowed"})
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"code": "", "new_code": True}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_nothing_changed_no_reload(hass, fake) -> None:
    fake.power_off = True
    entry = await _setup(hass, fake)
    first = entry.runtime_data
    result = await _open_permissions(hass, entry)
    before = len(fake.calls)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"power_actions": False, "power_off": True, "fan_control": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert fake.calls[before:] == []
    assert entry.runtime_data is first


async def test_v02_entry_console_off_is_cannot_connect(hass, fake) -> None:
    fake.legacy_v02 = True
    entry = await _setup(hass, fake)
    assert "permissions" not in entry.data[CONF_INFO]
    fake.down = True
    result = await _open_permissions(hass, entry)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_connect"
