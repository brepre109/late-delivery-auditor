"""Tolerant parsing of the date/time strings found in carrier and marketplace exports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo

_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%b-%Y", "%b %d %Y", "%b %d, %Y")
_TIME_FORMATS = ("%H:%M:%S", "%H:%M", "%I:%M:%S %p", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p")

_OFFSET_RE = re.compile(r"(?:\s*(Z|UTC|GMT)|\s*([+-])(\d{2}):?(\d{2}))$", re.IGNORECASE)


class TimeParseError(ValueError):
    pass


@dataclass(frozen=True)
class ParsedMoment:
    """A date with an optional time and an optional explicit UTC offset."""

    day: date
    at: time | None  # None => the source gave only a date
    tz: tzinfo | None  # None => naive (no offset in the source)

    @property
    def date_only(self) -> bool:
        return self.at is None


def _normalise(text: str) -> str:
    s = text.strip()
    s = re.sub(r"\b([ap])\.m\.?", lambda m: m.group(1) + "m", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s)
    return s


def parse_moment(text: str | None) -> ParsedMoment | None:
    """Parse ``text`` into a :class:`ParsedMoment`. Blank input returns ``None``.

    Accepts ISO 8601 (``2026-09-15``, ``2026-09-15T10:30``, ``2026-09-15T15:02:11Z``,
    ``2026-09-15 10:30:00-04:00``) and common US spreadsheet forms (``09/15/2026``,
    ``9/15/2026 10:30 AM``, ``9/15/2026 4:30 p.m.``, ``2026-09-15 10:30 -0400``).
    """
    if text is None or not str(text).strip():
        return None
    s = _normalise(str(text))

    tz: tzinfo | None = None
    m = _OFFSET_RE.search(s)
    # Only treat a trailing +hh:mm as an offset if there is a time part before it
    # (so "2026-09-15" is not read as "2026-09" with offset "-15").
    if m and (m.group(1) or re.search(r"\d:\d\d", s[: m.start()])):
        if m.group(1):
            tz = timezone.utc
        else:
            sign = 1 if m.group(2) == "+" else -1
            tz = timezone(sign * timedelta(hours=int(m.group(3)), minutes=int(m.group(4))))
        s = s[: m.start()].strip()

    s = s.replace("T", " ", 1) if re.match(r"\d{4}-\d{2}-\d{2}T", s) else s
    # Strip fractional seconds.
    s = re.sub(r"(\d{2}:\d{2}:\d{2})\.\d+", r"\1", s)

    for dfmt in _DATE_FORMATS:
        try:
            return ParsedMoment(datetime.strptime(s, dfmt).date(), None, tz)
        except ValueError:
            pass
        for tfmt in _TIME_FORMATS:
            try:
                dt = datetime.strptime(s, f"{dfmt} {tfmt}")
            except ValueError:
                continue
            return ParsedMoment(dt.date(), dt.time(), tz)
    raise TimeParseError(f"unrecognised date/time: {text!r}")


def parse_time_of_day(text: str) -> time:
    s = _normalise(text)
    for tfmt in _TIME_FORMATS:
        try:
            return datetime.strptime(s, tfmt).time()
        except ValueError:
            continue
    raise TimeParseError(f"unrecognised time of day: {text!r}")


def parse_date(text: str) -> date:
    m = parse_moment(text)
    if m is None:
        raise TimeParseError("empty date")
    return m.day


def to_aware(moment: ParsedMoment, local_zone: tzinfo, default_time: time, naive_zone: tzinfo | None = None) -> datetime:
    """Build an aware datetime.

    * explicit offset in the source wins;
    * otherwise the wall-clock value is interpreted in ``naive_zone`` (if given) or
      ``local_zone``;
    * a date-only value gets ``default_time``.
    """
    at = moment.at if moment.at is not None else default_time
    naive = datetime.combine(moment.day, at)
    if moment.tz is not None:
        return naive.replace(tzinfo=moment.tz)
    zone = naive_zone or local_zone
    return naive.replace(tzinfo=zone)


def zone_from_name(name: str | None) -> tzinfo | None:
    if not name or not name.strip():
        return None
    n = name.strip()
    if n.upper() in ("UTC", "Z", "GMT"):
        return timezone.utc
    return ZoneInfo(n)


def fmt_duration(delta: timedelta) -> str:
    """``timedelta`` -> ``"1d 2h 05m"`` style string (negative values get a minus)."""
    secs = int(delta.total_seconds())
    sign = "-" if secs < 0 else ""
    secs = abs(secs)
    d, rem = divmod(secs, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}d")
    if d or h:
        parts.append(f"{h}h")
    parts.append(f"{m:02d}m" if (d or h) else f"{m}m")
    if not d and not h and not m:
        return f"{sign}{s}s"
    return sign + " ".join(parts)
