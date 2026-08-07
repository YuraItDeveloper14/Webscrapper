"""Tell a mobile number from a landline, so calls land on a person.

A mobile usually rings the owner; a landline rings a desk. This is decided from
the operator prefix — deterministic and free. US numbers are the exception:
mobiles and landlines share area codes there, so the type is simply unknown
without a paid carrier lookup.
"""
from __future__ import annotations

import re

MOBILE = "mobile"
LANDLINE = "landline"

# National (trunk-stripped) prefixes that mean "mobile", per collection country.
_UA_MOBILE = {"39", "50", "63", "66", "67", "68", "73", "89",
              "91", "92", "93", "94", "95", "96", "97", "98", "99"}
_PL_MOBILE = {"45", "50", "51", "53", "57", "60", "66", "69", "72", "73", "78", "79", "88"}

_DIAL = {"Україна": "380", "Польща": "48", "Велика Британія": "44",
         "Німеччина": "49", "Чехія": "420", "США": "1"}


def _national(digits: str, cc: str) -> str:
    """Strip the country code / trunk zero, leaving the national number."""
    if digits.startswith(cc):
        return digits[len(cc):]
    if digits.startswith("0"):
        return digits[1:]
    return digits


def _country_code(phone_digits: str, country: str | None) -> str:
    """Dialling code from the lead's country, or from the number's own prefix."""
    cc = _DIAL.get((country or "").strip())
    if cc:
        return cc
    for code in ("380", "420", "48", "49", "44", "1"):  # longest first
        if phone_digits.startswith(code):
            return code
    return ""


def phone_kind(phone: str | None, country: str | None = "") -> str:
    """Return "mobile", "landline" or "" when it cannot be told."""
    if not phone:
        return ""
    cc = _country_code(re.sub(r"\D", "", phone), country)
    if not cc:
        return ""
    kinds = set()
    for part in re.split(r"[;,/]| або ", phone):
        digits = re.sub(r"\D", "", part)
        if len(digits) < 7:
            continue
        nat = _national(digits, cc)
        if cc == "380":
            kinds.add(MOBILE if nat[:2] in _UA_MOBILE else LANDLINE)
        elif cc == "48":
            kinds.add(MOBILE if nat[:2] in _PL_MOBILE else LANDLINE)
        elif cc == "44":
            kinds.add(MOBILE if nat.startswith("7") else LANDLINE)
        elif cc == "49":
            kinds.add(MOBILE if nat[:3] in {"151", "152", "155", "157", "159", "160", "162",
                                            "163", "170", "171", "172", "173", "174", "175",
                                            "176", "177", "178", "179"} else LANDLINE)
        elif cc == "420":
            kinds.add(MOBILE if nat[:1] in {"6", "7"} else LANDLINE)
        # "1" (US/Canada): area codes are shared, so the type is not knowable here
    if MOBILE in kinds:          # a mobile among several numbers is the one to call
        return MOBILE
    return LANDLINE if LANDLINE in kinds else ""


LABEL = {MOBILE: "мобільний", LANDLINE: "стаціонарний"}
