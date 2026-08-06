"""Scrape business listings from OpenStreetMap via the Overpass API.

Why OSM instead of Google Maps: Overpass is a plain HTTP+JSON endpoint, so it
needs no browser and runs anywhere. Many businesses already carry an email in
their OSM tags (email / contact:email); for the rest we have their website and
extract_emails.py finds the address. Coverage is smaller than Google Maps but
the data is clean, free, and there is no bot-detection to fight.

Flow:  city name --(Nominatim)--> bbox --(Overpass)--> businesses
"""
from __future__ import annotations

import asyncio
import httpx

HEADERS = {"User-Agent": "leadgen/1.0 (OSM lead research; contact via project owner)"}

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]

# Human category -> list of OSM tag filters. Extend freely.
CATEGORY_FILTERS: dict[str, list[str]] = {
    "cafe":        ["amenity=cafe"],
    "coffee":      ["amenity=cafe"],
    "restaurant":  ["amenity=restaurant"],
    "bakery":      ["shop=bakery"],
    "bar":         ["amenity=bar", "amenity=pub"],
    "hotel":       ["tourism=hotel", "tourism=guest_house"],
    "dentist":     ["amenity=dentist", "healthcare=dentist"],
    "doctor":      ["amenity=doctors", "healthcare=doctor"],
    "pharmacy":    ["amenity=pharmacy"],
    "beauty":      ["shop=beauty", "shop=hairdresser"],
    "hairdresser": ["shop=hairdresser"],
    "gym":         ["leisure=fitness_centre", "sport=fitness"],
    "car_repair":  ["shop=car_repair"],
    "lawyer":      ["office=lawyer"],
    "real_estate": ["office=estate_agent"],
    "shop":        ["shop"],  # any shop
}


async def geocode_city(city: str, client: httpx.AsyncClient) -> tuple[float, float, float, float]:
    """Return (south, west, north, east) bbox for a city name via Nominatim."""
    r = await client.get(NOMINATIM, params={"q": city, "format": "json", "limit": 1},
                         headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"City not found: {city!r}")
    # Nominatim boundingbox = [minlat, maxlat, minlon, maxlon]
    minlat, maxlat, minlon, maxlon = map(float, data[0]["boundingbox"])
    return (minlat, minlon, maxlat, maxlon)  # south, west, north, east


def _build_query(filters: list[str], bbox: tuple, timeout: int = 90, out_limit: int = 600) -> str:
    s, w, n, e = bbox
    box = f"{s},{w},{n},{e}"
    parts = []
    for f in filters:
        # node + way so we catch both point and building-mapped businesses
        parts.append(f"  node[{f}]({box});")
        parts.append(f"  way[{f}]({box});")
    body = "\n".join(parts)
    # cap how many elements Overpass returns so whole-region queries stay fast
    return f"[out:json][timeout:{timeout}];\n(\n{body}\n);\nout center tags {out_limit};"


async def _overpass(query: str, client: httpx.AsyncClient, attempts: int = 3) -> list[dict]:
    """POST a query to Overpass, cycling endpoints and retrying transient loads.

    504/429 and timeouts are common when the public servers are busy, so we
    retry each endpoint a few times with a growing backoff before giving up.
    """
    last = None
    for attempt in range(attempts):
        for ep in OVERPASS_ENDPOINTS:
            try:
                r = await client.post(ep, data={"data": query}, headers=HEADERS, timeout=180)
                if r.status_code == 200:
                    return r.json().get("elements", [])
                last = f"{ep} -> HTTP {r.status_code}"
                if r.status_code not in (429, 502, 503, 504):
                    return []  # a real client error (e.g. bad query) — don't hammer
            except Exception as exc:
                last = f"{ep} -> {exc}"
        if attempt < attempts - 1:
            await asyncio.sleep(3 * (attempt + 1))  # 3s, 6s backoff between rounds
    raise RuntimeError(f"Overpass busy after {attempts} tries: {last}")


def _tag(t: dict, *keys: str) -> str:
    for k in keys:
        if t.get(k):
            return t[k]
    return ""


def _to_lead(el: dict, query: str, geo: dict) -> dict:
    t = el.get("tags", {})
    street = _tag(t, "addr:street")
    house = _tag(t, "addr:housenumber")
    city = _tag(t, "addr:city") or geo.get("city", "")
    address = " ".join(x for x in [f"{street} {house}".strip(), city] if x).strip(", ")
    osm_id = f"{el.get('type', 'node')}/{el.get('id')}"
    # coordinates: nodes carry lat/lon directly, ways/relations carry a center
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")
    return {
        "name": _tag(t, "name", "name:en", "brand"),
        "website": _tag(t, "website", "contact:website"),
        "email": _tag(t, "email", "contact:email"),
        "phone": _tag(t, "phone", "contact:phone"),
        "address": address,
        "category": _tag(t, "amenity", "shop", "tourism", "office", "healthcare", "leisure"),
        "rating": "",
        "query": query,
        "maps_url": f"https://www.openstreetmap.org/{osm_id}",  # stable dedup id
        "lat": lat, "lon": lon,
        "country": geo.get("country", ""),
        "region": geo.get("region", ""),
        "city": geo.get("city", ""),
    }


async def scrape_osm(geocode_q: str, category: str, max_results: int = 200,
                     country: str = "", region: str = "", city: str = "") -> list[dict]:
    """Return business lead dicts for a category within a place, from OSM.

    `geocode_q` is what Nominatim resolves (e.g. "Lviv, Ukraine"); country/region/
    city are clean tags stored on each lead for later filtering.
    """
    filters = CATEGORY_FILTERS.get(category.lower())
    if filters is None:
        # Allow a raw OSM filter like "amenity=cafe" to be passed straight through.
        filters = [category] if "=" in category else ["shop"]
    geo = {"country": country, "region": region, "city": city or geocode_q.split(",")[0].strip()}
    async with httpx.AsyncClient() as client:
        bbox = await geocode_city(geocode_q, client)
        query = _build_query(filters, bbox, out_limit=max_results + 100)
        elements = await _overpass(query, client)
    leads, seen = [], set()
    for el in elements:
        lead = _to_lead(el, f"{category} {geo['city']}", geo)
        if not lead["name"] or lead["maps_url"] in seen:
            continue
        seen.add(lead["maps_url"])
        leads.append(lead)
        if len(leads) >= max_results:
            break
    return leads


if __name__ == "__main__":
    import sys, json
    city = sys.argv[1] if len(sys.argv) > 1 else "Lviv"
    cat = sys.argv[2] if len(sys.argv) > 2 else "cafe"
    data = asyncio.run(scrape_osm(city, cat, 200))
    print(json.dumps(data, ensure_ascii=False, indent=2))
    print(f"\n{len(data)} businesses | {sum(1 for d in data if d['website'])} with site | "
          f"{sum(1 for d in data if d['email'])} with email in OSM")
