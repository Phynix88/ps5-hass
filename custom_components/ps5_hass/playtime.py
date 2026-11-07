"""Play time: day and week totals in Home Assistant's time zone, and its coordinator."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api import ApiError, AuthError, CannotConnect, Ps5HassClient
from .const import DOMAIN, PLAYTIME_INTERVAL

if TYPE_CHECKING:
    from .coordinator import ConsoleState, StateCoordinator

_LOGGER = logging.getLogger(__package__)

GAMES = frozenset({"game"})


def _midnight(day: date, tz: ZoneInfo) -> int:
    """Unix time of 00:00 local on `day` (a day that skips midnight starts at the jump)."""
    return int(datetime.combine(day, time.min, tzinfo=tz).timestamp())


def split_seconds(start: datetime, seconds: int, tz: ZoneInfo) -> dict[date, int]:
    """Seconds per local day for a span of `seconds` from `start`; days are cut at local midnight."""
    out: dict[date, int] = {}
    pos = int(start.timestamp())
    end = pos + seconds
    while pos < end:
        day = datetime.fromtimestamp(pos, tz).date()
        # a day is 23 or 25 hours long at a DST change: take the next midnight from the clock
        cut = min(end, _midnight(day + timedelta(days=1), tz))
        if cut <= pos:  # a zone that skips its midnight: never loop on one second
            cut = end
        out[day] = out.get(day, 0) + cut - pos
        pos = cut
    return out


def totals(
    sessions: list[dict[str, Any]],
    current: dict[str, Any] | None,
    now: datetime,
    tz: ZoneInfo,
    kinds: frozenset[str] = GAMES,
) -> tuple[int, int]:
    """(seconds today, seconds this week) of the given kinds; the week starts on Monday."""
    today = now.astimezone(tz).date()
    monday = today - timedelta(days=today.weekday())
    today_s = week_s = 0
    spans = [*sessions, *([current] if current else [])]
    for span in spans:
        if span.get("kind") not in kinds:
            continue
        start = datetime.fromtimestamp(span["start"], tz)
        for day, secs in split_seconds(start, span["duration_s"], tz).items():
            if day == today:
                today_s += secs
            if monday <= day <= today:
                week_s += secs
    return today_s, week_s


def week_request_start(now: datetime, tz: ZoneInfo) -> int:
    """`since` for /playtime: Monday 00:00 of this week minus one day, so the week is complete."""
    today = now.astimezone(tz).date()
    monday = today - timedelta(days=today.weekday())
    return _midnight(monday - timedelta(days=1), tz)


def games(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The titles of kind game from a coordinator answer."""
    return [t for t in (data or {}).get("titles", []) if t.get("kind") == "game"]


class PlaytimeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """/playtime every minute and at once when the running app changes.

    `data` is the last /playtime answer plus `today_s` and `week_s`; it is empty until the
    first answer. While the payload is away (the console is off or in rest mode) the last
    answer stays, and only the totals are worked out again, so "today" still ends at midnight.
    No value goes down: per-game totals keep their highest value, today and this week theirs
    until the day or week changes.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: Ps5HassClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} playtime",
            update_interval=timedelta(seconds=PLAYTIME_INTERVAL),
        )
        self.client = client
        self.state: StateCoordinator | None = None
        self._seen: tuple[bool, str | None] | None = None
        # highest values shown: the sensors are total_increasing, and the payload can lose up to a
        # minute (checkpoint, update, power cut). Today and week start again on a new day / week.
        self._titles: dict[str, dict[str, Any]] = {}
        self._day: tuple[date, int] | None = None
        self._week: tuple[date, int] | None = None

    def follow(self, state: StateCoordinator) -> Callable[[], None]:
        """Refresh when the payload comes back or the running app changes; returns the unsubscribe."""
        self.state = state
        self._seen = self._situation(state.data)
        return state.async_add_listener(self._state_changed)

    @staticmethod
    def _situation(console: ConsoleState | None) -> tuple[bool, str | None]:
        if console is None or not console.payload or console.state is None:
            return (False, None)
        app = console.state.get("app")
        return (True, app["title_id"] if app else None)

    @callback
    def _state_changed(self) -> None:
        assert self.state is not None
        now = self._situation(self.state.data)
        if now != self._seen:
            self._seen = now
            self.hass.async_create_task(self.async_request_refresh())

    def _payload_up(self) -> bool:
        return self.state is None or self._situation(self.state.data)[0]

    async def _async_update_data(self) -> dict[str, Any]:
        tz = dt_util.get_time_zone(self.hass.config.time_zone) or dt_util.DEFAULT_TIME_ZONE
        now = dt_util.utcnow()
        raw = {k: v for k, v in (self.data or {}).items() if k not in ("today_s", "week_s")}
        if self._payload_up():
            try:
                answer = await self.client.playtime(week_request_start(now, tz))
            except AuthError as err:
                raise ConfigEntryAuthFailed(str(err)) from err
            except (CannotConnect, ApiError):
                pass  # keep what we have
            else:
                raw = {k: answer[k] for k in ("foreground", "titles", "sessions", "current")}
                raw["titles"] = self._keep_titles(raw["titles"])
        if not raw:
            return {}
        today_s, week_s = totals(raw["sessions"], raw["current"], now, tz)
        today = now.astimezone(tz).date()
        monday = today - timedelta(days=today.weekday())
        self._day = (today, _peak(self._day, today, today_s))
        self._week = (monday, _peak(self._week, monday, week_s))
        return {**raw, "today_s": self._day[1], "week_s": self._week[1]}

    def _keep_titles(self, titles: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Totals never go down, and a title missing from an answer keeps its last value."""
        for title in titles:
            known = self._titles.get(title["title_id"])
            if known is not None and known["total_s"] > title["total_s"]:
                title = {**title, "total_s": known["total_s"]}
            self._titles[title["title_id"]] = title
        return list(self._titles.values())


def _peak(last: tuple[date, int] | None, key: date, value: int) -> int:
    """The higher of value and the last one while the period (day or week) is the same."""
    return max(value, last[1]) if last is not None and last[0] == key else value
