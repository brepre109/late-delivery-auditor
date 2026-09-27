"""Decide, row by row, whether a delivered package is a guaranteed-service refund candidate."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from enum import Enum
from typing import Any, Iterable

from .rules import Carrier, Rule, RuleSet, normalise_service
from .timeparse import (
    ParsedMoment,
    TimeParseError,
    fmt_duration,
    parse_moment,
    parse_time_of_day,
    to_aware,
    zone_from_name,
)
from .timezones import dest_state, resolve_tz


class Status(str, Enum):
    CLAIM = "CLAIM"  # late against a guaranteed commitment, no exclusion, window open
    ON_TIME = "ON_TIME"
    NOT_GUARANTEED = "NOT_GUARANTEED"  # service has no (active) guarantee, or unknown
    EXCLUDED = "EXCLUDED"  # late, but an exception / holiday / destination rule removes it
    WINDOW_EXPIRED = "WINDOW_EXPIRED"  # late and eligible, but the claim deadline has passed
    PENDING = "PENDING"  # not delivered yet
    REVIEW = "REVIEW"  # data missing or ambiguous; a human should look
    ERROR = "ERROR"  # row could not be parsed


@dataclass
class Shipment:
    tracking: str
    carrier: str
    service: str
    ship_date: str = ""
    promised: str = ""
    delivered: str = ""
    first_attempt: str = ""
    dest_postal_code: str = ""
    dest_state: str = ""
    dest_tz: str = ""
    delivered_tz: str = ""
    exception_codes: str = ""
    residential: str = ""
    invoice_date: str = ""
    line: int = 0


@dataclass
class Result:
    shipment: Shipment
    status: Status
    reason: str
    rule: Rule | None = None
    carrier_code: str | None = None
    zone: tzinfo | None = None
    commitment: datetime | None = None  # after adjustments, destination-local
    promised_local: datetime | None = None  # as given, destination-local
    delivered_local: datetime | None = None
    late_by: timedelta | None = None
    claim_deadline: date | None = None
    file_from: date | None = None
    notes: list[str] = field(default_factory=list)

    def as_row(self) -> dict[str, str]:
        s = self.shipment
        r = self.rule
        late_min = ""
        if self.late_by is not None:
            late_min = f"{self.late_by.total_seconds() / 60:.1f}"
        return {
            "status": self.status.value,
            "tracking": s.tracking,
            "carrier": self.carrier_code or s.carrier,
            "service": s.service,
            "ship_date": s.ship_date,
            "promised": _iso(self.promised_local) or s.promised,
            "commitment_applied": _iso(self.commitment),
            "delivered_local": _iso(self.delivered_local),
            "late_by": fmt_duration(self.late_by) if self.late_by is not None else "",
            "late_by_minutes": late_min,
            "dest_tz": getattr(self.zone, "key", str(self.zone)) if self.zone else "",
            "rule_applied": r.id if r else "",
            "rule_verification": f"{r.verification} {r.verified_on}" if r else "",
            "claim_deadline": self.claim_deadline.isoformat() if self.claim_deadline else "",
            "file_from": self.file_from.isoformat() if self.file_from else "",
            "citation": r.citation if r else "",
            "source_url": r.source_url if r else "",
            "reason": "; ".join([self.reason, *self.notes]) if self.notes else self.reason,
        }




def _iso(dt: datetime | None) -> str:
    return dt.isoformat(timespec="minutes") if dt else ""


OUTPUT_COLUMNS = list(Result(Shipment("", "", ""), Status.ERROR, "").as_row().keys())


def _truthy(v: str) -> bool | None:
    s = (v or "").strip().lower()
    if s in ("1", "true", "yes", "y", "residential", "res"):
        return True
    if s in ("0", "false", "no", "n", "commercial", "business"):
        return False
    return None


def _codes(raw: str) -> list[str]:
    parts = re.split(r"[;,|]", raw or "")
    return [re.sub(r"[^A-Z0-9]+", "_", p.strip().upper()).strip("_") for p in parts if p.strip()]


def wednesday_before_thanksgiving(year: int) -> date:
    nov1 = date(year, 11, 1)
    first_thu = nov1 + timedelta(days=(3 - nov1.weekday()) % 7)
    return first_thu + timedelta(weeks=3) - timedelta(days=1)


def _md(text: str, year: int) -> date:
    m, d = (int(x) for x in text.split("-"))
    return date(year, m, d)


def _adjustment_matches_day(when: dict[str, Any], day: date) -> bool:
    if "dates" in when:
        return day.isoformat() in {str(d) for d in when["dates"]}
    if when.get("named_day") == "wednesday_before_thanksgiving":
        return day == wednesday_before_thanksgiving(day.year)
    if "days_before_christmas" in when:
        xmas = date(day.year, 12, 25)
        return xmas - timedelta(days=int(when["days_before_christmas"])) <= day < xmas
    if "month_day_range" in when:
        lo, hi = when["month_day_range"]
        return _md(lo, day.year) <= day <= _md(hi, day.year)
    return False


def add_business_days(start: date, n: int, holidays: set[date]) -> date:
    """Add ``n`` business days, skipping Sundays and ``holidays``."""
    d = start
    left = n
    while left > 0:
        d += timedelta(days=1)
        if d.weekday() != 6 and d not in holidays:
            left -= 1
    return d


@dataclass
class AuditConfig:
    as_of: date
    naive_delivered: str = "local"  # "local" (destination wall clock) or "utc"


def audit_shipment(s: Shipment, rules: RuleSet, cfg: AuditConfig) -> Result:
    try:
        return _audit(s, rules, cfg)
    except (TimeParseError, ValueError) as e:
        return Result(s, Status.ERROR, f"could not parse row: {e}")


def _audit(s: Shipment, rules: RuleSet, cfg: AuditConfig) -> Result:
    ship = parse_moment(s.ship_date)
    ship_day = ship.day if ship else None
    code = rules.carrier_code(s.carrier)
    rule, why = rules.match(code, s.service, ship_day)
    res = Result(s, Status.NOT_GUARANTEED, "", rule=rule, carrier_code=code)

    if rule is None:
        res.reason = f"{rules.default_reason} ({why})"
        return res
    if not rule.guaranteed:
        res.reason = f"no active guarantee for this service/ship date (rule {rule.id}; see citation)"
        return res
    carrier: Carrier = rules.carriers[rule.carrier]

    state = dest_state(s.dest_state, s.dest_postal_code)
    if state and state in rule.excluded_dest_states:
        res.status = Status.EXCLUDED
        res.reason = f"destination {state} is excluded from the guarantee (other than loss)"
        return res

    residential = _truthy(s.residential)
    svc = normalise_service(s.service, carrier.service_prefixes)
    if svc in rule.residential_excluded_services:
        if residential is True:
            res.status = Status.EXCLUDED
            res.reason = "residential delivery is not guaranteed for this service"
            return res
        if residential is None:
            res.notes.append("residential flag missing: this service is not guaranteed to residences")

    zone, how = resolve_tz(s.dest_tz, s.dest_state, s.dest_postal_code)
    if zone is None:
        res.status = Status.REVIEW
        res.reason = how
        return res
    res.zone = zone

    promised = parse_moment(s.promised)
    if promised is None:
        res.status = Status.REVIEW
        res.reason = "no promised/committed delivery date"
        return res

    default_t = parse_time_of_day(rule.date_only_commitment_time)
    commit = to_aware(promised, zone, default_t).astimezone(zone)
    res.promised_local = commit
    if promised.date_only:
        res.notes.append(f"date-only commitment read as {rule.date_only_commitment_time} local (rule assumption)")

    # Holiday shift: commitment moves past carrier holidays (length of the holiday).
    if carrier.holiday_handling == "extend":
        shifted = commit
        while shifted.date() in carrier.holidays:
            shifted = _same_wall_time(shifted + timedelta(days=1), zone)
        if shifted != commit:
            res.notes.append(
                f"commitment fell on a carrier holiday; moved to {shifted.date()}"
                + ("" if carrier.holidays_verified else " (holiday list unverified)")
            )
            commit = shifted

    commit = _apply_rule_adjustments(rule, svc, residential, commit, zone, res)

    excluded_by_calendar: str | None = None
    for adj in carrier.calendar_adjustments:
        if rule.id not in adj.get("applies_to_rules", [rule.id]):
            continue
        only_svc = adj.get("only_if_service_contains")
        if only_svc and only_svc not in svc:
            continue
        kind = adj.get("kind")
        when = adj.get("when", {})
        if kind == "ship_window_business_days":
            continue  # evaluated after delivery is known
        if not _adjustment_matches_day(when, commit.date()):
            continue
        if kind == "end_of_day":
            commit = datetime.combine(commit.date(), time(23, 59, 59), tzinfo=zone)
            res.notes.append(f"{adj['id']}: commitment extended to end of day")
        elif kind == "extend_minutes":
            limit = adj.get("only_if_commitment_at_or_before")
            if limit and commit.timetz().replace(tzinfo=None) > parse_time_of_day(limit):
                continue
            commit = commit + timedelta(minutes=int(adj["minutes"]))
            res.notes.append(f"{adj['id']}: commitment extended {adj['minutes']} min")
        elif kind == "no_guarantee":
            excluded_by_calendar = f"{adj['id']}: no guarantee on {commit.date()}"
    res.commitment = commit

    # Delivered (or first attempt) timestamp.
    delivered = parse_moment(s.delivered)
    attempt = parse_moment(s.first_attempt)
    if delivered is None and attempt is None:
        res.status = Status.PENDING
        res.reason = "not delivered yet"
        if cfg.as_of > commit.date():
            res.notes.append("already past the commitment date; re-run after delivery")
        return res
    naive_zone = _naive_zone(s, cfg, zone)
    times = []
    for label, m in (("delivered", delivered), ("first attempt", attempt)):
        if m is None:
            continue
        if m.date_only:
            res.status = Status.REVIEW
            res.reason = f"{label} has no time of day; cannot compare to a time commitment"
            return res
        times.append((to_aware(m, zone, time(0), naive_zone).astimezone(zone), label))
    event, event_label = min(times)
    res.delivered_local = event
    if event_label == "first attempt":
        res.notes.append("first delivery attempt used (counts as on-schedule if timely)")

    delta = event - commit
    if delta.total_seconds() <= rule.grace_seconds:
        res.status = Status.ON_TIME
        res.reason = f"delivered {fmt_duration(-delta)} before commitment" if delta.total_seconds() < 0 else "delivered within the grace period"
        return res
    res.late_by = delta

    # From here on the package was late.
    if excluded_by_calendar:
        res.status = Status.EXCLUDED
        res.reason = excluded_by_calendar
        return res
    unknown: list[str] = []
    for c in _codes(s.exception_codes):
        if c in carrier.exclusion_codes:
            res.status = Status.EXCLUDED
            res.reason = f"exception {c}: {carrier.exclusion_codes[c]}"
            return res
        unknown.append(c)
    for adj in carrier.calendar_adjustments:
        if adj.get("kind") != "ship_window_business_days" or rule.id not in adj.get("applies_to_rules", [rule.id]):
            continue
        only_svc = adj.get("only_if_service_contains")
        if only_svc and only_svc not in svc:
            continue
        if ship_day and _adjustment_matches_day(adj.get("when", {}) | _ship_range(adj), ship_day):
            limit = add_business_days(ship_day, int(adj["business_days"]), carrier.holidays)
            if event.date() <= limit:
                res.status = Status.EXCLUDED
                res.reason = f"{adj['id']}: mailed {ship_day}, delivered within {adj['business_days']} business days"
                return res

    # Claim window.
    window = rule.claim_window
    anchor_name = window.get("anchor", "ship_date")
    anchor: date | None = None
    if anchor_name == "scheduled_delivery_date":
        anchor = commit.date()
    elif anchor_name == "invoice_date":
        inv = parse_moment(s.invoice_date)
        if inv:
            anchor = inv.day
        elif window.get("fallback_anchor") == "ship_date" and ship_day:
            anchor = ship_day
            res.notes.append("no invoice_date: deadline counted from ship date (earliest possible)")
    elif anchor_name == "ship_date":
        anchor = ship_day
    if anchor is None:
        res.status = Status.REVIEW
        res.reason = f"late by {fmt_duration(delta)} but claim window anchor ({anchor_name}) is missing"
        return res
    res.claim_deadline = anchor + timedelta(days=int(window["days"]))
    if window.get("earliest_days") and ship_day:
        res.file_from = ship_day + timedelta(days=int(window["earliest_days"]))
    if window.get("verified") is False:
        res.notes.append("claim window for this rule is UNVERIFIED")

    if unknown:
        res.status = Status.REVIEW
        res.reason = f"late by {fmt_duration(delta)} but has unrecognised exception code(s) {', '.join(unknown)}"
        return res
    if cfg.as_of > res.claim_deadline:
        res.status = Status.WINDOW_EXPIRED
        res.reason = f"late by {fmt_duration(delta)}; claim window closed {res.claim_deadline} (as of {cfg.as_of})"
        return res
    res.status = Status.CLAIM
    res.reason = f"delivered {fmt_duration(delta)} after the guaranteed commitment"
    if res.file_from and cfg.as_of < res.file_from:
        res.notes.append(f"carrier accepts the request from {res.file_from}")
    return res


def _ship_range(adj: dict[str, Any]) -> dict[str, Any]:
    when = adj.get("when", {})
    if "ship_month_day_range" in when:
        return {"month_day_range": when["ship_month_day_range"]}
    return {}


def _same_wall_time(dt: datetime, zone: tzinfo) -> datetime:
    return datetime.combine(dt.date(), dt.timetz().replace(tzinfo=None), tzinfo=zone)


def _naive_zone(s: Shipment, cfg: AuditConfig, dest: tzinfo) -> tzinfo:
    if s.delivered_tz.strip():
        z = zone_from_name(s.delivered_tz)
        if z is not None:
            return z
    return timezone.utc if cfg.naive_delivered == "utc" else dest


def _apply_rule_adjustments(rule: Rule, svc: str, residential: bool | None, commit: datetime, zone: tzinfo, res: Result) -> datetime:
    for adj in rule.commitment_adjustments:
        services = adj.get("services")
        if services and svc not in [normalise_service(x) for x in services]:
            continue
        kind = adj.get("kind")
        if kind == "set_time":
            t = parse_time_of_day(adj["to_time"])
            new = datetime.combine(commit.date(), t, tzinfo=zone)
            if new > commit:
                res.notes.append(f"commitment moved to {t.strftime('%H:%M')} per carrier notice")
                commit = new
        elif kind == "move_time":
            if residential not in adj.get("when_residential", [True, False, None]):
                continue
            if commit.timetz().replace(tzinfo=None) == parse_time_of_day(adj["from_time"]):
                t = parse_time_of_day(adj["to_time"])
                commit = datetime.combine(commit.date(), t, tzinfo=zone)
                extra = " (residential flag unknown; assumed residential)" if residential is None else ""
                res.notes.append(f"commitment moved to {t.strftime('%H:%M')} per carrier notice{extra}")
    return commit


def audit_all(shipments: Iterable[Shipment], rules: RuleSet, cfg: AuditConfig) -> list[Result]:
    return [audit_shipment(s, rules, cfg) for s in shipments]


def summarise(results: list[Result]) -> dict[str, Any]:
    by_status: dict[str, int] = {st.value: 0 for st in Status}
    by_carrier: dict[str, dict[str, int]] = {}
    for r in results:
        by_status[r.status.value] += 1
        c = r.carrier_code or (r.shipment.carrier or "?")
        by_carrier.setdefault(c, {}).setdefault(r.status.value, 0)
        by_carrier[c][r.status.value] += 1
    claims = [r for r in results if r.status is Status.CLAIM]
    next_deadline = min((r.claim_deadline for r in claims if r.claim_deadline), default=None)
    return {
        "rows": len(results),
        "by_status": {k: v for k, v in by_status.items() if v},
        "by_carrier": by_carrier,
        "claims": len(claims),
        "next_claim_deadline": next_deadline.isoformat() if next_deadline else None,
        "review": [r.shipment.tracking for r in results if r.status in (Status.REVIEW, Status.ERROR)],
    }
