"""Day and week totals from sessions: local midnight, Monday, DST, games only."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from custom_components.ps5_hass.playtime import split_seconds, totals

BERLIN = ZoneInfo("Europe/Berlin")
UTC = ZoneInfo("UTC")


def _ts(*args: int, tz: ZoneInfo = BERLIN) -> int:
    return int(datetime(*args, tzinfo=tz).timestamp())


def test_split_over_midnight() -> None:
    start = datetime(2026, 11, 3, 23, 30, tzinfo=BERLIN)
    assert split_seconds(start, 3600, BERLIN) == {date(2026, 11, 3): 1800, date(2026, 11, 4): 1800}
    # the same instant given in UTC splits at the local midnight, not at UTC's
    assert split_seconds(start.astimezone(UTC), 3600, BERLIN) == {
        date(2026, 11, 3): 1800,
        date(2026, 11, 4): 1800,
    }
    # several days, and nothing for an empty or negative span
    assert split_seconds(datetime(2026, 11, 3, 22, 0, tzinfo=BERLIN), 3 * 86400, BERLIN) == {
        date(2026, 11, 3): 7200,
        date(2026, 11, 4): 86400,
        date(2026, 11, 5): 86400,
        date(2026, 11, 6): 79200,
    }
    assert split_seconds(start, 0, BERLIN) == {}
    assert split_seconds(start, -5, BERLIN) == {}


def test_week_starts_monday() -> None:
    # Sunday 2026-11-08 23:00 for two hours: one hour on Sunday, one on Monday the 9th
    sessions = [{"kind": "game", "start": _ts(2026, 11, 8, 23), "duration_s": 7200}]
    sunday = datetime(2026, 11, 8, 23, 59, tzinfo=BERLIN)
    assert totals(sessions, None, sunday, BERLIN) == (3600, 3600)
    monday = datetime(2026, 11, 9, 12, 0, tzinfo=BERLIN)
    assert totals(sessions, None, monday, BERLIN) == (3600, 3600)  # Monday: the new week
    tuesday = datetime(2026, 11, 10, 12, 0, tzinfo=BERLIN)
    assert totals(sessions, None, tuesday, BERLIN) == (0, 3600)  # Sunday's hour is last week
    # a week earlier session (Mon 2 .. Sun 8) counts in that week only
    earlier = [{"kind": "game", "start": _ts(2026, 11, 8, 10), "duration_s": 600}]
    assert totals(earlier, None, monday, BERLIN) == (0, 0)
    assert totals(earlier, None, sunday, BERLIN) == (600, 600)


def test_dst_day_23h() -> None:
    # 2026-03-29: clocks go forward in Europe/Berlin, the local day has 23 hours
    start = datetime(2026, 3, 29, 0, 0, tzinfo=BERLIN)
    assert split_seconds(start, 24 * 3600, BERLIN) == {
        date(2026, 3, 29): 23 * 3600,
        date(2026, 3, 30): 3600,
    }
    # 2026-10-25: clocks go back, the day has 25 hours
    start = datetime(2026, 10, 25, 0, 0, tzinfo=BERLIN)
    assert split_seconds(start, 26 * 3600, BERLIN) == {
        date(2026, 10, 25): 25 * 3600,
        date(2026, 10, 26): 3600,
    }
    sessions = [{"kind": "game", "start": int(start.timestamp()), "duration_s": 25 * 3600}]
    now = datetime(2026, 10, 25, 23, 0, tzinfo=BERLIN)
    assert totals(sessions, None, now, BERLIN) == (25 * 3600, 25 * 3600)


def test_apps_not_counted() -> None:
    now = datetime(2026, 11, 4, 12, 0, tzinfo=BERLIN)
    start = _ts(2026, 11, 4, 9)
    sessions = [
        {"kind": "app", "start": start, "duration_s": 3600},
        {"kind": "game", "start": start + 3600, "duration_s": 1200},
    ]
    current = {"kind": "app", "start": start + 7200, "duration_s": 900}
    assert totals(sessions, current, now, BERLIN) == (1200, 1200)
    assert totals(sessions, current, now, BERLIN, frozenset({"game", "app"})) == (5700, 5700)


def test_current_session_included() -> None:
    now = datetime(2026, 11, 4, 12, 0, tzinfo=BERLIN)
    sessions = [{"kind": "game", "start": _ts(2026, 11, 4, 9), "duration_s": 600}]
    current = {"kind": "game", "start": _ts(2026, 11, 4, 11), "duration_s": 95}
    assert totals(sessions, current, now, BERLIN) == (695, 695)
    assert totals([], current, now, BERLIN) == (95, 95)
    assert totals(sessions, None, now, BERLIN) == (600, 600)
    # a running session across midnight splits as well
    over = {"kind": "game", "start": _ts(2026, 11, 3, 23, 50), "duration_s": 1200}
    assert totals([], over, now, BERLIN) == (600, 1200)
