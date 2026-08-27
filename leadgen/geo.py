"""Geography for collection: Country -> Region (whole admin area, scraped at once).

The user picks a country and a region (oblast / voivodeship / state) and we scrape
the WHOLE region's bounding box — no per-city picking. Region names are chosen so
Nominatim resolves them to the right admin area.
"""
from __future__ import annotations

COUNTRY = "Україна"

# Kyiv first, then all 24 oblasts alphabetically.
COUNTRIES: dict[str, dict] = {
    COUNTRY: {"suffix": "Ukraine", "kind": "oblast", "regions": [
        "м. Київ",
        "Вінницька", "Волинська", "Дніпропетровська", "Донецька", "Житомирська",
        "Закарпатська", "Запорізька", "Івано-Франківська", "Київська",
        "Кіровоградська", "Луганська", "Львівська", "Миколаївська", "Одеська",
        "Полтавська", "Рівненська", "Сумська", "Тернопільська", "Харківська",
        "Херсонська", "Хмельницька", "Черкаська", "Чернівецька", "Чернігівська",
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


# Colour per trade, so a glance down the table separates the niches. Ties a
# category to one hue everywhere it appears — decoration would be noise.
CATEGORY_HUE: dict[str, str] = {
    "cafe": "amber", "restaurant": "amber", "fast_food": "amber", "bakery": "amber",
    "bar": "grape", "pub": "grape", "nightclub": "grape",
    "hotel": "violet", "guest_house": "violet", "hostel": "violet",
    "apartment": "violet", "motel": "violet",
    "dentist": "teal", "doctors": "teal", "clinic": "teal", "veterinary": "teal",
    "pharmacy": "green",
    "beauty": "pink", "hairdresser": "pink", "massage": "pink",
    "fitness_centre": "orange", "sports_centre": "orange",
    "car_repair": "blue", "car_wash": "blue", "tyres": "blue",
    "lawyer": "slate", "estate_agent": "slate", "company": "slate",
    "shop": "cyan", "supermarket": "cyan", "convenience": "cyan",
    "clothes": "cyan", "florist": "pink", "furniture": "cyan",
}


def category_hue(value: str) -> str:
    return CATEGORY_HUE.get((value or "").strip(), "slate")


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
