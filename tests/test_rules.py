"""One or more tests per rule / exclusion. Expected outcomes are taken from the carrier terms quoted in rules.json."""

import json
from datetime import date, timedelta

import pytest

from late_delivery_auditor.audit import Status, add_business_days, wednesday_before_thanksgiving
from late_delivery_auditor.rules import RulesError, load_rules, normalise_service, raw_rules_json


def test_every_rule_has_source_and_verified_date(rules):
    for r in rules.rules:
        assert r.source_url.startswith("https://"), r.id
        assert date.fromisoformat(r.verified_on), r.id
        assert r.citation, r.id


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("FedEx 2Day® A.M.", "2day am"),
        ("FEDEX_2_DAY_AM", "2 day am"),
        ("UPS Next Day Air Saver®", "next day air saver"),
        ("UPS 2nd Day Air A.M.", "2nd day air am"),
    ],
)
def test_service_normalisation(raw, expected):
    assert normalise_service(raw, ["fedex", "ups"]) == expected


# ---- guaranteed / not guaranteed -------------------------------------------------------

LATE = dict(ship_date="2026-09-21", promised="2026-09-22 10:30", delivered="2026-09-22 12:00", dest_state="NY")


@pytest.mark.parametrize(
    "carrier,service,expected_rule",
    [
        ("FedEx", "FedEx First Overnight", "fedex-us-time-definite"),
        ("FedEx", "PRIORITY_OVERNIGHT", "fedex-us-time-definite"),
        ("FedEx", "FedEx Standard Overnight", "fedex-us-time-definite"),
        ("FedEx", "FEDEX_2_DAY_AM", "fedex-us-time-definite"),
        ("UPS", "UPS Next Day Air Early", "ups-us-air-guaranteed"),
        ("UPS", "UPS Next Day Air", "ups-us-air-guaranteed"),
        ("UPS", "UPS 2nd Day Air A.M.", "ups-us-air-guaranteed"),
    ],
)
def test_guaranteed_services_flag_late(run, carrier, service, expected_rule):
    r = run(carrier=carrier, service=service, **LATE)
    assert r.status is Status.CLAIM
    assert r.rule.id == expected_rule


@pytest.mark.parametrize(
    "carrier,service",
    [
        ("FedEx", "FedEx 2Day"),
        ("FedEx", "FedEx Express Saver"),
        ("FedEx", "FedEx Ground"),
        ("FedEx", "FedEx Home Delivery"),
        ("UPS", "UPS Ground"),
        ("UPS", "UPS 2nd Day Air"),
        ("UPS", "UPS 3 Day Select"),
        ("USPS", "Priority Mail"),
        ("USPS", "USPS Ground Advantage"),
    ],
)
def test_suspended_or_unguaranteed_services(run, carrier, service):
    r = run(carrier=carrier, service=service, **LATE)
    assert r.status is Status.NOT_GUARANTEED
    assert r.rule is not None and not r.rule.guaranteed


def test_unknown_service_defaults_to_not_guaranteed(run):
    r = run(carrier="UPS", service="UPS Hyperloop Same Hour", **LATE)
    assert r.status is Status.NOT_GUARANTEED and r.rule is None
    assert "not in rule table" in r.reason


def test_unknown_carrier_defaults_to_not_guaranteed(run):
    r = run(carrier="DHL", service="DHL Express Worldwide", **LATE)
    assert r.status is Status.NOT_GUARANTEED and "unknown carrier" in r.reason


def test_fedex_ship_date_windows(run):
    base = dict(carrier="FedEx", service="FedEx Priority Overnight", dest_state="CA")
    # Shipped during the Dec 1 2025 - Jan 12 2026 suspension.
    r = run(ship_date="2025-12-10", promised="2025-12-11 10:30", delivered="2025-12-11 14:00", **base)
    assert r.status is Status.NOT_GUARANTEED and r.rule.id == "fedex-us-suspended-dec2025"
    # First day of reinstatement.
    r = run(ship_date="2026-01-13", promised="2026-01-14 10:30", delivered="2026-01-14 14:00", as_of="2026-01-20", **base)
    assert r.status is Status.CLAIM
    # Before any rule's window.
    r = run(ship_date="2025-06-01", promised="2025-06-02 10:30", delivered="2025-06-02 14:00", **base)
    assert r.status is Status.NOT_GUARANTEED and r.rule is None and "outside every rule" in r.reason


