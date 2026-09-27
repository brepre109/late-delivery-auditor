# late-delivery-auditor

Find the parcels in a shipments CSV that a carrier delivered late against a *guaranteed* commitment, and list them as money-back-guarantee refund candidates, with the rule applied, the claim deadline and the carrier terms each one relies on.

> **Disclaimer.** This tool finds **candidates**. It does not guarantee a refund. Carriers decide eligibility from their own scan data and terms, and they suspend or change guarantees without notice. Check the cited terms (and the `verified_on` date) before filing anything. This is not legal advice.

## Features

- **Data-driven rule table** (`rules.json`, or YAML with PyYAML). Each rule has a carrier, service aliases, `guaranteed`, the ship-date window it covers, a grace period, a claim window, `verification` + `verified_on`, a `source_url` and a quoted `citation`. Any carrier or service that no rule covers is **not guaranteed** by default.
- **Time-zone aware.** Carrier commitments ("by 10:30 a.m.") are in the destination's local time. The destination zone comes from an explicit `dest_tz` column, then a ZIP-prefix override for split states, then a state-to-IANA table. Delivered timestamps can carry a UTC offset (`Z`, `-04:00`, `-0400`) or be naive. Naive timestamps are read as destination local time by default; `--naive-delivered utc` or a per-row `delivered_tz` column changes that. All arithmetic uses `zoneinfo`, so DST is handled correctly.
- **Carrier details built in:** FedEx's "60 seconds or more" and UPS's "within 60 seconds" grace periods; UPS Next Day Air Saver's end-of-day commitment; UPS residential Next Day Air 10:30 → 12:00; UPS 2nd Day Air A.M. residential exclusion; FedEx Thanksgiving-eve (+90 min) and pre-Christmas (end of day) exceptions; FedEx's temporary Sept 9–10, 2026 extension; USPS Priority Mail Express exclusions for AK/HI/territories and for 1-Day items mailed Dec 22–25.
- **Exclusions:** exception/delay-reason codes (weather, address correction, recipient unavailable, customer-requested change, and more), mapped to the carrier clause that removes the guarantee. Carrier holidays, configurable `no_guarantee` days, and claim windows that have expired as of `--as-of` are also excluded.
- **Claim deadlines:** UPS counts 15 days from the scheduled delivery date. FedEx counts 15 days from the invoice date; if the CSV has no invoice date it falls back to the ship date, which gives the earliest possible deadline. USPS Priority Mail Express can be claimed from 2 to 30 days after mailing.
- **Anything unclear goes to review, not to a guess:** unknown exception codes, a missing destination, or a delivery date with no time of day.
- Zero runtime dependencies (Python ≥ 3.10 standard library).

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"          # add ,yaml for YAML rule files
late-audit audit samples/shipments.csv -o claims.csv --as-of 2026-09-27
late-audit rules                 # the rule table; -v for sources and citations
pytest -q
```

On the bundled sample (25 synthetic rows) the audit prints:

```
Audited 25 shipments as of 2026-09-27 using built-in rules.json (checked 2026-09-27)
  CLAIM           5
  ON_TIME         6
  NOT_GUARANTEED  6
  EXCLUDED        4
  WINDOW_EXPIRED  1
  PENDING         1
  REVIEW          2
5 claim candidate(s); earliest deadline 2026-10-01
Needs review: 1Z999AA10000000011, 1Z999AA10000000012
```

The five claim rows are in `samples/expected_claims.csv`. Add `--all` to write every row with its status and reason, and `--summary-json summary.json` to save the counts.

To use your own rules, run `late-audit rules --json > my_rules.json`, edit the file, then pass `--rules my_rules.json`.

## Input CSV

Headers are matched case-insensitively, and common spellings are accepted (`Tracking #`, `tracking_number`, `Service Level`, `Guaranteed By`, and so on). The delimiter is detected from the header line (`,`, `;`, tab or `|`). A UTF-8 BOM is fine. Extra columns are ignored.

| Field | Required | Notes |
|---|---|---|
| `tracking` | yes | |
| `carrier` | yes | `UPS`, `FedEx`/`FDX`, `USPS`, … (aliases in the rule file) |
| `service` | yes | Marketing names or API codes: `FedEx 2Day® A.M.`, `FEDEX_2_DAY_AM`, `UPS Next Day Air Saver` |
| `ship_date` | for windows | Mailing / pickup date |
| `promised` | yes to audit | Carrier commitment, as a date or a datetime. A naive datetime is destination-local. A date-only value uses the rule's `date_only_commitment_time` |
| `delivered` | | Datetime with or without an offset. Blank means `PENDING` |
| `first_attempt` | | Earliest of attempt/delivery is used (UPS and USPS count an attempt) |
| `dest_postal_code`, `dest_state`, `dest_tz` | one of them | `dest_tz` is an IANA name and wins over the others |
| `delivered_tz` | | Zone for a naive `delivered` value in this row (`UTC`, `America/Chicago`) |
| `exception_codes` | | `;`, `,` or `\|` separated, e.g. `WEATHER;ADDRESS_CORRECTION` |
| `residential` | | `yes/no/true/false`. Used by the UPS rules. When blank, the tool takes the more conservative reading |
| `invoice_date` | | FedEx claim-window anchor |

