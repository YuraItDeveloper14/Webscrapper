"""Scrape business listings from Google Maps search results.

Google Maps itself does NOT expose emails. What we can reliably get here:
    name, website, phone, address, category, rating, maps_url
The website is the important one — extract_emails.py visits it to find the email.

Strategy: open the Maps search URL, scroll the results feed until it stops
growing (or we hit `max_results`), then read each card. We read from the feed
list rather than opening every place panel — far faster and less bot-like.
"""
from __future__ import annotations

import asyncio
import re
import urllib.parse
from dataclasses import dataclass, asdict

from playwright.async_api import async_playwright, Page

FEED_SELECTOR = 'div[role="feed"]'
CARD_SELECTOR = 'div[role="feed"] > div > div[jsaction]'


@dataclass
class Business:
    name: str = ""
    website: str = ""
    phone: str = ""
    address: str = ""
    category: str = ""
    rating: str = ""
    maps_url: str = ""


def _search_url(query: str) -> str:
    return "https://www.google.com/maps/search/" + urllib.parse.quote(query)


async def _dismiss_consent(page: Page) -> None:
    """Google may show a cookie/consent wall. Reject non-essential where possible."""
    for label in ("Reject all", "Відхилити все", "Alle ablehnen", "Odrzuć wszystko"):
        try:
            btn = page.get_by_role("button", name=label)
            if await btn.count():
                await btn.first.click(timeout=3000)
                await page.wait_for_timeout(800)
                return
        except Exception:
            pass


async def _scroll_feed(page: Page, max_results: int) -> None:
    """Scroll the results feed until it stops loading new cards or we have enough."""
    try:
        await page.wait_for_selector(FEED_SELECTOR, timeout=15000)
    except Exception:
        return  # single-result pages redirect straight to a place panel
    stable_rounds = 0
    last_count = 0
    for _ in range(60):
        cards = await page.query_selector_all(CARD_SELECTOR)
        count = len(cards)
        if count >= max_results:
            break
        if count == last_count:
            stable_rounds += 1
            if stable_rounds >= 3:
                break  # feed exhausted
        else:
            stable_rounds = 0
        last_count = count
        await page.eval_on_selector(FEED_SELECTOR, "el => el.scrollTo(0, el.scrollHeight)")
        await page.wait_for_timeout(1600)


async def _parse_cards(page: Page, query: str, max_results: int) -> list[Business]:
    cards = await page.query_selector_all(CARD_SELECTOR)
    out: list[Business] = []
    for card in cards[:max_results]:
        b = Business(query=query) if False else Business()  # keep dataclass simple
        # Name + maps link come from the main anchor.
        link = await card.query_selector('a[href*="/maps/place/"]')
        if link:
            b.maps_url = await link.get_attribute("href") or ""
            b.name = (await link.get_attribute("aria-label")) or ""
        # Website: Maps renders a dedicated "Visit site" link with a real http href.
        site = await card.query_selector('a[href^="http"]:not([href*="google.com"])[data-value], a[aria-label*="site"], a[aria-label*="сайт"]')
        if not site:
            # fallback: any external http anchor that isn't a google/maps link
            for a in await card.query_selector_all('a[href^="http"]'):
                href = await a.get_attribute("href") or ""
                if "google.com" not in href and "/maps/" not in href:
                    site = a
                    break
        if site:
            b.website = await site.get_attribute("href") or ""
        # Text blob holds rating / category / phone / address lines.
        text = (await card.inner_text()) if card else ""
        b.rating = _first(re.search(r"\b([0-5][.,]\d)\b", text))
        b.phone = _first(re.search(r"(\+?\d[\d\s\-()]{7,}\d)", text))
        if b.name:
            out.append(b)
    return out


def _first(m: re.Match | None) -> str:
    return m.group(1).strip() if m else ""


async def _dump_debug(page: Page) -> None:
    """Save the full page HTML + a screenshot for offline selector tuning."""
    from pathlib import Path
    out = Path(__file__).resolve().parent.parent / "data"
    out.mkdir(parents=True, exist_ok=True)
    html = await page.content()
    (out / "_debug_feed.html").write_text(html, encoding="utf-8")
    try:
        await page.screenshot(path=str(out / "_debug.png"), full_page=False)
    except Exception:
        pass
    print(f"[debug] wrote {out / '_debug_feed.html'} and _debug.png")


async def _launch(p, headless: bool):
    """Launch a Chromium-family browser, preferring the system browser.

    The bundled Playwright Chromium is broken on some Windows machines (a
    side-by-side / SxS activation error), while the system Edge or Chrome
    launches cleanly. Try those first, fall back to bundled Chromium last.
    """
    last_err = None
    for channel in ("msedge", "chrome", None):
        try:
            kwargs = {"headless": headless}
            if channel:
                kwargs["channel"] = channel
            return await p.chromium.launch(**kwargs)
        except Exception as e:  # channel not installed / broken build
            last_err = e
    raise RuntimeError(f"Could not launch any Chromium browser: {last_err}")


async def scrape_query(query: str, max_results: int = 100, headless: bool = True,
                       debug: bool = False) -> list[dict]:
    """Return a list of business dicts for a single Maps search query.

    When `debug` is True, dump the feed HTML and a screenshot to data/ so the
    selectors can be inspected/tuned against the live Google Maps markup.
    """
    async with async_playwright() as p:
        browser = await _launch(p, headless)
        context = await browser.new_context(
            locale="en-US",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"),
            viewport={"width": 1280, "height": 900},
        )
        page = await context.new_page()
        await page.goto(_search_url(query), wait_until="domcontentloaded", timeout=45000)
        await _dismiss_consent(page)
        await _scroll_feed(page, max_results)
        if debug:
            await _dump_debug(page)
        businesses = await _parse_cards(page, query, max_results)
        await browser.close()
    results = []
    for b in businesses:
        d = asdict(b)
        d["query"] = query
        results.append(d)
    return results


if __name__ == "__main__":
    import sys, json
    q = sys.argv[1] if len(sys.argv) > 1 else "coffee shop Kyiv"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 20
    data = asyncio.run(scrape_query(q, n, headless=True))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"\nFound {len(data)} businesses ({sum(1 for x in data if x['website'])} with website)")
