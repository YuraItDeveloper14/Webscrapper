"""Regression tests for bugs fixed after the first release."""
import sqlite3

import pytest

from leadgen.extract_emails import _clean, _with_scheme, has_viewport
from leadgen.phone import LANDLINE, MOBILE, phone_kind
from leadgen.score import AGGREGATORS, SOCIAL, host_matches, like_patterns, opportunity
from leadgen.scrape_osm import _social_link
from leadgen.verify import page_has_phone


# -- social / rented-page detection matches hosts, not substrings -----------

@pytest.mark.parametrize("url", [
    "https://dropbox.com/s/menu",      # contains "x.com"
    "https://mart.med.ua",             # contains "t.me"
    "https://gym.menu",                # contains "m.me"
    "https://book.ru",                 # contains "ok.ru"
])
def test_real_sites_are_not_taken_for_social_pages(url):
    r = opportunity({"website": url})
    assert "лише соцмережа" not in r["reasons"]
    assert r["tier"] == "cold"


@pytest.mark.parametrize("url", [
    "https://t.me/somebakery",
    "https://www.instagram.com/somebakery",
    "instagram.com/somebakery",
    "HTTPS://M.FACEBOOK.COM/somebakery",
    "https://taplink.cc/somebakery",
])
def test_social_pages_are_still_found(url):
    assert host_matches(url, SOCIAL)
    assert opportunity({"website": url})["reasons"][0] == "лише соцмережа"


def test_path_tokens():
    assert host_matches("https://www.google.com/maps/place/x", AGGREGATORS)
    assert not host_matches("https://www.google.com/search?q=x", AGGREGATORS)


def test_sql_patterns_agree_with_host_matching():
    urls = ["https://dropbox.com/s/menu", "https://mart.med.ua", "https://gym.menu",
            "https://book.ru", "https://t.me/somebakery", "https://www.instagram.com/x",
            "instagram.com/x", "https://somebakery.wixsite.com/home",
            "https://www.google.com/maps/place/x", "https://example.org",
            "https://taplink.cc/x", "http://x.com", "https://notinstagram.com/x"]
    db = sqlite3.connect(":memory:")
    db.execute("create table t (u text)")
    db.executemany("insert into t values (?)", [(u,) for u in urls])
    patterns = like_patterns((*SOCIAL, *AGGREGATORS))
    where = " or ".join("u like ?" for _ in patterns)
    hits = {row[0] for row in db.execute(f"select u from t where {where}", patterns)}
    expected = {u for u in urls if host_matches(u, (*SOCIAL, *AGGREGATORS))}
    assert hits == expected


# -- social link from OSM tags ----------------------------------------------

def test_social_link_without_scheme_is_kept_as_a_link():
    assert _social_link({"contact:instagram": "instagram.com/somebakery"}) == \
        "https://instagram.com/somebakery"
    assert _social_link({"contact:instagram": "@somebakery"}) == \
        "https://instagram.com/somebakery"
    assert _social_link({"contact:tiktok": "@somebakery"}) == "https://tiktok.com/@somebakery"


def test_website_tag_counts_as_social_only_for_social_hosts():
    assert _social_link({"website": "https://t.me/somebakery"}) == "https://t.me/somebakery"
    assert _social_link({"website": "https://mart.med.ua"}) == ""


# -- email extraction helpers ------------------------------------------------

def test_scheme_is_added_only_when_missing():
    assert _with_scheme("httpie.io") == "https://httpie.io"
    assert _with_scheme("example.org") == "https://example.org"
    assert _with_scheme("HTTP://example.org") == "HTTP://example.org"
    assert _with_scheme(" https://example.org ") == "https://example.org"


@pytest.mark.parametrize("html,expected", [
    ('<meta name="viewport" content="width=device-width">', True),
    ("<meta name='viewport' content='width=device-width'>", True),
    ('<meta content="width=device-width" name=viewport>', True),
    ('<META NAME="VIEWPORT" CONTENT="width=device-width">', True),
    ('<meta name="description" content="viewport">', False),
    ("<p>no meta here</p>", False),
])
def test_viewport_detection(html, expected):
    assert has_viewport(html) is expected


def test_clean_drops_asset_names_and_placeholders():
    got = _clean({"logo@2x.png", "info@bakery.ua", "you@example.com", "name@bakery.ua",
                  "owner.personal@gmail.com"})
    assert got[0] == "info@bakery.ua"
    assert "logo@2x.png" not in got
    assert "you@example.com" not in got
    assert "name@bakery.ua" not in got


# -- phone matching on a fetched page ------------------------------------------

def test_phone_on_page_is_matched_only_inside_a_phone():
    keys = {"1234567"}
    assert page_has_phone("Тел: +380 (67) 123-45-67", keys)
    assert page_has_phone('<a href="tel:+380671234567">call</a>', keys)
    # unrelated numbers separated by words must not be glued into a match
    assert not page_has_phone("Ціна 067 12 грн, знижка 34567 бонусів", keys)


def test_old_long_distance_prefix():
    assert phone_kind("8 067 123 45 67", "Україна") == MOBILE
    assert phone_kind("8 044 123 45 67", "Україна") == LANDLINE