# ---- grace periods -----------------------------------------------------------------------

def test_fedex_60_seconds_or_more_is_late(run):
    base = dict(carrier="FedEx", service="FedEx Priority Overnight", ship_date="2026-09-21",
                promised="2026-09-22 10:30", dest_state="NY")
    assert run(delivered="2026-09-22 10:30:59", **base).status is Status.ON_TIME
    assert run(delivered="2026-09-22 10:31:00", **base).status is Status.CLAIM


def test_ups_within_60_seconds_is_on_time(run):
    base = dict(ship_date="2026-09-21", promised="2026-09-22 10:30", dest_state="NY")
    assert run(delivered="2026-09-22 10:31:00", **base).status is Status.ON_TIME
    assert run(delivered="2026-09-22 10:31:01", **base).status is Status.CLAIM


# ---- commitment adjustments --------------------------------------------------------------

def test_ups_nda_saver_extends_to_end_of_day(run):
    base = dict(service="UPS Next Day Air Saver", ship_date="2026-09-21", promised="2026-09-22 15:00", dest_state="CO")
    assert run(delivered="2026-09-22 23:58", **base).status is Status.ON_TIME
    assert run(delivered="2026-09-23 08:00", **base).status is Status.CLAIM


@pytest.mark.parametrize("residential,expected", [("yes", Status.ON_TIME), ("", Status.ON_TIME), ("no", Status.CLAIM)])
def test_ups_nda_residential_1030_moves_to_noon(run, residential, expected):
    r = run(residential=residential, ship_date="2026-09-21", promised="2026-09-22 10:30",
            delivered="2026-09-22 11:45", dest_state="NY")
    assert r.status is expected


def test_ups_2nd_day_air_am_residential_excluded(run):
    r = run(service="UPS 2nd Day Air A.M.", residential="yes", **LATE)
    assert r.status is Status.EXCLUDED


def test_fedex_labor_day_2026_extension(run):
    base = dict(carrier="FedEx", service="FedEx Priority Overnight", ship_date="2026-09-08", dest_state="DC", as_of="2026-09-15")
    assert run(promised="2026-09-09 10:30", delivered="2026-09-09 11:59", **base).status is Status.ON_TIME
    assert run(promised="2026-09-09 10:30", delivered="2026-09-09 12:01", **base).status is Status.CLAIM
    # 4:30 pm commitment is after the 1:30 pm cut-off: no extension.
    assert run(promised="2026-09-09 16:30", delivered="2026-09-09 17:00", **base).status is Status.CLAIM


def test_fedex_thanksgiving_eve_90_minutes(run):
    wed = wednesday_before_thanksgiving(2026)
    assert wed == date(2026, 11, 25)
    base = dict(carrier="FedEx", service="FedEx Priority Overnight", ship_date="2026-11-24", dest_state="NY", as_of="2026-11-30")
    assert run(promised="2026-11-25 10:30", delivered="2026-11-25 11:55", **base).status is Status.ON_TIME
    assert run(promised="2026-11-25 10:30", delivered="2026-11-25 12:05", **base).status is Status.CLAIM


def test_fedex_week_before_christmas_end_of_day(run):
    base = dict(carrier="FedEx", service="FedEx Standard Overnight", dest_state="NY", as_of="2026-12-28")
    # Dec 18 is 7 calendar days before Christmas: delivery by end of day counts.
    assert run(ship_date="2026-12-17", promised="2026-12-18 16:30", delivered="2026-12-18 20:00", **base).status is Status.ON_TIME
    # Dec 17 is outside the window.
    assert run(ship_date="2026-12-16", promised="2026-12-17 16:30", delivered="2026-12-17 20:00", **base).status is Status.CLAIM


