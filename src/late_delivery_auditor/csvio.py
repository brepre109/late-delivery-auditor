"""Read shipment CSVs with flexible headers; write claims CSVs."""

from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Iterable, TextIO

from .audit import OUTPUT_COLUMNS, Result, Shipment

# Shipment field -> accepted header spellings (after normalisation: lower-case, non-alnum -> "_").
HEADER_ALIASES: dict[str, tuple[str, ...]] = {
    "tracking": ("tracking", "tracking_number", "tracking_no", "tracking_id", "tracking_num", "awb", "trackingnumber"),
    "carrier": ("carrier", "carrier_code", "carrier_name", "shipping_carrier"),
    "service": ("service", "service_level", "service_type", "service_code", "service_name", "shipping_service"),
    "ship_date": ("ship_date", "shipped_at", "ship_datetime", "shipped", "mailing_date", "mail_date", "pickup_date", "shipped_date"),
    "promised": ("promised", "promised_delivery", "promised_by", "committed_delivery", "commitment", "delivery_commitment",
                 "scheduled_delivery", "guaranteed_delivery", "guaranteed_by", "promised_delivery_date"),
    "delivered": ("delivered", "delivered_at", "delivery_date", "delivered_date", "actual_delivery", "delivered_timestamp", "delivery_time"),
    "first_attempt": ("first_attempt", "attempted_at", "first_delivery_attempt", "attempt"),
    "dest_postal_code": ("dest_postal_code", "dest_zip", "postal_code", "zip", "zip_code", "ship_to_zip", "ship_to_postal_code", "destination_zip"),
    "dest_state": ("dest_state", "state", "ship_to_state", "destination_state", "province"),
    "dest_tz": ("dest_tz", "destination_tz", "time_zone", "timezone", "tz"),
    "delivered_tz": ("delivered_tz", "delivery_tz", "delivered_time_zone"),
    "exception_codes": ("exception_codes", "exception_code", "exceptions", "exception", "delay_reason", "delay_reasons", "delay_codes"),
    "residential": ("residential", "is_residential", "address_type"),
    "invoice_date": ("invoice_date", "billed_date", "invoice"),
}
REQUIRED = ("tracking", "carrier", "service")


class CsvFormatError(ValueError):
    pass


def _norm_header(h: str) -> str:
    h = h.replace("﻿", "").replace("#", " number ").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", h).strip("_")


def map_headers(headers: list[str]) -> dict[str, str]:
    """Return {shipment_field: original_header}."""
    normed = {_norm_header(h): h for h in headers}
    out: dict[str, str] = {}
    for fld, aliases in HEADER_ALIASES.items():
        for a in aliases:
            if a in normed:
                out[fld] = normed[a]
                break
    missing = [f for f in REQUIRED if f not in out]
    if missing:
        raise CsvFormatError(f"missing required column(s): {', '.join(missing)} (have: {', '.join(headers)})")
    return out


def read_shipments(src: str | Path | TextIO) -> list[Shipment]:
    if isinstance(src, (str, Path)):
        text = Path(src).read_text(encoding="utf-8-sig")
    else:
        text = src.read()
    text = text.lstrip("﻿")
    # Pick the delimiter from the header line only: data cells (exception codes such as
    # "WEATHER;ADDRESS_CORRECTION") may contain other delimiters.
    header_line = text.splitlines()[0] if text.strip() else ""
    delimiter = max(",;\t|", key=header_line.count) if header_line else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    if not reader.fieldnames:
        raise CsvFormatError("empty CSV")
    cols = map_headers(list(reader.fieldnames))
    out: list[Shipment] = []
    for i, row in enumerate(reader, start=2):
        if not any((v or "").strip() for v in row.values()):
            continue
        vals = {f: (row.get(h) or "").strip() for f, h in cols.items()}
        out.append(Shipment(line=i, **{k: vals.get(k, "") for k in HEADER_ALIASES}))
    return out


def write_results(results: Iterable[Result], dest: str | Path | TextIO) -> int:
    rows = [r.as_row() for r in results]
    if isinstance(dest, (str, Path)):
        with open(dest, "w", newline="", encoding="utf-8") as fh:
            return _write(rows, fh)
    return _write(rows, dest)


def _write(rows: list[dict[str, str]], fh: TextIO) -> int:
    w = csv.DictWriter(fh, fieldnames=OUTPUT_COLUMNS)
    w.writeheader()
    w.writerows(rows)
    return len(rows)
