"""Opportunity scoring — how good a sales prospect a lead is.

The angle: businesses with no site, a social-only presence, a rented page-
builder link, no HTTPS, a dead site, or a non-mobile site are the best targets
for someone who sells websites, ads, and AI automation. We score each lead from
signals we already have (the website URL string) plus any live signals captured
when the site was fetched (site_ok / mobile).
"""
from __future__ import annotations

from urllib.parse import urlsplit

SOCIAL = ("facebook.com", "instagram.com", "fb.com", "m.me", "t.me", "tiktok.com",
          "twitter.com", "x.com", "vk.com", "ok.ru", "youtube.com", "linktr.ee",
          "taplink", "linktr")
# Rented page builders / aggregators / short links — not a real owned website.
AGGREGATORS = ("bit.ly", "goo.gl", "cutt.ly", "choiceqr.com", "virtual.ua",
               "google.com/maps", "maps.app.goo.gl", "sites.google.com",
               "wixsite.com", "business.site", "н4.biz")

TIERS = ("hot", "warm", "cold")


def _host_path(url: str) -> tuple[str, str]:
    u = (url or "").strip().lower()
    if "://" not in u:
        u = "http://" + u
    try:
        parts = urlsplit(u)
    except ValueError:
        return "", ""
    host = parts.hostname or ""
    if host.startswith("www."):
        host = host[4:]
    return host, parts.path or ""


def host_matches(url: str, tokens) -> bool:
    """True when the URL's host is one of `tokens` or a subdomain of one.

    Matching the whole URL string misfired: "x.com" sits inside "dropbox.com",
    "t.me" inside "mart.med.ua", "ok.ru" inside "book.ru". A token may carry a
    path ("google.com/maps"); a token without a dot ("taplink") is a brand
    fragment and is looked for anywhere in the host.
    """
    host, path = _host_path(url)
    if not host:
        return False
    for tok in tokens:
        if "/" in tok:
            t_host, t_path = tok.split("/", 1)
            if (host == t_host or host.endswith("." + t_host)) \
                    and path.lstrip("/").startswith(t_path):
                return True
        elif "." in tok:
            if host == tok or host.endswith("." + tok):
                return True
        elif tok in host:
            return True
    return False


def like_patterns(tokens) -> list[str]:
    """SQL LIKE patterns that pick the same hosts as `host_matches`.

    Ports and query-only URLs ("https://t.me?x") are the rare cases left out.
    """
    out: list[str] = []
    for tok in tokens:
        if "/" in tok:
            out += [f"{tok}%", f"%://{tok}%", f"%.{tok}%"]
        elif "." in tok:
            out += [tok, f"{tok}/%", f"%://{tok}", f"%://{tok}/%", f"%.{tok}", f"%.{tok}/%"]
        else:
            out.append(f"%{tok}%")
    return out


def opportunity(lead: dict) -> dict:
    """Return {score:int, tier:str, reasons:[str], angle:str} for a lead."""
    w = (lead.get("website") or "").strip().lower()
    reasons: list[str] = []
    score = 0

    if not w:
        score += 60
        # Careful wording on purpose: OpenStreetMap simply has no site listed,
        # which is not proof the business has none. Claiming otherwise on a call
        # is embarrassing, so the UI offers a one-click check instead.
        reasons.append("сайт не вказано")
        angle = "перевір і пропонуй сайт"
    elif host_matches(w, SOCIAL):
        score += 45
        reasons.append("лише соцмережа")
        angle = "сайт + реклама"
    elif host_matches(w, AGGREGATORS):
        score += 42
        reasons.append("не власний сайт")
        angle = "власний сайт"
    else:
        angle = "апгрейд / реклама / AI"
        if w.startswith("http://"):
            score += 22
            reasons.append("без HTTPS")
        # "site does not open" was claimed here from a single automated fetch and
        # was wrong often enough to embarrass a caller — plenty of sites simply
        # refuse bots. Non-mobile is only stated when the page was actually read.
        if lead.get("site_ok") == 1 and lead.get("mobile") == 0:
            score += 16
            reasons.append("не мобільний")
        if not reasons:
            reasons.append("має сайт")
            angle = "реклама / AI-автоматизація"

    # reachability bumps priority for cold calling (not opportunity itself)
    if lead.get("phone"):
        reasons.append("☎ телефон")

    tier = "hot" if score >= 42 else "warm" if score >= 16 else "cold"
    return {"score": score, "tier": tier, "reasons": reasons, "angle": angle}


# SQL fragment matching "weak or missing site" for fast DB-level counts.
PROSPECT_SQL = (
    "website IS NULL OR website = '' "
    "OR website LIKE 'http://%' "
    "OR website LIKE '%facebook.com%' OR website LIKE '%instagram.com%' "
    "OR website LIKE '%t.me%' OR website LIKE '%bit.ly%' "
    "OR website LIKE '%choiceqr.com%' OR website LIKE '%virtual.ua%' "
    "OR website LIKE '%maps.app.goo.gl%' OR website LIKE '%business.site%'"
)