def test_holiday_shift(run):
    # A commitment dated on a listed holiday moves to the next day.
    r = run(ship_date="2026-11-25", promised="2026-11-26 10:30", delivered="2026-11-27 10:00", dest_state="NY", as_of="2026-11-30")
    assert r.status is Status.ON_TIME
    assert r.commitment.date() == date(2026, 11, 27)


# ---- exceptions ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", ["WEATHER", "weather", "Address Correction", "RECIPIENT_UNAVAILABLE", "CUSTOMER_REQUEST"])
def test_exception_codes_exclude(run, code):
    r = run(exception_codes=code, **LATE)
    assert r.status is Status.EXCLUDED


def test_multiple_codes_one_known(run):
    r = run(exception_codes="MISSORT;WEATHER", **LATE)
    assert r.status is Status.EXCLUDED


def test_unknown_code_goes_to_review(run):
    r = run(exception_codes="MISSORT", **LATE)
    assert r.status is Status.REVIEW and "MISSORT" in r.reason


def test_exception_on_on_time_package_is_still_on_time(run):
    r = run(exception_codes="WEATHER", ship_date="2026-09-21", promised="2026-09-22 10:30",
            delivered="2026-09-22 10:00", dest_state="NY")
    assert r.status is Status.ON_TIME


def test_first_attempt_counts(run):
    r = run(ship_date="2026-09-21", promised="2026-09-22 10:30", first_attempt="2026-09-22 10:15",
            delivered="2026-09-23 09:00", dest_state="NY")
    assert r.status is Status.ON_TIME


# ---- USPS Priority Mail Express ---------------------------------------------------------

PME = dict(carrier="USPS", service="Priority Mail Express", ship_date="2026-09-19",
           promised="2026-09-21 18:00", delivered="2026-09-22 12:10", dest_state="MO")


def test_pme_late_claim_window_2_to_30_days(run):
    r = run(**PME)
    assert r.status is Status.CLAIM
    assert r.file_from == date(2026, 9, 21)  # 2 days after mailing
    assert r.claim_deadline == date(2026, 10, 19)  # 30 days after mailing


@pytest.mark.parametrize("state", ["AK", "HI", "GU"])
def test_pme_non_contiguous_destinations_excluded(run, state):
    r = run(**{**PME, "dest_state": state})
    assert r.status is Status.EXCLUDED


def test_pme_date_only_assumes_6pm(run):
    r = run(**{**PME, "promised": "2026-09-21", "delivered": "2026-09-21 17:30"})
    assert r.status is Status.ON_TIME
    r = run(**{**PME, "promised": "2026-09-21", "delivered": "2026-09-21 18:30"})
    assert r.status is Status.CLAIM


def test_pme_1day_dec22_25_exception(run):
    base = dict(carrier="USPS", service="Priority Mail Express 1-Day", dest_state="NY", as_of="2026-12-30")
    # Mailed Tue Dec 22, due Dec 23; delivered Thu Dec 24 = within 2 business days -> no refund.
    r = run(ship_date="2026-12-22", promised="2026-12-23 18:00", delivered="2026-12-24 12:00", **base)
    assert r.status is Status.EXCLUDED
    # Delivered Mon Dec 28: Dec 25 is not a USPS holiday in the table, Sun Dec 27 is skipped -> beyond 2 business days.
    r = run(ship_date="2026-12-22", promised="2026-12-23 18:00", delivered="2026-12-28 12:00", **base)
    assert r.status is Status.CLAIM
    # 2-Day service is not covered by the exception.
    r = run(ship_date="2026-12-22", promised="2026-12-23 18:00", delivered="2026-12-24 12:00",
            **{**base, "service": "Priority Mail Express 2-Day"})
    assert r.status is Status.CLAIM


def test_add_business_days_skips_sundays_and_holidays():
    assert add_business_days(date(2026, 12, 26), 1, set()) == date(2026, 12, 28)  # Sat -> Mon
    assert add_business_days(date(2026, 12, 24), 1, {date(2026, 12, 25)}) == date(2026, 12, 26)


# ---- claim windows ----------------------------------------------------------------------