Accepted date forms include ISO 8601 (`2026-09-15T15:02:11Z`), `2026-09-15 10:30 -0400`, `09/15/2026 10:30 AM`, `9/15/26` and `4:30 p.m.`.

## Output

`claims.csv` columns: `status, tracking, carrier, service, ship_date, promised, commitment_applied, delivered_local, late_by, late_by_minutes, dest_tz, rule_applied, rule_verification, claim_deadline, file_from, citation, source_url, reason`.

`commitment_applied` is the commitment after any adjustments (holidays, NDA Saver end of day, carrier extensions). `reason` records each adjustment and assumption that was applied.

| Status | Meaning |
|---|---|
| `CLAIM` | Late beyond the grace period, guaranteed service, no exclusion, window open |
| `ON_TIME` | Delivered (or attempted) within the commitment plus grace |
| `NOT_GUARANTEED` | Service suspended, never guaranteed, or not in the rule table |
| `EXCLUDED` | Late, but an exception code, destination, residential rule or calendar rule removes it |
| `WINDOW_EXPIRED` | Would be a claim, but `as_of` is after the claim deadline |
| `PENDING` | Not delivered yet |
| `REVIEW` | Missing or ambiguous data (no zone, unknown exception code, date-only delivery) |
| `ERROR` | A value could not be parsed |

## Carrier terms

Checked on **2026-09-27** by reading the carrier pages and documents below. Guarantees change often: re-check before relying on them.

