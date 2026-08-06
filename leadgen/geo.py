"""Curated geography for the collection UI: Country -> Region -> Cities.

A whole-country Overpass query is not feasible (it times out and returns far
too much), so collection is always scoped to cities. This dataset lets the user
pick a country and region, then one or many cities; each city becomes its own
scrape job. City names are geocoded as "<city>, <suffix>" so Nominatim resolves
the right place across countries.
"""
from __future__ import annotations

COUNTRIES: dict[str, dict] = {
    "Україна": {
        "suffix": "Ukraine", "flag": "\U0001F1FA\U0001F1E6",
        "regions": {
            "м. Київ": ["Київ"],
            "Львівська": ["Львів", "Дрогобич", "Червоноград"],
            "Київська": ["Біла Церква", "Бровари", "Ірпінь"],
            "Харківська": ["Харків"],
            "Одеська": ["Одеса", "Ізмаїл"],
            "Дніпропетровська": ["Дніпро", "Кривий Ріг", "Кам'янське"],
            "Запорізька": ["Запоріжжя"],
            "Вінницька": ["Вінниця"],
            "Полтавська": ["Полтава", "Кременчук"],
            "Івано-Франківська": ["Івано-Франківськ"],
            "Тернопільська": ["Тернопіль"],
            "Волинська": ["Луцьк"],
            "Рівненська": ["Рівне"],
            "Хмельницька": ["Хмельницький"],
            "Черкаська": ["Черкаси"],
            "Чернівецька": ["Чернівці"],
            "Чернігівська": ["Чернігів"],
            "Житомирська": ["Житомир"],
            "Сумська": ["Суми"],
            "Миколаївська": ["Миколаїв"],
            "Херсонська": ["Херсон"],
            "Кіровоградська": ["Кропивницький"],
            "Закарпатська": ["Ужгород", "Мукачево"],
        },
    },
    "Польща": {
        "suffix": "Poland", "flag": "\U0001F1F5\U0001F1F1",
        "regions": {"Головні міста": [
            "Warszawa", "Kraków", "Wrocław", "Poznań", "Gdańsk",
            "Łódź", "Katowice", "Lublin", "Szczecin", "Rzeszów"]},
    },
    "Велика Британія": {
        "suffix": "United Kingdom", "flag": "\U0001F1EC\U0001F1E7",
        "regions": {"Головні міста": [
            "London", "Manchester", "Birmingham", "Leeds", "Liverpool",
            "Glasgow", "Edinburgh", "Bristol", "Cardiff"]},
    },
    "Німеччина": {
        "suffix": "Germany", "flag": "\U0001F1E9\U0001F1EA",
        "regions": {"Головні міста": [
            "Berlin", "München", "Hamburg", "Köln", "Frankfurt",
            "Stuttgart", "Düsseldorf", "Leipzig"]},
    },
    "Чехія": {
        "suffix": "Czechia", "flag": "\U0001F1E8\U0001F1FF",
        "regions": {"Головні міста": ["Praha", "Brno", "Ostrava", "Plzeň"]},
    },
    "США": {
        "suffix": "USA", "flag": "\U0001F1FA\U0001F1F8",
        "regions": {
            "California": ["Los Angeles", "San Francisco", "San Diego", "San Jose"],
            "New York": ["New York", "Buffalo"],
            "Texas": ["Houston", "Dallas", "Austin", "San Antonio"],
            "Florida": ["Miami", "Orlando", "Tampa"],
            "Illinois": ["Chicago"],
            "Washington": ["Seattle"],
        },
    },
}


def country_names() -> list[str]:
    return list(COUNTRIES.keys())


def geocode_query(country: str, city: str) -> str:
    """Full Nominatim query string, e.g. 'Ternopil, Ukraine'."""
    suffix = COUNTRIES.get(country, {}).get("suffix", "")
    return f"{city}, {suffix}" if suffix else city


def region_of(country: str, city: str) -> str:
    for region, cities in COUNTRIES.get(country, {}).get("regions", {}).items():
        if city in cities:
            return region
    return ""
