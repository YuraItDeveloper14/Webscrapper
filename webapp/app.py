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
                         CATEGORY_LABELS)
from leadgen.score import opportunity

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "leadgen-local-panel")
app.json.sort_keys = False  # preserve curated geo order (big cities first)

# ---- background job registry -------------------------------------------------
JOBS: dict[int, dict] = {}
_job_seq = 0
_lock = threading.Lock()


JOB_TTL = 6  # seconds a finished job message stays visible


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
           if j["state"] == "running" or (now - j.get("finished", 0)) < JOB_TTL]
    return sorted(out, key=lambda j: -j["id"])[:5]


def _run_scrape_job(jid: int, targets: list[dict], category: str, max_results: int) -> None:
    async def work():
        total_added = 0
        all_leads = []
        for tg in targets:
            leads = await scrape_osm(tg["geocode_q"], category, max_results=max_results,
                                     country=tg["country"], region=tg["region"], city=tg["city"])
            all_leads.extend(leads)
            JOBS[jid]["found"] = len(all_leads)
        await enrich_emails(all_leads, concurrency=8)
        JOBS[jid]["with_email"] = sum(1 for l in all_leads if l.get("email"))
        with db.connect() as conn:
            for lead in all_leads:
                if db.upsert_lead(conn, lead):
                    total_added += 1
        db.dedupe_existing()  # clean any node+way duplicates from this batch
        JOBS[jid]["added"] = total_added

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


app.jinja_env.globals["gmaps_url"] = gmaps_url
app.jinja_env.globals["opportunity"] = opportunity


def _query_and_filter(args, limit=1000):
    """Shared lead query + opportunity filter for the register and the map."""
    filters = {
        "search": args.get("search", "").strip(),
        "category": args.get("category", ""),
        "status": args.get("status", ""),
        "has_email": args.get("has_email", ""),
        "country": args.get("country", ""),
        "region": args.get("region", ""),
    }
    opp = args.get("opp", "")  # "" | nosite | weak | hot
    leads = db.query_leads(limit=limit, **filters)
    if opp:
        def keep(l):
            o = opportunity(l)
            if opp == "nosite":
                return not (l.get("website") or "").strip()
            if opp == "weak":
                return o["tier"] in ("hot", "warm")
            if opp == "hot":
                return o["tier"] == "hot"
            return True
        leads = [l for l in leads if keep(l)]
    return leads, filters, opp


# ---- routes ------------------------------------------------------------------
@app.route("/")
def index():
    leads, filters, opp = _query_and_filter(request.args)
    # best sales targets first (no site / weak site), unless the user chose a sort
    leads.sort(key=lambda l: -opportunity(l)["score"])
    return render_template(
        "leads.html",
        leads=leads,
        filters=filters,
        opp=opp,
        stats=db.stats(),
        prospects=db.prospect_count(),
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


@app.route("/scrape", methods=["POST"])
def scrape():
    country = request.form.get("country", "").strip()
    region = request.form.get("region", "").strip()
    category = request.form.get("category", "cafe").strip()
    if not region:
        flash("Обери область.", "error")
        return redirect(url_for("index"))
    target = {"geocode_q": geo_geocode_query(country, region),
              "country": country, "region": region, "city": ""}
    label = f"{CATEGORY_LABELS.get(category, category)} · {region}"
    jid = _new_job(label)
    threading.Thread(target=_run_scrape_job, args=(jid, [target], category, SCRAPE_MAX_PER_REGION),
                     daemon=True).start()
    flash(f"Збираю «{CATEGORY_LABELS.get(category, category)}» по всій області {region}. За хвилину зʼявиться внизу.", "ok")
    return redirect(url_for("index"))


@app.route("/lead/<int:lead_id>/status", methods=["POST"])
def lead_status(lead_id: int):
    db.set_status(lead_id, request.form.get("status", "new"))
    return ("", 204)


@app.route("/lead/<int:lead_id>/note", methods=["POST"])
def lead_note(lead_id: int):
    db.set_note(lead_id, request.form.get("note", ""))
    return ("", 204)


@app.route("/admin/clear", methods=["POST"])
def admin_clear():
    n = db.clear_all()
    flash(f"База очищена — видалено {n} записів.", "ok")
    return redirect(url_for("index"))


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
    leads = db.query_leads(
        search=request.args.get("search", ""),
        category=request.args.get("category", ""),
        status=request.args.get("status", ""),
        has_email=request.args.get("has_email", "yes"),
        limit=100000,
    )
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "email", "phone", "website", "address", "category", "status"])
    seen = set()
    for l in leads:
        key = (l.get("email") or "").lower()
        if key and key in seen:
            continue
        seen.add(key)
        w.writerow([l["name"], l["email"], l["phone"], l["website"],
                    l["address"], l["category"], l["email_status"]])
    return Response(
        "﻿" + buf.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=leads.csv"},
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
