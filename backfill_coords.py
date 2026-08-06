"""One-off: backfill lat/lon for leads scraped before coordinates were captured.

Reads each lead's OSM id from maps_url, asks Overpass for the coordinates in
batches (cheap — a few queries for hundreds of ids), and updates the DB.
"""
import re
import time
import httpx
from sqlalchemy import text
from leadgen.db import connect, set_coords

HEADERS = {"User-Agent": "leadgen/1.0 backfill (contact via project owner)"}
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


def overpass(query):
    last = None
    for _ in range(3):
        for ep in ENDPOINTS:
            try:
                r = httpx.post(ep, data={"data": query}, headers=HEADERS, timeout=120)
                if r.status_code == 200:
                    return r.json().get("elements", [])
                last = f"{ep} {r.status_code}"
            except Exception as e:
                last = f"{ep} {e}"
        time.sleep(4)
    raise RuntimeError(f"overpass failed: {last}")


def main():
    with connect() as c:
        rows = c.execute(text(
            "SELECT id, maps_url FROM leads WHERE (lat IS NULL OR lon IS NULL) "
            "AND maps_url LIKE '%openstreetmap.org/%'"
        )).mappings().all()
    print("to backfill:", len(rows))

    by_type = {"node": {}, "way": {}, "relation": {}}
    for r in rows:
        m = re.search(r"openstreetmap\.org/(node|way|relation)/(\d+)", r["maps_url"])
        if m:
            by_type[m.group(1)][int(m.group(2))] = r["id"]

    updated = 0
    for typ, idmap in by_type.items():
        ids = list(idmap)
        for i in range(0, len(ids), 250):
            chunk = ids[i:i + 250]
            q = f"[out:json][timeout:120];{typ}(id:{','.join(map(str, chunk))});out center;"
            els = overpass(q)
            with connect() as c:
                for el in els:
                    lat = el.get("lat") or el.get("center", {}).get("lat")
                    lon = el.get("lon") or el.get("center", {}).get("lon")
                    lead_id = idmap.get(el.get("id"))
                    if lat and lon and lead_id:
                        set_coords(c, lead_id, lat, lon)
                        updated += 1
            print(f"  {typ}: chunk {i//250 + 1}, updated so far {updated}")
            time.sleep(1)
    print("DONE, updated:", updated)


if __name__ == "__main__":
    main()
