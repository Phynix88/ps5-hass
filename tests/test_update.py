"""Update entity: a newer payload on GitHub."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.ps5_hass.const import PAYLOAD_RELEASE_INTERVAL, PAYLOAD_RELEASE_URL

from .conftest import entry_for
from .helpers import tick, tick_until

ENTITY = "update.ps5_payload"
DDP = "custom_components.ps5_hass.coordinator.ddp_status"
SESSION = "custom_components.ps5_hass.update.async_get_clientsession"


def feed(version: str = "v0.9.0") -> dict:
    """payloads.json as the payload repo publishes it."""
    return {
        "name": "ps5-hass",
        "payloads": [
            {"id": "other", "name": "Other", "version": "v7.7.7"},
            {"id": "ps5-hass", "name": "PS5 Home Assistant bridge", "version": version},
        ],
    }


@pytest.fixture
async def github(hass: HomeAssistant) -> AsyncIterator[AiohttpClientMocker]:
    """Answers for GitHub only; the fake payload keeps its real session."""
    mocker = AiohttpClientMocker()
    session = mocker.create_session(hass.loop)
    with patch(SESSION, return_value=session):
        yield mocker
    await session.close()


async def _setup(hass, fake, version: str = "v0.3.1"):
    fake.version = version
    entry = entry_for(fake)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def test_shows_update_when_newer(hass: HomeAssistant, fake, github) -> None:
    github.get(PAYLOAD_RELEASE_URL, json=feed("v0.9.0"))
    await _setup(hass, fake)
    state = hass.states.get(ENTITY)
    assert state.state == "on"
    assert state.attributes["installed_version"] == "0.3.1"
    assert state.attributes["latest_version"] == "0.9.0"
    assert state.attributes["release_url"] == (
        "https://github.com/Phynix88/ps5-hass-payload/releases/tag/v0.9.0"
    )
    assert state.attributes["supported_features"] == 0  # no install from HA
    assert state.attributes["release_summary"]
    assert github.call_count == 1


async def test_no_update_when_equal(hass: HomeAssistant, fake, github) -> None:
    github.get(PAYLOAD_RELEASE_URL, json=feed("v0.3.1"))
    await _setup(hass, fake, version="v0.3.1")
    state = hass.states.get(ENTITY)
    assert state.state == "off"
    assert state.attributes["installed_version"] == "0.3.1"
    assert state.attributes["latest_version"] == "0.3.1"


async def test_older_feed_is_no_update(hass: HomeAssistant, fake, github) -> None:
    github.get(PAYLOAD_RELEASE_URL, json=feed("v0.3.1"))
    await _setup(hass, fake, version="v0.4.0")
    assert hass.states.get(ENTITY).state == "off"


@pytest.mark.parametrize("installed", ["dev", "host"])
async def test_dev_builds_never_claim_an_update(hass, fake, github, installed) -> None:
    github.get(PAYLOAD_RELEASE_URL, json=feed("v0.9.0"))
    await _setup(hass, fake, version=installed)
    state = hass.states.get(ENTITY)
    assert state.state == "off"
    assert state.attributes["installed_version"] == installed


async def test_network_error_unknown_no_spam(
    hass: HomeAssistant, fake, github, freezer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    github.get(PAYLOAD_RELEASE_URL, exc=ClientError("offline"))
    await _setup(hass, fake)

    def warnings() -> list[str]:
        return [
            r.getMessage()
            for r in caplog.records
            if r.levelno >= logging.WARNING and "GitHub" in r.getMessage()
        ]

    state = hass.states.get(ENTITY)
    assert state.state == "unknown"
    assert state.attributes["installed_version"] == "0.3.1"
    assert state.attributes["latest_version"] is None
    assert len(warnings()) == 1

    for _ in range(3):  # the outage goes on: still one warning
        await tick(hass, freezer, PAYLOAD_RELEASE_INTERVAL)
    assert github.call_count == 4
    assert len(warnings()) == 1
    assert hass.states.get(ENTITY).state == "unknown"

    github.clear_requests()
    github.get(PAYLOAD_RELEASE_URL, json=feed("v0.9.0"))
    await tick(hass, freezer, PAYLOAD_RELEASE_INTERVAL)
    assert hass.states.get(ENTITY).state == "on"

    github.clear_requests()
    github.get(PAYLOAD_RELEASE_URL, status=503)
    await tick(hass, freezer, PAYLOAD_RELEASE_INTERVAL)
    assert hass.states.get(ENTITY).state == "unknown"
    assert len(warnings()) == 2  # a new outage may warn again


@pytest.mark.parametrize(
    "answer",
    [
        {"status": 404},
        {"exc": TimeoutError()},
        {"text": "not json"},
        {"json": ["not", "an", "object"]},
        {"json": {"payloads": []}},
        {"json": {"payloads": [{"id": "other", "version": "v1.0.0"}]}},
        {"json": {"payloads": [{"id": "ps5-hass"}]}},
        {"json": {"payloads": [{"id": "ps5-hass", "version": 3}]}},
    ],
)
async def test_bad_answers_are_unknown(hass, fake, github, answer) -> None:
    github.get(PAYLOAD_RELEASE_URL, **answer)
    await _setup(hass, fake)
    assert hass.states.get(ENTITY).state == "unknown"


async def test_update_found_on_later_poll(hass: HomeAssistant, fake, github, freezer) -> None:
    github.get(PAYLOAD_RELEASE_URL, json=feed("v0.3.1"))
    await _setup(hass, fake)
    assert hass.states.get(ENTITY).state == "off"
    github.clear_requests()
    github.get(PAYLOAD_RELEASE_URL, json=feed("v0.4.0"))
    await tick(hass, freezer, PAYLOAD_RELEASE_INTERVAL)
    assert hass.states.get(ENTITY).state == "on"


async def test_replaced_payload_clears_the_update_at_once(
    hass: HomeAssistant, fake, github, freezer
) -> None:
    """The owner replaces the payload on the console: no waiting for the next GitHub check."""
    github.get(PAYLOAD_RELEASE_URL, json=feed("v0.4.0"))
    await _setup(hass, fake, version="v0.3.1")
    state = hass.states.get(ENTITY)
    assert state.state == "on" and state.attributes["installed_version"] == "0.3.1"

    fake.down = True  # the console reloads the payload
    with patch(DDP, AsyncMock(return_value="on")):
        await tick_until(hass, freezer, lambda: hass.states.get("sensor.ps5_power").state == "on"
                         and hass.states.get("sensor.ps5_cpu_temperature").state == "unavailable")
        fake.version = "v0.4.0"
        fake.down = False
        await tick_until(
            hass, freezer,
            lambda: hass.states.get(ENTITY).attributes["installed_version"] == "0.4.0",
        )
    state = hass.states.get(ENTITY)
    assert state.state == "off"
    assert github.call_count == 1  # no new GitHub check was needed


async def test_summary_only_with_an_update(hass: HomeAssistant, fake, github) -> None:
    github.get(PAYLOAD_RELEASE_URL, json=feed("v0.3.1"))
    await _setup(hass, fake, version="v0.3.1")
    assert hass.states.get(ENTITY).attributes["release_summary"] is None
