"""``late-audit`` command-line interface."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import __version__
from .audit import AuditConfig, Status, audit_all, summarise
from .csvio import CsvFormatError, read_shipments, write_results
from .rules import RulesError, load_rules, raw_rules_json
from .timeparse import parse_date

DISCLAIMER = "Candidates only: the carrier decides eligibility. Check the cited terms before filing."


def _cmd_audit(a: argparse.Namespace) -> int:
    rules = load_rules(a.rules)
    as_of = parse_date(a.as_of) if a.as_of else date.today()
    shipments = read_shipments(a.shipments)
    results = audit_all(shipments, rules, AuditConfig(as_of=as_of, naive_delivered=a.naive_delivered))
    keep = results if a.all else [r for r in results if r.status is Status.CLAIM]
    n = write_results(keep, sys.stdout if a.output == "-" else a.output)
    summary = summarise(results)
    summary.update({"as_of": as_of.isoformat(), "rules": rules.source, "rules_checked_on": rules.checked_on,
                    "output": str(a.output), "rows_written": n})
    if a.summary_json:
        Path(a.summary_json).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    out = sys.stderr if a.output == "-" else sys.stdout
    print(f"Audited {summary['rows']} shipments as of {as_of} using {rules.source} (checked {rules.checked_on})", file=out)
    for k, v in summary["by_status"].items():
        print(f"  {k:<15} {v}", file=out)
    if summary["claims"]:
        print(f"{summary['claims']} claim candidate(s); earliest deadline {summary['next_claim_deadline']}", file=out)
    if summary["review"]:
        print(f"Needs review: {', '.join(summary['review'])}", file=out)
    print(f"Wrote {n} row(s) to {a.output}. {DISCLAIMER}", file=out)
    return 0


def _cmd_rules(a: argparse.Namespace) -> int:
    if a.json:
        print(raw_rules_json(a.rules))
        return 0
    rules = load_rules(a.rules)
    print(f"Rules: {rules.source} (checked {rules.checked_on})")
    print(f"Unknown carrier/service: {rules.default_reason}\n")
    hdr = f"{'rule':<28} {'carrier':<6} {'guar.':<5} {'ship dates':<23} {'window':<34} {'verified'}"
    print(hdr)
    print("-" * len(hdr))
    for r in rules.rules:
        span = f"{r.effective_ship_from or '...'} - {r.effective_ship_to or '...'}"
        w = r.claim_window
        window = f"{w['days']}d from {w.get('anchor')}" if w.get("days") else "-"
        if w.get("verified") is False:
            window += " (unverified)"
        print(f"{r.id:<28} {r.carrier:<6} {'yes' if r.guaranteed else 'no':<5} {span:<23} {window:<34} {r.verification} {r.verified_on}")
        if a.verbose:
            print(f"    services: {', '.join(r.services)}")
            print(f"    source:   {r.source_url}")
            print(f"    citation: {r.citation}")
            if r.notes:
                print(f"    notes:    {r.notes}")
    for c in rules.carriers.values():
        hol = "verified" if c.holidays_verified else "UNVERIFIED placeholder"
        print(f"\n{c.code}: holiday handling={c.holiday_handling}, {len(c.holidays)} holidays ({hol}), "
              f"{len(c.exclusion_codes)} exclusion codes: {', '.join(sorted(c.exclusion_codes))}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="late-audit", description="Flag packages eligible for carrier guaranteed-service refund claims.")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    au = sub.add_parser("audit", help="audit a shipments CSV and write claim candidates")
    au.add_argument("shipments", help="input shipments CSV")
    au.add_argument("-o", "--output", default="claims.csv", help="output CSV ('-' for stdout; default claims.csv)")
    au.add_argument("--as-of", help="evaluate claim windows as of this date (default: today)")
    au.add_argument("--rules", help="rules file (.json, or .yaml with PyYAML) instead of the built-in table")
    au.add_argument("--all", action="store_true", help="write every row with its status, not only CLAIM rows")
    au.add_argument("--naive-delivered", choices=("local", "utc"), default="local",
                    help="how to read delivered timestamps without an offset (default: destination local time)")
    au.add_argument("--summary-json", help="also write the summary as JSON to this path")
    au.set_defaults(func=_cmd_audit)

    ru = sub.add_parser("rules", help="print the rule table")
    ru.add_argument("--rules", help="rules file instead of the built-in table")
    ru.add_argument("-v", "--verbose", action="store_true", help="show services, sources and citations")
    ru.add_argument("--json", action="store_true", help="dump the raw rule file (copy it to customise)")
    ru.set_defaults(func=_cmd_rules)
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    try:
        return a.func(a)
    except (RulesError, CsvFormatError, FileNotFoundError, ValueError) as e:
        print(f"late-audit: error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
