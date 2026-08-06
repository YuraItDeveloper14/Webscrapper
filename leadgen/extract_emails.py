"""Given a business website, find a contact email.

Approach: fetch the homepage, then a few likely contact pages, and pull emails
out of both the HTML (mailto: links) and the visible text. We prefer role-based
company addresses (info@, contact@) over generic free-mail where possible, and
drop obvious junk (example.com, image filenames misread as emails, etc.).
"""
from __future__ import annotations

import re
import asyncio
import urllib.parse

import httpx
from selectolax.parser import HTMLParser

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Pages that commonly carry a contact address.
CONTACT_PATHS = ["", "contact", "contact-us", "contacts", "about", "about-us",
                 "kontakt", "impressum", "support"]

# Extensions that look like emails but are really asset filenames.
JUNK_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "css", "js", "ico", "mp4", "pdf"}
JUNK_DOMAINS = {"example.com", "sentry.io", "wixpress.com", "domain.com", "email.com",
                "demolink.org", "yourdomain.com", "yoursite.com", "test.com",
                "sentry-next.wixpress.com", "example.org", "email.tld"}
# Local-parts that are obviously template placeholders, not real inboxes.
JUNK_LOCALS = {"example", "youremail", "email", "name", "user", "test", "sample"}

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
}


def _clean(emails: set[str]) -> list[str]:
    good = []
    for e in emails:
        e = e.strip().strip(".").lower()
        if e.count("@") != 1 or len(e) > 100:
            continue
        local, domain = e.split("@")
        tld = domain.rsplit(".", 1)[-1]
        if tld in JUNK_TLDS or domain in JUNK_DOMAINS or local in JUNK_LOCALS:
            continue
        good.append(e)
    # Rank: role-based addresses first, then shortest (usually the real one).
    role = ("info@", "contact@", "hello@", "office@", "sales@", "kontakt@")
    good = sorted(set(good), key=lambda e: (not e.startswith(role), len(e)))
    return good


def _emails_from_html(html: str) -> set[str]:
    found: set[str] = set()
    tree = HTMLParser(html)
    for node in tree.css("a[href^='mailto:']"):
        href = node.attributes.get("href", "")
        addr = urllib.parse.unquote(href[7:].split("?")[0])
        found.update(EMAIL_RE.findall(addr))
    # Also scan raw text (many sites print the address without a mailto link).
    found.update(EMAIL_RE.findall(tree.text(separator=" ")))
    return found


async def _fetch(client: httpx.AsyncClient, url: str) -> str:
    try:
        r = await client.get(url, timeout=12, follow_redirects=True)
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            return r.text
    except Exception:
        pass
    return ""


async def find_email(website: str, client: httpx.AsyncClient | None = None) -> str:
    """Return the best-guess contact email for a website, or "" if none found."""
    if not website:
        return ""
    if not website.startswith("http"):
        website = "https://" + website
    base = website.rstrip("/")
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(headers=HEADERS)
    try:
        all_emails: set[str] = set()
        for path in CONTACT_PATHS:
            url = base if path == "" else f"{base}/{path}"
            html = await _fetch(client, url)
            if not html:
                continue
            all_emails.update(_emails_from_html(html))
            cleaned = _clean(all_emails)
            if cleaned and path in ("", "contact", "contact-us", "contacts"):
                return cleaned[0]  # good hit early, stop crawling this site
        cleaned = _clean(all_emails)
        return cleaned[0] if cleaned else ""
    finally:
        if own_client:
            await client.aclose()


async def probe_site(website: str, client: httpx.AsyncClient) -> dict:
    """Fetch a homepage once and report live signals + any emails on it."""
    url = website if website.startswith("http") else "https://" + website
    out = {"site_ok": 0, "mobile": None, "emails": set()}
    try:
        r = await client.get(url, timeout=12, follow_redirects=True)
        if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
            out["site_ok"] = 1
            html = r.text
            out["mobile"] = 1 if 'name="viewport"' in html.lower() else 0
            out["emails"] = _emails_from_html(html)
    except Exception:
        pass
    return out


async def enrich_emails(leads: list[dict], concurrency: int = 8) -> list[dict]:
    """Fill in `email` + live site signals (site_ok/mobile) for leads with a site."""
    sem = asyncio.Semaphore(concurrency)
    async with httpx.AsyncClient(headers=HEADERS) as client:
        async def one(lead: dict):
            if not lead.get("website"):
                return
            async with sem:
                sig = await probe_site(lead["website"], client)
                lead["site_ok"] = sig["site_ok"]
                lead["mobile"] = sig["mobile"]
                if lead.get("email"):
                    return
                hits = _clean(sig["emails"])
                lead["email"] = hits[0] if hits else await find_email(lead["website"], client)
        await asyncio.gather(*(one(l) for l in leads))
    return leads


if __name__ == "__main__":
    import sys
    site = sys.argv[1] if len(sys.argv) > 1 else "https://www.python.org"
    print(asyncio.run(find_email(site)) or "(no email found)")
