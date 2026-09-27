"""Load and query the data-driven carrier rule table (JSON, or YAML if PyYAML is installed)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from importlib import resources
from pathlib import Path
from typing import Any

from .timeparse import parse_date

REQUIRED_RULE_KEYS = ("id", "carrier", "services", "guaranteed", "verification", "verified_on", "source_url", "citation")
VERIFICATION_VALUES = ("verified", "unverified")


class RulesError(ValueError):
    pass


def normalise_service(text: str | None, prefixes: list[str] | tuple[str, ...] = ()) -> str:
    """Lower-case, drop trademarks/punctuation, collapse spaces and strip a carrier prefix.

    ``"FedEx 2Day® A.M."`` -> ``"2day am"``; ``"FEDEX_2_DAY_AM"`` -> ``"2 day am"``;
    ``"UPS Next Day Air Saver®"`` -> ``"next day air saver"``.
    """
    s = (text or "").lower()
    s = s.replace("®", " ").replace("™", " ").replace(".", "")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    for p in prefixes:
        if s == p:
            return ""
        if s.startswith(p + " "):
            s = s[len(p) + 1 :]
            break
    return s


def normalise_carrier_name(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


@dataclass
class Rule:
    id: str
    carrier: str
    services: list[str]
    guaranteed: bool
    verification: str
    verified_on: str
    source_url: str
    citation: str
    effective_ship_from: date | None = None
    effective_ship_to: date | None = None
    grace_seconds: int = 60
    date_only_commitment_time: str = "23:59:59"
    claim_window: dict[str, Any] = field(default_factory=dict)
    excluded_dest_states: list[str] = field(default_factory=list)
    residential_excluded_services: list[str] = field(default_factory=list)
    commitment_adjustments: list[dict[str, Any]] = field(default_factory=list)
    terms_url: str = ""
    notes: str = ""

    def covers_ship_date(self, ship: date | None) -> bool:
        if ship is None:
            return self.effective_ship_from is None and self.effective_ship_to is None
        if self.effective_ship_from and ship < self.effective_ship_from:
            return False
        if self.effective_ship_to and ship > self.effective_ship_to:
            return False
        return True


@dataclass
class Carrier:
    code: str
    aliases: list[str]
    service_prefixes: list[str]
    holiday_handling: str
    holidays: set[date]
    holidays_verified: bool
    exclusion_codes: dict[str, str]
    calendar_adjustments: list[dict[str, Any]]


@dataclass
class RuleSet:
    rules: list[Rule]
    carriers: dict[str, Carrier]
    default_reason: str
    checked_on: str
    source: str

    def carrier_code(self, raw: str | None) -> str | None:
        n = normalise_carrier_name(raw)
        if not n:
            return None
        for code, c in self.carriers.items():
            if n == code.lower() or n in c.aliases:
                return code
        return None

    def match(self, carrier_code: str | None, service_raw: str | None, ship: date | None) -> tuple[Rule | None, str]:
        """Return ``(rule, note)``.

        ``rule`` is None when nothing applies; ``note`` then explains why
        (unknown carrier, unknown service, or ship date outside every rule's window).
        """
        if carrier_code is None:
            return None, "unknown carrier"
        carrier = self.carriers[carrier_code]
        svc = normalise_service(service_raw, carrier.service_prefixes)
        candidates = [r for r in self.rules if r.carrier == carrier_code and svc in r.services]
        if not candidates:
            return None, f"service {service_raw!r} not in rule table"
        for r in candidates:
            if r.covers_ship_date(ship):
                return r, ""
        return None, f"ship date {ship} is outside every rule's effective window for {service_raw!r}"


def _opt_date(v: Any, where: str) -> date | None:
    if v in (None, ""):
        return None
    try:
        return parse_date(str(v))
    except ValueError as e:
        raise RulesError(f"{where}: {e}") from None


def _load_raw(path: str | Path | None) -> tuple[dict[str, Any], str]:
    if path is None:
        text = resources.files("late_delivery_auditor").joinpath("data/rules.json").read_text(encoding="utf-8")
        return json.loads(text), "built-in rules.json"
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError as e:  # pragma: no cover - depends on environment
            raise RulesError("YAML rules need PyYAML: pip install 'late-delivery-auditor[yaml]'") from e
        return yaml.safe_load(text), str(p)
    return json.loads(text), str(p)


def load_rules(path: str | Path | None = None) -> RuleSet:
    raw, source = _load_raw(path)
    if not isinstance(raw, dict) or "rules" not in raw or "carriers" not in raw:
        raise RulesError(f"{source}: expected top-level 'carriers' and 'rules'")

    carriers: dict[str, Carrier] = {}
    for code, c in raw["carriers"].items():
        code_u = code.upper()
        carriers[code_u] = Carrier(
            code=code_u,
            aliases=[normalise_carrier_name(a) for a in c.get("aliases", [])],
            service_prefixes=[normalise_carrier_name(a) for a in c.get("service_prefixes", [code.lower()])],
            holiday_handling=c.get("holiday_handling", "none"),
            holidays={_opt_date(d, f"{code}.holidays") for d in c.get("holidays", [])},  # type: ignore[misc]
            holidays_verified=bool(c.get("holidays_verified", False)),
            exclusion_codes={k.upper(): v for k, v in c.get("exclusion_codes", {}).items()},
            calendar_adjustments=list(c.get("calendar_adjustments", [])),
        )

    rules: list[Rule] = []
    seen: set[str] = set()
    for i, r in enumerate(raw["rules"]):
        where = f"{source}: rules[{i}]"
        missing = [k for k in REQUIRED_RULE_KEYS if k not in r]
        if missing:
            raise RulesError(f"{where} missing {missing}")
        if r["id"] in seen:
            raise RulesError(f"{where}: duplicate id {r['id']!r}")
        seen.add(r["id"])
        carrier = str(r["carrier"]).upper()
        if carrier not in carriers:
            raise RulesError(f"{where}: carrier {carrier!r} not defined under 'carriers'")
        if r["verification"] not in VERIFICATION_VALUES:
            raise RulesError(f"{where}: verification must be one of {VERIFICATION_VALUES}")
        if r["guaranteed"] and not r.get("claim_window", {}).get("days"):
            raise RulesError(f"{where}: guaranteed rules need claim_window.days")
        prefixes = carriers[carrier].service_prefixes
        rules.append(
            Rule(
                id=r["id"],
                carrier=carrier,
                services=[normalise_service(s, prefixes) for s in r["services"]],
                guaranteed=bool(r["guaranteed"]),
                verification=r["verification"],
                verified_on=str(r["verified_on"]),
                source_url=r["source_url"],
                citation=r["citation"],
                effective_ship_from=_opt_date(r.get("effective_ship_from"), where),
                effective_ship_to=_opt_date(r.get("effective_ship_to"), where),
                grace_seconds=int(r.get("grace_seconds", 60)),
                date_only_commitment_time=str(r.get("date_only_commitment_time", "23:59:59")),
                claim_window=dict(r.get("claim_window", {})),
                excluded_dest_states=[s.upper() for s in r.get("excluded_dest_states", [])],
                residential_excluded_services=[normalise_service(s, prefixes) for s in r.get("residential_excluded_services", [])],
                commitment_adjustments=list(r.get("commitment_adjustments", [])),
                terms_url=r.get("terms_url", ""),
                notes=r.get("notes", ""),
            )
        )
    return RuleSet(
        rules=rules,
        carriers=carriers,
        default_reason=raw.get("default", {}).get("reason", "No matching rule; treated as not guaranteed."),
        checked_on=str(raw.get("checked_on", "")),
        source=source,
    )


def raw_rules_json(path: str | Path | None = None) -> str:
    raw, _ = _load_raw(path)
    return json.dumps(raw, indent=2)
