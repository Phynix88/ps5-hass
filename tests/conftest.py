"""Shared fixtures."""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ps5_hass.const import (
    CONF_CLIENT_ID,
    CONF_INFO,
    CONF_TOKEN,
    DOMAIN,
)

from .fake_payload import FakePayload


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom_components/ in every test."""


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
