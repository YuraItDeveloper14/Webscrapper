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


async def geocode_area(place: str, client: httpx.AsyncClient) -> dict:
    """Resolve a place to its OSM area id (exact borders) plus a bbox fallback.

    Querying by area keeps results inside the region; a bounding box would spill
    over into neighbouring countries along the border.
    """
    r = await client.get(NOMINATIM, params={"q": place, "format": "json", "limit": 1},
                         headers=HEADERS, timeout=30)
    r.raise_for_status()
    data = r.json()
    if not data:
        raise ValueError(f"Place not found: {place!r}")
    hit = data[0]
    # Nominatim boundingbox = [minlat, maxlat, minlon, maxlon]
    minlat, maxlat, minlon, maxlon = map(float, hit["boundingbox"])
    area_id = None
    if hit.get("osm_type") == "relation":   # Overpass area ids: 3600000000 + relation id
        area_id = 3600000000 + int(hit["osm_id"])
    return {"area_id": area_id, "bbox": (minlat, minlon, maxlat, maxlon)}


def _build_query(filters: list[str], area_id: int | None = None, bbox: tuple | None = None,
                 timeout: int = 120, out_limit: int = 600) -> str:
    """Build an Overpass query scoped to an exact area, or to a bbox as fallback."""
    if area_id:
        head, scope = f"area({area_id})->.a;", "(area.a)"
    else:
        s, w, n, e = bbox
        head, scope = "", f"({s},{w},{n},{e})"
    parts = []
    for f in filters:
        # node + way so we catch both point and building-mapped businesses
        parts.append(f"  node[{f}]{scope};")
        parts.append(f"  way[{f}]{scope};")
    body = "\n".join(parts)
    # cap how many elements Overpass returns so whole-region queries stay fast
    return f"[out:json][timeout:{timeout}];\n{head}\n(\n{body}\n);\nout center tags {out_limit};"


async def _overpass(query: str, client: httpx.AsyncClient, attempts: int = 3) -> list[dict]:
    """POST a query to Overpass, cycling endpoints and retrying transient loads.

    504/429 and timeouts are common when the public servers are busy, so we
    retry each endpoint a few times with a growing backoff before giving up.

    An empty list means Overpass really found nothing. Any failure raises, so a
    rejected or throttled request is never mistaken for an empty region.
    """
    last = None
    for attempt in range(attempts):
        rejected = 0
        for ep in OVERPASS_ENDPOINTS:
            try:
                r = await client.post(ep, data={"data": query}, headers=HEADERS, timeout=180)
                if r.status_code == 200:
                    return r.json().get("elements", [])
                last = f"{ep} -> HTTP {r.status_code}"
                if r.status_code not in (429, 502, 503, 504):
                    rejected += 1  # this mirror refused it; the others may not
            except Exception as exc:
                last = f"{ep} -> {exc}"
        if rejected == len(OVERPASS_ENDPOINTS):   # every mirror refused — a bad query
            raise RuntimeError(f"Overpass rejected the query: {last}")
        if attempt < attempts - 1:
            await asyncio.sleep(3 * (attempt + 1))  # 3s, 6s backoff between rounds
    raise RuntimeError(f"Overpass busy after {attempts} tries: {last}")


def _tag(t: dict, *keys: str) -> str:
    for k in keys:
        if t.get(k):
            return t[k]
    return ""


_SOCIAL_HOSTS = ("instagram.com", "facebook.com", "fb.com", "t.me", "tiktok.com")


def _social_link(t: dict) -> str:
    """Best social-media profile for a business (many SMBs have only this)."""
    for key, base in (("contact:instagram", "https://instagram.com/"),
                      ("contact:facebook", "https://facebook.com/"),
                      ("contact:tiktok", "https://tiktok.com/@")):
        v = t.get(key)
        if v:
            return v if v.startswith("http") else base + v.lstrip("@/")
    # sometimes the "website" tag is actually a social page
    site = (t.get("website") or t.get("contact:website") or "").lower()
    if any(h in site for h in _SOCIAL_HOSTS):
        return t.get("website") or t.get("contact:website")
    return ""


def _to_lead(el: dict, query: str, geo: dict) -> dict:
    t = el.get("tags", {})
    street = _tag(t, "addr:street")
    house = _tag(t, "addr:housenumber")
    # the real town from OSM — never the region name, which would be misleading
    city = _tag(t, "addr:city", "addr:town", "addr:village") or geo.get("city", "")
    address = " ".join(x for x in [f"{street} {house}".strip(), city] if x).strip(", ")
    osm_id = f"{el.get('type', 'node')}/{el.get('id')}"
    # coordinates: nodes carry lat/lon directly, ways/relations carry a center
    lat = el.get("lat") or el.get("center", {}).get("lat")
    lon = el.get("lon") or el.get("center", {}).get("lon")
    social = _social_link(t)
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
        "city": city,
        "social": social,
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
    geo = {"country": country, "region": region, "city": city}
    async with httpx.AsyncClient() as client:
        place = await geocode_area(geocode_q, client)
        limit = max_results + 100
        elements = []
        if place["area_id"]:
            elements = await _overpass(
                _build_query(filters, area_id=place["area_id"], out_limit=limit), client)
        if not elements:  # no boundary relation, or the area query came back empty
            elements = await _overpass(
                _build_query(filters, bbox=place["bbox"], out_limit=limit), client)
    leads, seen = [], set()
    for el in elements:
        # `query` stores the chosen category key so a search can show exactly
        # its own results later (country + region + category)
        lead = _to_lead(el, category, geo)
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
