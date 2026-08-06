"""leadgen CLI — scrape Google Maps, enrich with emails, store, export.

Examples:
    py run.py scrape "dentist Kyiv" --max 60
    py run.py scrape queries.txt --max 100        # one query per line
    py run.py export leads.csv
    py run.py stats
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from leadgen import db
from leadgen.scrape_maps import scrape_query
from leadgen.scrape_osm import scrape_osm
from leadgen.extract_emails import enrich_emails


def _load_queries(arg: str) -> list[str]:
    p = Path(arg)
    if p.exists() and p.suffix.lower() in (".txt", ".csv"):
        return [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [arg]


async def cmd_scrape(args) -> None:
    all_leads: list[dict] = []
    if args.source == "osm":
        # OSM source: browser-free, runs anywhere. Query = "category in City".
        for city in _load_queries(args.city):
            print(f"[osm] {args.category!r} in {city!r} ...")
            leads = await scrape_osm(city, args.category, max_results=args.max)
            print(f"      found {len(leads)} businesses "
                  f"({sum(1 for l in leads if l['website'])} with site, "
                  f"{sum(1 for l in leads if l['email'])} already with email)")
            all_leads.extend(leads)
    else:
        for q in _load_queries(args.query):
            print(f"[maps] scraping: {q!r} ...")
            leads = await scrape_query(q, max_results=args.max, headless=not args.show,
                                       debug=args.debug)
            print(f"       found {len(leads)} businesses "
                  f"({sum(1 for l in leads if l['website'])} with a website)")
            all_leads.extend(leads)

    print(f"[email] visiting {sum(1 for l in all_leads if l['website'])} sites to extract emails ...")
    await enrich_emails(all_leads, concurrency=args.concurrency)

    inserted = 0
    with db.connect() as conn:
        for lead in all_leads:
            if db.upsert_lead(conn, lead):
                inserted += 1
    s = db.stats()
    print(f"[db] +{inserted} new leads | total={s['total']} "
          f"with_email={s['with_email']} with_website={s['with_website']}")


def cmd_export(args) -> None:
    n = db.export_csv(Path(args.out), only_with_email=not args.all)
    print(f"[export] wrote {n} rows -> {args.out}")


def cmd_stats(_args) -> None:
    s = db.stats()
    print(f"total={s['total']}  with_email={s['with_email']}  with_website={s['with_website']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Google Maps -> email lead scraper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("scrape", help="scrape leads from OSM (default) or Google Maps")
    sp.add_argument("--source", choices=["osm", "maps"], default="osm",
                    help="osm = OpenStreetMap (no browser, default); maps = Google Maps (browser)")
    # OSM source args:
    sp.add_argument("--city", default="Lviv",
                    help="[osm] city name, or path to a .txt file of cities (one per line)")
    sp.add_argument("--category", default="cafe",
                    help="[osm] business category (cafe, restaurant, dentist, hotel, ...) "
                         "or a raw OSM filter like amenity=cafe")
    # Maps source args:
    sp.add_argument("--query", default="coffee shop Lviv",
                    help="[maps] search text, or path to a .txt file of queries")
    # shared:
    sp.add_argument("--max", type=int, default=200, help="max results per city/query")
    sp.add_argument("--concurrency", type=int, default=8, help="parallel site fetches")
    sp.add_argument("--show", action="store_true", help="[maps] show the browser (non-headless)")
    sp.add_argument("--debug", action="store_true",
                    help="[maps] dump data/_debug_feed.html + screenshot for selector tuning")
    sp.set_defaults(func=lambda a: asyncio.run(cmd_scrape(a)))

    ep = sub.add_parser("export", help="export leads to CSV")
    ep.add_argument("out", help="output .csv path")
    ep.add_argument("--all", action="store_true", help="include leads without an email")
    ep.set_defaults(func=cmd_export)

    st = sub.add_parser("stats", help="show database counts")
    st.set_defaults(func=cmd_stats)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
