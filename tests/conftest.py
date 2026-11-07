"""Shared fixtures."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ps5_hass.const import (
    CONF_CLIENT_ID,
    CONF_INFO,
    CONF_TOKEN,
    DOMAIN,
    PAYLOAD_RELEASE_URL,
)

from .fake_payload import FakePayload


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


@pytest.fixture(autouse=True)
async def no_github(hass: HomeAssistant) -> AsyncGenerator[None]:
    """Never reach GitHub from a test; test_update.py brings its own answers."""
    mocker = AiohttpClientMocker()
    mocker.get(PAYLOAD_RELEASE_URL, json={"payloads": [{"id": "ps5-hass", "version": "v0.1.0"}]})
    session = mocker.create_session(hass.loop)
    with patch("custom_components.ps5_hass.update.async_get_clientsession", return_value=session):
        yield
    await session.close()


@pytest.fixture
async def fake(socket_enabled: None) -> AsyncGenerator[FakePayload]:
    """A running fake payload on 127.0.0.1."""
    payload = FakePayload()
    await payload.start()
    yield payload
    await payload.stop()
    if not payload.expect_schema_errors:
        assert not payload.schema_errors, payload.schema_errors


def entry_for(fake: FakePayload, **options: object) -> MockConfigEntry:
    """A config entry already paired with `fake`."""
    token = fake.add_token("0b7e2f1c-1111-4222-8333-944445555666")
    info = fake.info()
    info.pop("result")
    return MockConfigEntry(
        domain=DOMAIN,
        title="PS5",
        unique_id=fake.device_id,
        data={
            "host": "127.0.0.1",
            "port": fake.port,
            CONF_TOKEN: token,
            CONF_CLIENT_ID: "0b7e2f1c-1111-4222-8333-944445555666",
            CONF_INFO: info,
        },
        options=dict(options),
    )
