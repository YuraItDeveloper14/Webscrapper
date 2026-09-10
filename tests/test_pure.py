"""Unit tests for the parts of leadgen that need no network, browser or database.

Run from the repository root:

    python -m pytest -q tests
"""
from leadgen.geo import COUNTRIES, COUNTRY, category_hue, category_label, geocode_query
from leadgen.phone import LANDLINE, MOBILE, phone_kind
from leadgen.score import opportunity
from leadgen.verify import MAX_DOMAINS, _full_name_slug, candidate_domains, phone_keys


# -- phone_kind ------------------------------------------------------------

def test_phone_kind_empty():
    assert phone_kind(None) == ""
    assert phone_kind("") == ""


def test_ukrainian_mobile_and_landline():
    assert phone_kind("+380 67 123 45 67") == MOBILE
    assert phone_kind("+380 44 123 45 67") == LANDLINE      # Kyiv landline


def test_trunk_zero_needs_the_country():
    # "0..." alone does not reveal the country, so the type stays unknown
    assert phone_kind("067 123 45 67") == ""
    assert phone_kind("067 123 45 67", "Україна") == MOBILE
    assert phone_kind("067 123 45 67", "  Україна ") == MOBILE


def test_a_mobile_among_several_numbers_wins():
    assert phone_kind("+380 44 123 45 67; +380 67 765 43 21") == MOBILE
    assert phone_kind("044 123 45 67 або 050 111 22 33", "Україна") == MOBILE


def test_too_short_to_tell():
    assert phone_kind("+380 12") == ""


def test_other_countries():
    assert phone_kind("+48 501 234 567") == MOBILE           # Poland
    assert phone_kind("+44 7700 900123") == MOBILE           # UK mobile
    assert phone_kind("+44 20 7946 0958") == LANDLINE        # UK landline
    assert phone_kind("+49 151 23456789") == MOBILE          # Germany
    assert phone_kind("+420 601 123 456") == MOBILE          # Czechia


def test_us_numbers_are_unknowable():
    # mobiles and landlines share area codes in the US
    assert phone_kind("+1 212 555 0100") == ""


# -- geo -------------------------------------------------------------------

def test_every_oblast_plus_kyiv_is_listed():
    regions = COUNTRIES[COUNTRY]["regions"]
    assert regions[0] == "м. Київ"
    assert len(regions) == 25
    assert len(set(regions)) == len(regions)


def test_geocode_query():
    assert geocode_query(COUNTRY, "Львівська") == "Львівська область, Ukraine"
    assert geocode_query(COUNTRY, "м. Київ") == "Київ, Ukraine"
    assert geocode_query("Unknown", "Somewhere") == "Somewhere"


def test_category_label_and_hue():
    assert category_label("cafe") == "кав'ярня"
    assert category_label(" cafe ") == "кав'ярня"
    assert category_label("unknown_tag") == "unknown_tag"
    assert category_label("") == ""
    assert category_hue("dentist") == "teal"
    assert category_hue("unknown_tag") == "slate"
    assert category_hue("") == "slate"


# -- opportunity -----------------------------------------------------------

def test_no_website_is_the_hottest_lead():
    r = opportunity({"website": ""})
    assert r["score"] == 60 and r["tier"] == "hot"
    assert r["reasons"] == ["сайт не вказано"]


def test_phone_is_noted_without_changing_the_score():
    r = opportunity({"website": "", "phone": "+380 67 123 45 67"})
    assert r["score"] == 60
    assert r["reasons"][-1] == "☎ телефон"


def test_social_only_and_rented_pages():
    social = opportunity({"website": "  HTTPS://INSTAGRAM.COM/somebakery "})
    assert (social["score"], social["tier"]) == (45, "hot")
    rented = opportunity({"website": "https://somebakery.wixsite.com/home"})
    assert (rented["score"], rented["tier"]) == (42, "hot")


def test_owned_site_signals():
    assert opportunity({"website": "http://example.org"})["tier"] == "warm"
    assert opportunity({"website": "https://example.org"})["tier"] == "cold"
    confirmed = opportunity({"website": "https://example.org", "site_ok": 1, "mobile": 0})
    assert confirmed["score"] == 16 and "не мобільний" in confirmed["reasons"]


def test_non_mobile_is_only_claimed_when_the_page_was_read():
    r = opportunity({"website": "https://example.org", "mobile": 0})
    assert r["score"] == 0
    assert r["reasons"] == ["має сайт"]


# -- verify ----------------------------------------------------------------

def test_phone_keys_are_format_independent():
    assert phone_keys(None) == set()
    assert phone_keys("12345") == set()
    assert phone_keys("+380 (67) 123-45-67") == phone_keys("0671234567") == {"1234567"}
    assert phone_keys("067 123 45 67; 044 765 43 21") == {"1234567", "7654321"}


def test_candidate_domains_follow_real_world_spelling():
    domains = candidate_domains("Авіцена", "Київ")
    assert domains[0] == "avitsena.kiev.ua"      # city TLD goes first
    assert "avicena.kiev.ua" in domains          # the spelling used in the wild
    assert "avitsena.com.ua" in domains


def test_candidate_domains_edge_cases():
    assert candidate_domains("", "") == []
    assert candidate_domains("Стоматологія Клініка") == []     # only trade words
    assert len(candidate_domains("Зуб Мудрості", "")) <= MAX_DOMAINS


def test_full_name_slug():
    assert _full_name_slug("zubnafeia.com.ua", "Зубна Фея") is True
    assert _full_name_slug("zubna.com.ua", "Зубна Фея") is False
    assert _full_name_slug("zubna.com.ua", "Зубна") is False   # one word is not proof
