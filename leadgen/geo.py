"""Geography for collection: Country -> Region (whole admin area, scraped at once).

The user picks a country and a region (oblast / voivodeship / state) and we scrape
the WHOLE region's bounding box — no per-city picking. Region names are chosen so
Nominatim resolves them to the right admin area.
"""
from __future__ import annotations

COUNTRIES: dict[str, dict] = {
    "Україна": {"suffix": "Ukraine", "kind": "oblast", "regions": [
        "м. Київ", "Київська", "Львівська", "Харківська", "Одеська",
        "Дніпропетровська", "Запорізька", "Вінницька", "Полтавська",
        "Івано-Франківська", "Тернопільська", "Волинська", "Рівненська",
        "Хмельницька", "Черкаська", "Чернівецька", "Чернігівська", "Житомирська",
        "Сумська", "Миколаївська", "Херсонська", "Кіровоградська", "Закарпатська",
    ]},
    "Польща": {"suffix": "Poland", "kind": "plain", "regions": [
        "Mazowieckie", "Małopolskie", "Dolnośląskie", "Wielkopolskie",
        "Pomorskie", "Śląskie", "Łódzkie", "Lubelskie", "Podkarpackie",
        "Zachodniopomorskie",
    ]},
    "Велика Британія": {"suffix": "United Kingdom", "kind": "plain", "regions": [
        "Greater London", "Greater Manchester", "West Midlands", "West Yorkshire",
        "Merseyside", "Scotland", "Wales",
    ]},
    "Німеччина": {"suffix": "Germany", "kind": "plain", "regions": [
        "Berlin", "Bayern", "Hamburg", "Nordrhein-Westfalen", "Hessen",
        "Baden-Württemberg", "Sachsen",
    ]},
    "Чехія": {"suffix": "Czechia", "kind": "plain", "regions": [
        "Praha", "Jihomoravský kraj", "Moravskoslezský kraj", "Plzeňský kraj",
    ]},
    "США": {"suffix": "USA", "kind": "plain", "regions": [
        "California", "New York", "Texas", "Florida", "Illinois", "Washington",
    ]},
}

# OSM business category -> Ukrainian label shown in the UI.
CATEGORY_LABELS: dict[str, str] = {
    "cafe": "Кав'ярні",
    "restaurant": "Ресторани",
    "bakery": "Пекарні",
    "bar": "Бари та паби",
    "hotel": "Готелі",
    "dentist": "Стоматології",
    "doctor": "Лікарі та клініки",
    "pharmacy": "Аптеки",
    "beauty": "Салони краси",
    "hairdresser": "Перукарні",
    "gym": "Спортзали та фітнес",
    "car_repair": "Автосервіси",
    "lawyer": "Юристи",
    "real_estate": "Нерухомість",
    "shop": "Магазини",
}


# Raw OSM category values -> short Ukrainian label for the table badge.
CATEGORY_ONE: dict[str, str] = {
    "cafe": "кав'ярня", "restaurant": "ресторан", "fast_food": "фастфуд",
    "bakery": "пекарня", "bar": "бар", "pub": "паб", "nightclub": "нічний клуб",
    "hotel": "готель", "guest_house": "готель", "hostel": "хостел",
    "apartment": "апартаменти", "motel": "мотель",
    "dentist": "стоматологія", "doctors": "клініка", "clinic": "клініка",
    "veterinary": "ветклініка", "pharmacy": "аптека",
    "beauty": "салон краси", "hairdresser": "перукарня", "massage": "масаж",
    "fitness_centre": "спортзал", "sports_centre": "спорткомплекс",
    "car_repair": "автосервіс", "car_wash": "автомийка", "tyres": "шиномонтаж",
    "lawyer": "юрист", "estate_agent": "нерухомість", "company": "компанія",
    "shop": "магазин", "supermarket": "супермаркет", "convenience": "продукти",
    "clothes": "одяг", "florist": "квіти", "furniture": "меблі",
}


def category_label(value: str) -> str:
    """Ukrainian label for a stored OSM category (falls back to the raw value)."""
    return CATEGORY_ONE.get((value or "").strip(), value or "")


def geocode_query(country: str, region: str) -> str:
    """Nominatim query for a whole region, e.g. 'Львівська область, Ukraine'."""
    meta = COUNTRIES.get(country, {})
    suffix = meta.get("suffix", "")
    if meta.get("kind") == "oblast":
        if region.startswith("м. "):
            return f"{region[3:]}, {suffix}"
        return f"{region} область, {suffix}"
    return f"{region}, {suffix}" if suffix else region