**FedEx.** Sources: [money-back guarantee page](https://www.fedex.com/en-us/service-guide/money-back-guarantee.html) and [FedEx Service Guide 2026 (PDF)](https://www.fedex.com/content/dam/fedex/us-united-states/services/Service_Guide_2026.pdf).
- FedEx's current page does not cover the 2020 suspension; its history begins in 2025. The guarantee was reinstated for packages shipped on or after **Jan 13, 2026** for U.S. domestic **First Overnight, Priority Overnight, Standard Overnight, 2Day A.M.** and the Extra Hours contract variants. For International First/Priority/Priority Express/Priority Freight/Priority DirectDistribution (+ Freight) it was reinstated from ship date **Feb 12, 2026**. It was suspended again for these services for shipments from Dec 1, 2025 until those reinstatements.
- The guarantee "for all other FedEx services will remain suspended until further notice." That covers FedEx 2Day, Express Saver, Ground, Home Delivery and the rest. The Service Guide adds: "There are no delivery commitments for shipments on which the money-back guarantee is suspended."
- A service failure is delivery **60 seconds or more** after the commitment (MBG policy A).
- Request within **15 calendar days of the invoice date**, or of the ship date when paying by credit card, cash, check or money order (D.3). Refunds and credits go to the payer only, one per package.
- Exceptions include Liabilities Not Assumed (e.g. weather/acts of God), incorrect address or ZIP, recipient unavailable or refusing to accept or sign, Hold at Location reroutes, undeliverable or returned packages, dangerous goods (dry ice only is OK), security or regulatory delays, oversize packages, and bad payer credit. A delivery scheduled for the Wednesday before Thanksgiving and made within 90 minutes of the commitment does not qualify. Neither does one scheduled in the 7 days before Christmas and made by end of day. A holiday extends the commitment by the length of the holiday, and a delivery change requested by the shipper or recipient adjusts it.
- A temporary extension applied on Sept 9–10, 2026: +90 minutes for commitments at 12 p.m. or earlier (and residential 1:30 p.m.) on listed services.

**UPS.** Sources: [UPS Service Guarantee page](https://www.ups.com/us/en/support/shipping-support/legal-terms-conditions/ups-service-guarantee) (ups.com/serviceguarantee) and [UPS Tariff/Terms and Conditions of Service – United States, effective Apr 17, 2026 (PDF)](https://www.ups.com/assets/resources/webcontent/en_US/terms_service_us.pdf), section 54.
- The guarantee is **currently active** for **Next Day Air Early, Next Day Air, Next Day Air Saver, 2nd Day Air A.M.** and for the Worldwide Express Plus/Express/Express NA1/Express Saver/Saver/Express Freight (+ Midday) international services. It "remains suspended for all other UPS shipments and services", which includes Ground, 2nd Day Air, 3 Day Select and Ground Saver.
- The Next Day Air Saver commitment is extended to **11:59 p.m.** until further notice. Since Oct 18, 2021, residential Next Day Air with a 10:30 a.m. commitment is extended to **12:00 p.m.**
- "On time" means delivery is **attempted before or within 60 seconds** of the published time.
- Notify UPS within **15 calendar days of the scheduled delivery date**, or of the date UPS amended the delivery time, whichever is later. Notification is through the Billing Center or the IVR. The refund covers transportation charges only (not fuel or other fees) and goes to the payer only.
- Exclusions (54.2) include 2nd Day Air A.M. residential deliveries, Large Package/Additional Handling packages, Call Tags, and Delivery Intercept / Delivery Change / My Choice requests. Delays beyond UPS's control are also excluded: refusal or unavailability of the recipient, acts of God, adverse weather, customs, security regulations, strikes, network disruptions, pandemic and more. Undeliverable returned shipments are excluded too (sec. 37).

**USPS.** Source: [Domestic Mail Manual 604.9 (Refunds), incl. 9.5 and Exhibit 9.2.1](https://pe.usps.com/text/dmm300/604.htm).
- Only **Priority Mail Express** has a delivery-failure postage refund. USPS refunds the postage when an item is not delivered, not attempted, or not made available by the date and time specified at mailing (9.5.1). Priority Mail and Ground Advantage have no money-back guarantee.
- Apply **no sooner than 2 days and no later than 30 days from the mailing date**. With an extra service the window is 30–60 days (Exhibit 9.2.1). Apply online at usps.com/help or on PS Form 3533. Only the mailer may apply.
- There is no refund (except for loss) for items to or from **Alaska, Hawaii, Guam, American Samoa, CNMI, Palau, the Marshall Islands or Micronesia**. There is also no refund for PME 1-Day items mailed **Dec 22–25** that were delivered within 2 business days. Other exclusions: law-enforcement detention, strikes, incorrect ZIP or address, forwarding or return, addressee hold requests, undeliverable items, and HQ-authorized acts of God or transport disruptions (9.5.5).

**Unverified or assumed items (edit `rules.json` if you know better):**
- Holiday lists are placeholders (`holidays_verified: false`). Replace them with ups.com/holidays and FedEx's published schedules.
- The international FedEx claim window is assumed to match the domestic 15 days (`claim_window.verified: false`).
- Date-only commitments are read as 23:59:59 local for FedEx and UPS (never flags a same-day delivery). For USPS PME they are read as 18:00 local, which is an assumption. Give the exact commitment time when you have it.
- The FedEx Sept 2026 extension is applied to all four domestic time-definite services when the commitment is 1:30 p.m. or earlier. FedEx's notice did not list Standard Overnight, so this errs toward not flagging.
- Shipments before Dec 1, 2025 (FedEx) have no rule and default to not guaranteed. For UPS, the current terms are applied regardless of ship date.

## Architecture

```
src/late_delivery_auditor/
  data/rules.json   carrier + rule table (edit or replace with --rules)
  rules.py          load/validate rules, normalise carrier & service names, match by ship date
  timezones.py      state -> IANA zone, ZIP3 -> state, ZIP3 overrides for split states
  timeparse.py      tolerant date/time parsing, aware-datetime construction, duration formatting
  audit.py          per-row decision pipeline -> Result(status, reason, deadlines, ...)
  csvio.py          header aliasing / delimiter detection, claims CSV writer
  cli.py            `late-audit audit` and `late-audit rules`
```

Each row goes through these steps: match a rule by carrier, service and ship date; check destination and residential exclusions; resolve the destination zone; build the commitment; apply holiday, rule and calendar adjustments; take the earlier of delivery and first attempt; compare against the commitment plus grace; check exception codes and the ship-window rules; compute the claim window; check it against `--as-of`.

**Time-zone caveats.** Split states are resolved only where a whole 3-digit ZIP prefix is on the other side of the line: El Paso TX, the Florida panhandle, East Tennessee, West Kentucky, NW/SW Indiana, the Nebraska panhandle, western South and North Dakota, the Idaho panhandle, and Ontario OR. Several areas are not resolved: western Kansas, Michigan's four Central-time UP counties, the Navajo Nation (DST in Arizona), Alaska west of 169.5°W, and border towns in general. Pass `dest_tz` for those. Military ZIPs go to review.

## Tech stack

Python 3.10+ standard library only (`zoneinfo`, `csv`, `argparse`, `dataclasses`). The optional `PyYAML` extra enables YAML rule files. Tests use pytest.

## Status / limitations

- The tool does not file claims or call carrier APIs, and it does not fetch tracking. You supply the CSV, and it runs fully offline.
- The tool does not check payer identity (refunds are payer-only), multi-piece proration, UPS brokerage conditions for international shipments, or origin-side USPS exclusions (items mailed *from* AK or HI).
- The commitment must come from the carrier. A marketplace's "promised delivery date" is the marketplace's promise to the buyer, not the carrier's guarantee, and using it will produce false candidates.
- The rule table reflects terms read on 2026-09-27. The tool cannot know about later suspensions.
- The sample data is synthetic. The tracking numbers are invented, in carrier-like test formats.

## License

MIT, see [LICENSE](LICENSE).
