"""Map a US destination (state or ZIP code) to an IANA time zone.

Carrier delivery commitments ("by 10:30 a.m.") are stated in the destination's local
time, so every comparison needs the destination zone. Resolution order used by the
auditor is: an explicit ``dest_tz`` value, then a ZIP-prefix override, then the state
default.

Caveats (read before trusting a result):

* A state has one *default* zone here. Split states are handled only where a whole
  3-digit ZIP prefix sits clearly on the other side of a time-zone line
  (``ZIP3_TZ_OVERRIDES``). ZIP prefixes do not follow time-zone boundaries exactly,
  so a few border towns will still get the wrong zone. Pass ``dest_tz`` for those.
* Known splits NOT resolved by prefix: western Kansas (Sherman, Wallace, Greeley,
  Hamilton counties), the Nebraska/Oregon/North Dakota border counties outside the
  listed prefixes, Michigan's four Upper Peninsula counties on Central time, the
  Navajo Nation in Arizona (observes DST), and Alaska's Aleutian Islands west of
  169.5 W (America/Adak).
* Military (AA/AE/AP) and unknown codes return ``None``; the row is then sent to
  review instead of being guessed.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

STATE_TZ: dict[str, str] = {
    "AL": "America/Chicago",
    "AK": "America/Anchorage",
    "AZ": "America/Phoenix",
    "AR": "America/Chicago",
    "CA": "America/Los_Angeles",
    "CO": "America/Denver",
    "CT": "America/New_York",
    "DE": "America/New_York",
    "DC": "America/New_York",
    "FL": "America/New_York",
    "GA": "America/New_York",
    "HI": "Pacific/Honolulu",
    "ID": "America/Boise",
    "IL": "America/Chicago",
    "IN": "America/Indiana/Indianapolis",
    "IA": "America/Chicago",
    "KS": "America/Chicago",
    "KY": "America/New_York",
    "LA": "America/Chicago",
    "ME": "America/New_York",
    "MD": "America/New_York",
    "MA": "America/New_York",
    "MI": "America/Detroit",
    "MN": "America/Chicago",
    "MS": "America/Chicago",
    "MO": "America/Chicago",
    "MT": "America/Denver",
    "NE": "America/Chicago",
    "NV": "America/Los_Angeles",
    "NH": "America/New_York",
    "NJ": "America/New_York",
    "NM": "America/Denver",
    "NY": "America/New_York",
    "NC": "America/New_York",
    "ND": "America/Chicago",
    "OH": "America/New_York",
    "OK": "America/Chicago",
    "OR": "America/Los_Angeles",
    "PA": "America/New_York",
    "RI": "America/New_York",
    "SC": "America/New_York",
    "SD": "America/Chicago",
    "TN": "America/Chicago",
    "TX": "America/Chicago",
    "UT": "America/Denver",
    "VT": "America/New_York",
    "VA": "America/New_York",
    "WA": "America/Los_Angeles",
    "WV": "America/New_York",
    "WI": "America/Chicago",
    "WY": "America/Denver",
    "PR": "America/Puerto_Rico",
    "VI": "America/St_Thomas",
    "GU": "Pacific/Guam",
    "AS": "Pacific/Pago_Pago",
    "MP": "Pacific/Saipan",
}

# 3-digit ZIP prefixes whose zone differs from their state's default.
ZIP3_TZ_OVERRIDES: dict[str, str] = {
    # Texas: El Paso area is Mountain.
    "798": "America/Denver", "799": "America/Denver", "885": "America/Denver",
    # Florida panhandle west of the Apalachicola River is Central.
    "324": "America/Chicago", "325": "America/Chicago",
    # Tennessee: east Tennessee is Eastern (state default is Central).
    "373": "America/New_York", "374": "America/New_York", "376": "America/New_York",
    "377": "America/New_York", "378": "America/New_York", "379": "America/New_York",
    # Kentucky: western Kentucky is Central (state default is Eastern).
    "420": "America/Chicago", "421": "America/Chicago", "422": "America/Chicago",
    "423": "America/Chicago", "424": "America/Chicago",
    # Indiana: Gary and Evansville areas are Central.
    "463": "America/Chicago", "464": "America/Chicago",
    "476": "America/Chicago", "477": "America/Chicago",
    # Nebraska panhandle (Scottsbluff/Alliance) is Mountain.
    "693": "America/Denver",
    # South Dakota west river (Rapid City) is Mountain.
    "577": "America/Denver",
    # North Dakota southwest (Dickinson) is Mountain.
    "586": "America/Denver",
    # Idaho panhandle / Lewiston is Pacific.
    "835": "America/Los_Angeles", "838": "America/Los_Angeles",
    # Oregon: Ontario / Malheur County is Mountain.
    "979": "America/Boise",
}

# (first ZIP3, last ZIP3, state). Military and unassigned ranges are omitted.
_ZIP3_RANGES: list[tuple[int, int, str]] = [
    (5, 5, "NY"), (6, 7, "PR"), (8, 8, "VI"), (9, 9, "PR"),
    (10, 27, "MA"), (28, 29, "RI"), (30, 38, "NH"), (39, 49, "ME"),
    (50, 54, "VT"), (55, 55, "MA"), (56, 59, "VT"), (60, 69, "CT"),
    (70, 89, "NJ"), (100, 149, "NY"), (150, 196, "PA"), (197, 199, "DE"),
    (200, 200, "DC"), (201, 201, "VA"), (202, 205, "DC"), (206, 219, "MD"),
    (220, 246, "VA"), (247, 268, "WV"), (270, 289, "NC"), (290, 299, "SC"),
    (300, 319, "GA"), (320, 339, "FL"), (341, 349, "FL"), (350, 369, "AL"),
    (370, 385, "TN"), (386, 397, "MS"), (398, 399, "GA"), (400, 427, "KY"),
    (430, 459, "OH"), (460, 479, "IN"), (480, 499, "MI"), (500, 528, "IA"),
    (530, 549, "WI"), (550, 567, "MN"), (569, 569, "DC"), (570, 577, "SD"),
    (580, 588, "ND"), (590, 599, "MT"), (600, 629, "IL"), (630, 658, "MO"),
    (660, 679, "KS"), (680, 693, "NE"), (700, 714, "LA"), (716, 729, "AR"),
    (730, 732, "OK"), (733, 733, "TX"), (734, 749, "OK"), (750, 799, "TX"),
    (800, 816, "CO"), (820, 831, "WY"), (832, 838, "ID"), (840, 847, "UT"),
    (850, 865, "AZ"), (870, 884, "NM"), (885, 885, "TX"), (889, 898, "NV"),
    (900, 961, "CA"), (967, 968, "HI"), (970, 979, "OR"), (980, 994, "WA"),
    (995, 999, "AK"),
]


def zip_to_state(postal_code: str | None) -> str | None:
    """Return the USPS state code for a US ZIP (5-digit or ZIP+4), else None."""
    zip3 = _zip3(postal_code)
    if zip3 is None:
        return None
    n = int(zip3)
    for lo, hi, state in _ZIP3_RANGES:
        if lo <= n <= hi:
            return state
    return None


def _zip3(postal_code: str | None) -> str | None:
    if not postal_code:
        return None
    digits = "".join(ch for ch in str(postal_code).strip().split("-")[0] if ch.isdigit())
    if len(digits) in (3, 4):  # spreadsheets drop leading zeros ("2134" -> "02134")
        digits = digits.zfill(5)
    if len(digits) < 5:
        return None
    return digits[:3]


def resolve_tz(
    dest_tz: str | None = None,
    state: str | None = None,
    postal_code: str | None = None,
) -> tuple[ZoneInfo | None, str]:
    """Resolve the destination zone.

    Returns ``(zone or None, how)`` where ``how`` explains the source
    (``"explicit"``, ``"zip3 override"``, ``"state"``, ``"zip->state"``) or the reason
    resolution failed.
    """
    if dest_tz and dest_tz.strip():
        try:
            return ZoneInfo(dest_tz.strip()), "explicit"
        except (ZoneInfoNotFoundError, ValueError):
            return None, f"unknown time zone {dest_tz.strip()!r}"
    zip3 = _zip3(postal_code)
    if zip3 and zip3 in ZIP3_TZ_OVERRIDES:
        return ZoneInfo(ZIP3_TZ_OVERRIDES[zip3]), "zip3 override"
    st = (state or "").strip().upper()
    if st and st in STATE_TZ:
        return ZoneInfo(STATE_TZ[st]), "state"
    derived = zip_to_state(postal_code)
    if derived:
        return ZoneInfo(STATE_TZ[derived]), "zip->state"
    return None, "no destination time zone (add dest_tz, state or a US ZIP)"


def dest_state(state: str | None, postal_code: str | None) -> str | None:
    """Best-effort destination state: explicit value first, then from the ZIP."""
    st = (state or "").strip().upper()
    if st:
        return st
    return zip_to_state(postal_code)
