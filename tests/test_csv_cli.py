"""CSV parsing variants and the command-line interface."""

import csv
import io
import json
from pathlib import Path

import pytest

from late_delivery_auditor.cli import main
from late_delivery_auditor.csvio import CsvFormatError, read_shipments
from late_delivery_auditor.timeparse import TimeParseError, parse_moment

SAMPLE = Path(__file__).resolve().parent.parent / "samples" / "shipments.csv"


@pytest.mark.parametrize(
    "text,day,hhmm,has_tz",
    [
        ("2026-09-15", "2026-09-15", None, False),
        ("09/15/2026", "2026-09-15", None, False),
        ("9/15/26", "2026-09-15", None, False),
        ("2026-09-15T10:30", "2026-09-15", "10:30", False),
        ("2026-09-15 10:30:00-04:00", "2026-09-15", "10:30", True),
        ("2026-09-15T15:02:11.123Z", "2026-09-15", "15:02", True),
        ("2026-09-15 10:30 -0400", "2026-09-15", "10:30", True),
        ("9/15/2026 4:30 p.m.", "2026-09-15", "16:30", False),
        ("09/15/2026 10:30 AM", "2026-09-15", "10:30", False),
        ("2026-09-15 10:30 UTC", "2026-09-15", "10:30", True),
    ],
)
def test_parse_moment_variants(text, day, hhmm, has_tz):
    m = parse_moment(text)
    assert m.day.isoformat() == day
    assert (m.at.strftime("%H:%M") if m.at else None) == hhmm
    assert (m.tz is not None) == has_tz


def test_parse_moment_rejects_garbage():
    assert parse_moment("  ") is None
    with pytest.raises(TimeParseError):
        parse_moment("next tuesday")


def test_header_aliases_semicolon_and_bom():
    text = "﻿Tracking #;Carrier;Service Level;Ship Date;Guaranteed By;Delivered At;ZIP;Delay Reason\n" \
           "T1;ups;UPS Next Day Air;2026-09-21;2026-09-22 10:30;2026-09-22 11:00;10001;WEATHER,SECURITY_DELAY\n"
    rows = read_shipments(io.StringIO(text))
    assert len(rows) == 1
    s = rows[0]
    assert (s.tracking, s.carrier, s.promised, s.dest_postal_code) == ("T1", "ups", "2026-09-22 10:30", "10001")
    assert s.exception_codes == "WEATHER,SECURITY_DELAY"


def test_tab_delimited_and_blank_lines():
    text = "tracking\tcarrier\tservice\nA\tUPS\tUPS Ground\n\t\t\nB\tFedEx\tFedEx 2Day\n"
    assert [s.tracking for s in read_shipments(io.StringIO(text))] == ["A", "B"]


def test_missing_required_column():
    with pytest.raises(CsvFormatError):
        read_shipments(io.StringIO("tracking,service\nA,Ground\n"))


def test_bad_date_becomes_error_row(tmp_path, capsys):
    p = tmp_path / "s.csv"
    p.write_text("tracking,carrier,service,ship_date,promised,delivered,dest_state\n"
                 "A,UPS,UPS Next Day Air,2026-09-21,soon,2026-09-22 11:00,NY\n")
    out = tmp_path / "o.csv"
    assert main(["audit", str(p), "-o", str(out), "--all", "--as-of", "2026-09-27"]) == 0
    row = next(csv.DictReader(out.open()))
    assert row["status"] == "ERROR"


def test_cli_audit_sample(tmp_path, capsys):
    out = tmp_path / "claims.csv"
    summ = tmp_path / "summary.json"
    rc = main(["audit", str(SAMPLE), "-o", str(out), "--as-of", "2026-09-27", "--summary-json", str(summ)])
    assert rc == 0
    rows = list(csv.DictReader(out.open()))
    assert rows and all(r["status"] == "CLAIM" for r in rows)
    got = {r["tracking"] for r in rows}
    assert got == {"1Z999AA10000000002", "1Z999AA10000000005", "799999990002", "799999990009", "EF999999901US"}
    for r in rows:
        assert r["claim_deadline"] and r["source_url"].startswith("https://") and r["rule_applied"]
    summary = json.loads(summ.read_text())
    assert summary["rows"] == 25
    assert summary["by_status"]["CLAIM"] == 5
    assert "Candidates only" in capsys.readouterr().out


def test_cli_all_rows_match_sample_notes(tmp_path):
    """The sample's `note` column states the expected verdict in words; check the first word."""
    out = tmp_path / "all.csv"
    main(["audit", str(SAMPLE), "-o", str(out), "--all", "--as-of", "2026-09-27"])
    notes = {r["tracking_number"]: r["note"] for r in csv.DictReader(SAMPLE.open())}
    word_to_status = {"on": "ON_TIME", "late": "CLAIM", "excluded": "EXCLUDED", "no": "NOT_GUARANTEED",
                      "pending": "PENDING", "review": "REVIEW"}
    for r in csv.DictReader(out.open()):
        note = notes[r["tracking"]]
        expected = "WINDOW_EXPIRED" if "window closed" in note else word_to_status[note.split()[0].rstrip(":")]
        assert r["status"] == expected, (r["tracking"], note, r["reason"])


def test_cli_rules(capsys):
    assert main(["rules", "-v"]) == 0
    out = capsys.readouterr().out
    assert "fedex-us-time-definite" in out and "https://pe.usps.com/text/dmm300/604.htm" in out
    assert main(["rules", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["schema_version"] == 1


def test_cli_bad_rules_path(capsys):
    assert main(["rules", "--rules", "/nonexistent/rules.json"]) == 2
