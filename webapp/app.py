"""leadgen web panel — the hub for the whole pipeline.

Everything the CLI does is reachable here:
  * browse / search / filter leads          (/)
  * pull new leads from OSM (background job) (/scrape)
  * set a lead's status                      (/lead/<id>/status)
  * export the current DB to CSV             (/export)

Scraping is async and can take a while, so it runs in a background thread and
the page polls a tiny in-memory job registry for progress.
"""
from __future__ import annotations

import os
import asyncio
import threading
import time
import io
import csv
from pathlib import Path

from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, Response)

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from leadgen import db
from leadgen.scrape_osm import scrape_osm, CATEGORY_FILTERS
from leadgen.extract_emails import enrich_emails
from leadgen.geo import (COUNTRIES as GEO_COUNTRIES,
                         geocode_query as geo_geocode_query,
                         CATEGORY_LABELS, category_label)
from leadgen.score import opportunity
from leadgen.phone import phone_kind

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "leadgen-local-panel")
app.json.sort_keys = False  # preserve curated geo order (big cities first)

# ---- background job registry -------------------------------------------------
JOBS: dict[int, dict] = {}
_job_seq = 0
_lock = threading.Lock()


JOB_TTL = 6        # seconds a finished job message stays visible
ERROR_TTL = 45     # failures linger longer so they are not missed


def _new_job(label: str) -> int:
    global _job_seq
    with _lock:
        _job_seq += 1
        jid = _job_seq
        JOBS[jid] = {"id": jid, "label": label, "state": "running",
                     "found": 0, "with_email": 0, "added": 0,
                     "started": time.time(), "finished": 0, "error": ""}
    return jid


def _recent_jobs():
    """Running jobs + ones that finished within the last JOB_TTL seconds."""
    now = time.time()
    out = [j for j in JOBS.values()
           if j["state"] == "running"
           or (now - j.get("finished", 0)) < (ERROR_TTL if j["state"] == "error" else JOB_TTL)]
    return sorted(out, key=lambda j: -j["id"])[:5]


def _save_leads(leads: list[dict]) -> tuple[int, int]:
    """Write leads to the DB. Returns (newly added, rows that failed)."""
    added = failed = 0
    with db.connect() as conn:
        for lead in leads:
            # savepoint per row: one bad record must not discard the whole batch
            try:
                with conn.begin_nested():
                    if db.upsert_lead(conn, lead):
                        added += 1
            except Exception:
                failed += 1
    return added, failed


def _run_scrape_job(jid: int, targets: list[dict], category: str, max_results: int) -> None:
    async def work():
        all_leads = []
        for tg in targets:
            leads = await scrape_osm(tg["geocode_q"], category, max_results=max_results,
                                     country=tg["country"], region=tg["region"], city=tg["city"])
            all_leads.extend(leads)
            JOBS[jid]["found"] = len(all_leads)
        if not all_leads:
            raise RuntimeError("на цій території нічого не знайшлося")

        # Store the businesses BEFORE hunting for emails. That part visits every
        # site and takes minutes, and a restart mid-way used to throw the whole
        # scrape away; now the phones are already safe on disk.
        added, failed = _save_leads(all_leads)
        JOBS[jid]["added"] = added
        if failed == len(all_leads):
            raise RuntimeError("не вдалося зберегти жодного запису")

        await enrich_emails(all_leads, concurrency=8)
        JOBS[jid]["with_email"] = sum(1 for l in all_leads if l.get("email"))
        _save_leads(all_leads)  # second pass writes the found emails onto the rows
        db.dedupe_existing()    # node+way duplicates from this batch
        db.drop_cross_border()  # anything that slipped in from across a border

    try:
        asyncio.run(work())
        JOBS[jid]["state"] = "done"
    except Exception as exc:  # surface failures to the UI
        JOBS[jid]["state"] = "error"
        JOBS[jid]["error"] = str(exc)[:300]
    JOBS[jid]["finished"] = time.time()


def gmaps_url(lead: dict) -> str:
    """A Google Maps link that lands on the business (name + address, or coords)."""
    from urllib.parse import quote_plus
    q = " ".join(x for x in [lead.get("name", ""), lead.get("address", ""),
                             lead.get("city", ""), lead.get("country", "")] if x)
    if not q.strip() and lead.get("lat"):
        q = f"{lead['lat']},{lead['lon']}"
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(q)


def check_url(lead: dict) -> str:
    """Google search for this exact business — one click to see if a site exists.

    OSM often omits the website tag, so the panel never claims a business has no
    site; this link lets the answer be confirmed in seconds before dialling.
    """
    from urllib.parse import quote_plus
    where = lead.get("city") or lead.get("region") or lead.get("country") or ""
    return "https://www.google.com/search?q=" + quote_plus(f"{lead.get('name', '')} {where}".strip())


app.jinja_env.globals["gmaps_url"] = gmaps_url
app.jinja_env.globals["check_url"] = check_url
app.jinja_env.globals["opportunity"] = opportunity
app.jinja_env.globals["category_label"] = category_label
app.jinja_env.globals["phone_kind"] = phone_kind


