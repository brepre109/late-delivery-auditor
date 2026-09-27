"""Time-zone handling. Expected values come from tzdata / UTC arithmetic, not from the code under test."""

from datetime import datetime, timedelta, timezone

import pytest

from late_delivery_auditor.audit import Status
from late_delivery_auditor.timezones import resolve_tz, zip_to_state


@pytest.mark.parametrize(
    "state,zip_code,expected",
    [
        ("TX", "79901", "America/Denver"),  # El Paso: Mountain although Texas default is Central
        ("TX", "75201", "America/Chicago"),  # Dallas
        ("FL", "32501", "America/Chicago"),  # Pensacola
        ("FL", "33101", "America/New_York"),  # Miami
        ("TN", "37902", "America/New_York"),  # Knoxville
        ("TN", "37201", "America/Chicago"),  # Nashville
        ("AZ", "85004", "America/Phoenix"),
        ("", "02108", "America/New_York"),  # state derived from ZIP
        ("", "2108", "America/New_York"),  # leading zero lost in a spreadsheet
        ("ID", "83814", "America/Los_Angeles"),  # Coeur d'Alene
        ("", "96813", "Pacific/Honolulu"),
    ],
)
def test_resolve_tz(state, zip_code, expected):
    zone, _ = resolve_tz(None, state, zip_code)
    assert zone is not None and zone.key == expected


def test_explicit_tz_wins_and_unknown_is_reported():
    zone, how = resolve_tz("America/Detroit", "CA", "90012")
    assert zone.key == "America/Detroit" and how == "explicit"
    zone, how = resolve_tz("Mars/Olympus", None, None)
    assert zone is None and "unknown time zone" in how


def test_military_zip_is_unresolved():
    assert zip_to_state("09001") is None
    zone, _ = resolve_tz(None, None, "09001")
    assert zone is None


def test_utc_delivery_after_midnight_is_on_time_locally(run):
    # Date-only commitment 2026-09-24 in California; delivered 03:30Z on the 25th = 20:30 PDT on the 24th.
    r = run(carrier="FedEx", service="FedEx Standard Overnight", ship_date="2026-09-23",
            promised="2026-09-24", delivered="2026-09-25T03:30:00Z", dest_state="CA", dest_postal_code="94103")
    assert r.status is Status.ON_TIME
    assert r.delivered_local.isoformat() == "2026-09-24T20:30:00-07:00"


def test_naive_utc_mode_changes_the_answer(run):
    # Same wall-clock string, read as UTC: 2026-09-16 16:00Z = 11:00 CDT, 30 min late for a 10:30 commitment.
    kw = dict(ship_date="2026-09-15", promised="2026-09-16 10:30", delivered="2026-09-16 16:00", dest_state="IL")
    assert run(naive_delivered="utc", **kw).status is Status.CLAIM
    # Read as local: 16:00 CDT, also late but by 5h30m.
    local = run(**kw)
    assert local.late_by == timedelta(hours=5, minutes=30)


def test_delivered_tz_column_overrides_mode(run):
    r = run(ship_date="2026-09-15", promised="2026-09-16 10:30", delivered="2026-09-16 15:20",
            delivered_tz="UTC", dest_state="IL")
    assert r.status is Status.ON_TIME  # 15:20Z = 10:20 CDT


def test_spring_forward_dst(run):
    # DST began 2026-03-08 in the US. On 2026-03-09 Eastern is UTC-4, so 15:00Z = 11:00 EDT:
    # 30 minutes late for 10:30. A fixed UTC-5 assumption would wrongly say 10:00 (on time).
    expected_local = datetime(2026, 3, 9, 15, 0, tzinfo=timezone.utc) - timedelta(hours=4)
    r = run(ship_date="2026-03-08", promised="2026-03-09 10:30", delivered="2026-03-09T15:00:00Z",
            dest_state="NY", as_of="2026-03-10")
    assert r.status is Status.CLAIM
    assert r.delivered_local.replace(tzinfo=None) == expected_local.replace(tzinfo=None)
    assert r.late_by == timedelta(minutes=30)


def test_fall_back_dst(run):
    # DST ended 2026-11-01. On 2026-11-02 Central is UTC-6: 16:20Z = 10:20 CST -> on time for 10:30.
    r = run(ship_date="2026-11-01", promised="2026-11-02 10:30", delivered="2026-11-02T16:20:00Z",
            dest_state="MO", as_of="2026-11-03")
    assert r.status is Status.ON_TIME


def test_arizona_has_no_dst(run):
    # July: Phoenix is UTC-7 (no DST). 17:45Z = 10:45 MST -> 15 min late.
    r = run(ship_date="2026-07-13", promised="2026-07-14 10:30", delivered="2026-07-14T17:45:00Z",
            dest_state="AZ", as_of="2026-07-20")
    assert r.status is Status.CLAIM and r.late_by == timedelta(minutes=15)


def test_zip_override_decides_el_paso(run):
    # 22:15Z = 16:15 MDT (on time for 16:30); Central time would make it 17:15 (late).
    r = run(carrier="FedEx", service="FedEx Standard Overnight", ship_date="2026-09-21",
            promised="2026-09-22 16:30", delivered="2026-09-22T22:15:00Z", dest_state="TX", dest_postal_code="79901")
    assert r.status is Status.ON_TIME
    r2 = run(carrier="FedEx", service="FedEx Standard Overnight", ship_date="2026-09-21",
             promised="2026-09-22 16:30", delivered="2026-09-22T22:15:00Z", dest_state="TX", dest_postal_code="75201")
    assert r2.status is Status.CLAIM


def test_promised_with_explicit_offset_is_respected(run):
    # Promise given in UTC (14:30Z = 10:30 EDT); delivered 10:45 local.
    r = run(ship_date="2026-09-15", promised="2026-09-16T14:30:00Z", delivered="2026-09-16 10:45", dest_state="NY")
    assert r.status is Status.CLAIM and r.late_by == timedelta(minutes=15)


def test_missing_destination_goes_to_review(run):
    r = run(ship_date="2026-09-15", promised="2026-09-16 10:30", delivered="2026-09-16 11:45")
    assert r.status is Status.REVIEW