def test_ups_window_15_days_from_scheduled_delivery(run):
    kw = dict(ship_date="2026-09-01", promised="2026-09-02 10:30", delivered="2026-09-02 12:00", dest_state="NY")
    assert run(as_of="2026-09-17", **kw).status is Status.CLAIM  # deadline day itself
    r = run(as_of="2026-09-18", **kw)
    assert r.status is Status.WINDOW_EXPIRED and r.claim_deadline == date(2026, 9, 17)


def test_fedex_window_uses_invoice_date_else_ship_date(run):
    kw = dict(carrier="FedEx", service="FedEx Priority Overnight", ship_date="2026-09-01",
              promised="2026-09-02 10:30", delivered="2026-09-02 12:00", dest_state="NY")
    r = run(as_of="2026-09-20", invoice_date="2026-09-08", **kw)
    assert r.status is Status.CLAIM and r.claim_deadline == date(2026, 9, 23)
    r = run(as_of="2026-09-20", **kw)
    assert r.status is Status.WINDOW_EXPIRED and r.claim_deadline == date(2026, 9, 16)


def test_pme_window_expired(run):
    r = run(as_of="2026-10-20", **PME)
    assert r.status is Status.WINDOW_EXPIRED


def test_pending_and_date_only_delivery(run):
    assert run(ship_date="2026-09-21", promised="2026-09-22 10:30", dest_state="NY").status is Status.PENDING
    r = run(ship_date="2026-09-21", promised="2026-09-22 10:30", delivered="2026-09-23", dest_state="NY")
    assert r.status is Status.REVIEW


# ---- custom rule files --------------------------------------------------------------------

def test_custom_rules_file_and_no_guarantee_day(tmp_path, run):
    base = json.loads(raw_rules_json())
    base["carriers"]["UPS"]["calendar_adjustments"].append(
        {"id": "my-blackout", "kind": "no_guarantee", "when": {"dates": ["2026-09-22"]}}
    )
    p = tmp_path / "rules.json"
    p.write_text(json.dumps(base))
    from late_delivery_auditor import AuditConfig, Shipment, audit_shipment

    rs = load_rules(p)
    r = audit_shipment(Shipment(tracking="T", carrier="UPS", service="UPS Next Day Air", residential="no", **LATE),
                       rs, AuditConfig(as_of=date(2026, 9, 27)))
    assert r.status is Status.EXCLUDED and "my-blackout" in r.reason


def test_yaml_rules(tmp_path):
    yaml = pytest.importorskip("yaml")
    data = {
        "carriers": {"ACME": {"aliases": ["acme"]}},
        "rules": [{"id": "acme-fast", "carrier": "ACME", "services": ["fast"], "guaranteed": True,
                   "verification": "unverified", "verified_on": "2026-01-01", "source_url": "https://example.com/terms",
                   "citation": "example", "claim_window": {"anchor": "ship_date", "days": 10}}],
    }
    p = tmp_path / "r.yaml"
    p.write_text(yaml.safe_dump(data))
    rs = load_rules(p)
    rule, _ = rs.match("ACME", "ACME Fast", date(2026, 1, 1))
    assert rule.id == "acme-fast"


@pytest.mark.parametrize(
    "bad",
    [
        {"carriers": {}, "rules": [{"id": "x"}]},
        {"carriers": {"A": {}}, "rules": [{"id": "x", "carrier": "B", "services": [], "guaranteed": False,
                                            "verification": "verified", "verified_on": "2026-01-01",
                                            "source_url": "https://example.com", "citation": "c"}]},
        {"carriers": {"A": {}}, "rules": [{"id": "x", "carrier": "A", "services": [], "guaranteed": True,
                                            "verification": "verified", "verified_on": "2026-01-01",
                                            "source_url": "https://example.com", "citation": "c"}]},
    ],
)
def test_invalid_rules_rejected(tmp_path, bad):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps(bad))
    with pytest.raises(RulesError):
        load_rules(p)


def test_business_day_helper_matches_calendar():
    # Independent check with a plain loop over the calendar.
    start, n = date(2026, 12, 22), 2
    d, count = start, 0
    while count < n:
        d += timedelta(days=1)
        if d.weekday() != 6:
            count += 1
    assert add_business_days(start, n, set()) == d