def _query_and_filter(args, limit=1000):
    """Shared lead query + opportunity filter for the register and the map."""
    status = args.get("status", "")
    filters = {
        "search": args.get("search", "").strip(),
        "category": args.get("category", ""),
        "status": status,
        "has_email": "",
        # Results are scoped to the search that produced them (c/r/cat), so the
        # stored leads act as a cache and never leak into another search.
        # The status lists are the user's own marks, so they span every search.
        "country": "" if status else args.get("c", ""),
        "region": "" if status else args.get("r", ""),
        "cat_key": "" if status else args.get("cat", ""),
        "city": args.get("city", ""),
    }
    opp = args.get("opp", "")  # "" | nosite | weak
    working = not status       # hide handled leads and ones with no contact
    leads = db.query_leads(limit=limit, opp=opp, contactable=working,
                           hide_done=working, **filters)
    return leads, filters, opp, working


# ---- routes ------------------------------------------------------------------
@app.route("/")
def index():
    # "blank" = nothing chosen yet -> keep the results panel empty (limit 0 = no rows)
    blank = not any(request.args.get(k, "").strip()
                    for k in ("search", "status", "category", "opp", "all", "c", "r", "cat"))
    leads, filters, opp, working = _query_and_filter(
        request.args, limit=0 if blank else PAGE_LIMIT)
    total = 0 if blank else db.count_leads(opp=opp, contactable=working,
                                           hide_done=working, **filters)
    # when a search has rows but none are callable, say so instead of "nothing found"
    stored = db.count_leads(**filters) if (not blank and total == 0) else total
    # refine the SQL ordering within the page: best sales targets first
    leads.sort(key=lambda l: -opportunity(l)["score"])
    return render_template(
        "leads.html",
        leads=leads,
        total=total,
        stored=stored,
        filters=filters,
        opp=opp,
        blank=blank,
        status_counts=db.status_counts(),
        categories=db.distinct_categories(),
        countries_filter=db.distinct_col("country"),
        regions_filter=db.distinct_col("region"),
        statuses=db.VALID_STATUSES,
        category_labels=CATEGORY_LABELS,
        geo=GEO_COUNTRIES,
        jobs=_recent_jobs(),
    )


@app.route("/map")
def map_removed():
    # The map page was removed; forward old bookmarks to the register.
    return redirect(url_for("index"))


@app.errorhandler(404)
def _not_found(_e):
    # Single-page tool: any unknown URL just goes home instead of a raw 404.
    return redirect(url_for("index"))


# How many businesses to pull per region — fixed so the user never picks a limit.
SCRAPE_MAX_PER_REGION = 500
# Rows rendered per page; the header still reports the true match count.
PAGE_LIMIT = 500


@app.route("/scrape", methods=["POST"])
def scrape():
    country = request.form.get("country", "").strip()
    region = request.form.get("region", "").strip()
    category = request.form.get("category", "").strip()
    if not (country and region and category):
        flash("Оберіть країну, область і тип бізнесу.", "error")
        return redirect(url_for("index"))
    if any(j["state"] == "running" for j in JOBS.values()):
        flash("Збір уже триває — зачекайте, поки він завершиться.", "error")
        return redirect(url_for("index", all=1))
    # Already scraped this exact search and it produced usable leads — reuse it.
    # Counting only contactable rows means a run that failed or found nothing
    # callable can still be retried instead of being cached as an empty result.
    if db.count_leads(country=country, region=region, cat_key=category, contactable=True):
        return redirect(url_for("index", all=1, c=country, r=region, cat=category))
    target = {"geocode_q": geo_geocode_query(country, region),
              "country": country, "region": region, "city": ""}
    label = f"{CATEGORY_LABELS.get(category, category)} · {region}"
    jid = _new_job(label)
    threading.Thread(target=_run_scrape_job, args=(jid, [target], category, SCRAPE_MAX_PER_REGION),
                     daemon=True).start()
    # c/r/cat only repopulate the form after the redirect — they are not filters
    return redirect(url_for("index", all=1, c=country, r=region, cat=category))


@app.route("/lead/<int:lead_id>/status", methods=["POST"])
def lead_status(lead_id: int):
    db.set_status(lead_id, request.form.get("status", "new"))
    return ("", 204)


@app.route("/lead/<int:lead_id>/note", methods=["POST"])
def lead_note(lead_id: int):
    db.set_note(lead_id, request.form.get("note", ""))
    return ("", 204)


@app.route("/leads/bulk-status", methods=["POST"])
def bulk_status():
    ids = [int(i) for i in request.form.getlist("ids") if i.isdigit()]
    n = db.set_status_bulk(ids, request.form.get("status", "new"))
    return jsonify({"updated": n})


@app.route("/jobs.json")
def jobs_json():
    return jsonify(_recent_jobs())


@app.route("/export")
def export():
    # same query the page uses, so the file always matches what is on screen
    leads, _f, _o, _w = _query_and_filter(request.args, limit=100000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "email", "phone", "phone_type", "website", "social", "city",
                "region", "address", "category", "chance", "status"])
    seen = set()
    for l in leads:
        key = (l.get("email") or "").lower()
        if key and key in seen:
            continue
        seen.add(key)
        kind = phone_kind(l["phone"], l.get("country"))
        w.writerow([l["name"], l["email"], l["phone"],
                    {"mobile": "мобільний", "landline": "стаціонарний"}.get(kind, ""),
                    l["website"], l.get("social") or "", l.get("city") or "",
                    l.get("region") or "", l["address"], l["category"],
                    opportunity(l)["reasons"][0], l["email_status"]])
    return Response(
        "﻿" + buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
