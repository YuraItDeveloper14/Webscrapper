"""Lead storage — one `leads` table, works on SQLite (local) and Postgres (prod).

Backend is chosen by the DATABASE_URL env var: when set (e.g. Render Postgres)
we use it, otherwise a local SQLite file. All queries go through SQLAlchemy Core
so the same code emits correct SQL for either dialect.
"""
from __future__ import annotations

import os
import re
import csv
from pathlib import Path
from contextlib import contextmanager

from sqlalchemy import (create_engine, MetaData, Table, Column, Integer, Float,
                        Text, DateTime, Index, select, insert, update, func,
                        or_, and_, case, distinct, text)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "leads.db"


def _engine_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        # Render/Heroku hand out postgres:// — SQLAlchemy 2.x wants the driver spelled out.
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://") and "+psycopg" not in url:
            url = "postgresql+psycopg://" + url[len("postgresql://"):]
        return url
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DB_PATH}"


ENGINE = create_engine(_engine_url(), future=True, pool_pre_ping=True)
IS_SQLITE = ENGINE.dialect.name == "sqlite"

_meta = MetaData()
LEADS = Table(
    "leads", _meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("name", Text), Column("website", Text), Column("email", Text),
    Column("phone", Text), Column("address", Text), Column("category", Text),
    Column("rating", Text), Column("query", Text),
    Column("maps_url", Text, unique=True),
    Column("email_status", Text, default="new"),
    Column("lat", Float), Column("lon", Float),
    Column("country", Text), Column("region", Text), Column("city", Text),
    Column("notes", Text),
    Column("site_ok", Integer), Column("mobile", Integer), Column("social", Text),
    Column("created_at", DateTime, server_default=func.now()),
    Index("idx_leads_email", "email"),
    Index("idx_leads_status", "email_status"),
    Index("idx_leads_country", "country"),
)

VALID_STATUSES = ("new", "marked", "success", "rejected")
# ~110 m: close enough to be the same business mapped twice, far enough apart
# that two branches of a chain stay separate rows.
COORD_EPS = 0.001
_LEAD_COLS = ("name", "website", "email", "phone", "address", "category", "rating",
              "query", "maps_url", "lat", "lon", "country", "region", "city",
              "site_ok", "mobile", "social")

# Columns added to the SQLite file after its first release (Postgres is created fresh).
_ADDED_COLUMNS = {"lat": "REAL", "lon": "REAL", "country": "TEXT", "region": "TEXT",
                  "city": "TEXT", "notes": "TEXT", "site_ok": "INTEGER", "mobile": "INTEGER",
                  "social": "TEXT"}


def _migrate(conn) -> None:
    """Add any missing columns to an existing table (SQLite or Postgres)."""
    if IS_SQLITE:
        have = {r[1] for r in conn.execute(text("PRAGMA table_info(leads)"))}
        for col, decl in _ADDED_COLUMNS.items():
            if col not in have:
                conn.execute(text(f"ALTER TABLE leads ADD COLUMN {col} {decl}"))
    else:  # Postgres supports IF NOT EXISTS
        for col, decl in _ADDED_COLUMNS.items():
            conn.execute(text(f"ALTER TABLE leads ADD COLUMN IF NOT EXISTS {col} {decl}"))


# Old status value -> new one (statuses were simplified to 3 + default).
_STATUS_REMAP = {"target": "marked", "client": "success", "no": "rejected",
                 "called": "new", "contacted": "new", "replied": "success",
                 "bounced": "rejected", "skip": "new"}


def init_db() -> None:
    _meta.create_all(ENGINE)
    with ENGINE.begin() as conn:
        _migrate(conn)
        for old, new in _STATUS_REMAP.items():
            conn.execute(update(LEADS).where(LEADS.c.email_status == old)
                         .values(email_status=new))
        # early region scrapes stored the region name in `city` — clear those,
        # and strip it back out of the address it was glued into
        conn.execute(update(LEADS).where(
            or_(LEADS.c.city == LEADS.c.region,
                LEADS.c.city == LEADS.c.region + " область")).values(city=None))
        conn.execute(update(LEADS).where(and_(
            LEADS.c.region.is_not(None),
            LEADS.c.address.like("% область%"))).values(
            address=func.trim(func.replace(LEADS.c.address, LEADS.c.region + " область", ""))))
        # …and where the address ended up being just the region ("Illinois")
        conn.execute(update(LEADS).where(
            LEADS.c.address == LEADS.c.region).values(address=None))


init_db()


@contextmanager
def connect():
    """Yield a transactional SQLAlchemy connection (commits on clean exit)."""
    with ENGINE.begin() as conn:
        yield conn


def upsert_lead(conn, lead: dict) -> bool:
    """Insert a lead, or backfill missing fields on an existing one.

    Returns True if a new row was inserted. Identity is `maps_url` when present,
    otherwise (name, address).
    """
    # Identity: same OSM url, OR the same name at (almost) the same spot / same
    # city — OSM often maps one business twice, as a node and as a building way.
    ident = [LEADS.c.maps_url == lead.get("maps_url")]
    nm = (lead.get("name") or "").strip()
    city = (lead.get("city") or "").strip()
    lat, lon = lead.get("lat"), lead.get("lon")
    # exact name match on purpose: SQLite's lower() is ASCII-only and would miss
    # Cyrillic, so case folding is done in Python by dedupe_existing() instead.
    same_name = LEADS.c.name == nm
    if nm and lat is not None and lon is not None:
        ident.append(and_(same_name,
                          LEADS.c.lat.between(lat - COORD_EPS, lat + COORD_EPS),
                          LEADS.c.lon.between(lon - COORD_EPS, lon + COORD_EPS)))
    if nm and city:
        ident.append(and_(same_name, LEADS.c.city == city))
    sel = select(LEADS.c.id, LEADS.c.email, LEADS.c.website).where(or_(*ident))
    row = conn.execute(sel).first()
    if row is None:
        conn.execute(insert(LEADS).values({c: lead.get(c) for c in _LEAD_COLS}))
        return True
    vals = {}
    if not row.email and lead.get("email"):
        vals["email"] = lead["email"]
    if not row.website and lead.get("website"):
        vals["website"] = lead["website"]
    for sig in ("site_ok", "mobile"):
        if lead.get(sig) is not None:
            vals[sig] = lead[sig]
    if vals:
        conn.execute(update(LEADS).where(LEADS.c.id == row.id).values(**vals))
    return False


def set_coords(conn, lead_id: int, lat: float, lon: float) -> None:
    conn.execute(update(LEADS).where(LEADS.c.id == lead_id).values(lat=lat, lon=lon))


def _has(col):
    return and_(col.is_not(None), col != "")


def _filter_conditions(search="", category="", status="", has_email="",
                       country="", region="", opp="", cat_key="", city="",
                       contactable=False, hide_done=False):
    conds = []
    if cat_key:  # the search that produced the lead (stored in `query`)
        conds.append(LEADS.c.query.like(cat_key + "%"))
    if contactable:  # nothing to do with a business you cannot reach
        conds.append(or_(_has(LEADS.c.email), _has(LEADS.c.phone), _has(LEADS.c.social)))
    if hide_done:  # already handled — they move to the Успішні / Відмови views
        conds.append(LEADS.c.email_status.notin_(("success", "rejected")))
    if search:
        like = f"%{search}%"
        conds.append(or_(LEADS.c.name.ilike(like), LEADS.c.email.ilike(like),
                         LEADS.c.address.ilike(like)))
    if category:
        conds.append(LEADS.c.category == category)
    if status:
        conds.append(LEADS.c.email_status == status)
    if country:
        conds.append(LEADS.c.country == country)
    if region:
        conds.append(LEADS.c.region == region)
    if city:
        conds.append(LEADS.c.city == city)
    if has_email == "yes":
        conds.append(and_(LEADS.c.email.is_not(None), LEADS.c.email != ""))
    elif has_email == "no":
        conds.append(or_(LEADS.c.email.is_(None), LEADS.c.email == ""))
    if opp == "nosite":
        conds.append(or_(LEADS.c.website.is_(None), LEADS.c.website == ""))
    elif opp:  # weak / hot — anything missing or with a poor site
        conds.append(_prospect_condition())
    return conds


# best sales targets first: no site at all, then a weak one, then the rest
_TARGET_ORDER = (
    case((or_(LEADS.c.website.is_(None), LEADS.c.website == ""), 0), else_=1),
    case((LEADS.c.site_ok == 0, 0), else_=1),
    case((or_(LEADS.c.email.is_(None), LEADS.c.email == ""), 1), else_=0),
    LEADS.c.name,
)


def query_leads(search: str = "", category: str = "", status: str = "",
                has_email: str = "", country: str = "", region: str = "",
                opp: str = "", cat_key: str = "", city: str = "",
                contactable: bool = False, hide_done: bool = False,
                limit: int = 500) -> list[dict]:
    conds = _filter_conditions(search, category, status, has_email, country, region,
                               opp, cat_key, city, contactable, hide_done)
    stmt = (select(LEADS).where(and_(*conds)) if conds else select(LEADS))
    stmt = stmt.order_by(*_TARGET_ORDER).limit(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings().all()]


def count_leads(search: str = "", category: str = "", status: str = "",
                has_email: str = "", country: str = "", region: str = "",
                opp: str = "", cat_key: str = "", city: str = "",
                contactable: bool = False, hide_done: bool = False) -> int:
    """How many leads match — so the UI can say 'showing 500 of 1143'."""
    conds = _filter_conditions(search, category, status, has_email, country, region,
                               opp, cat_key, city, contactable, hide_done)
    stmt = select(func.count()).select_from(LEADS)
    if conds:
        stmt = stmt.where(and_(*conds))
    with connect() as conn:
        return conn.execute(stmt).scalar_one()


def set_status(lead_id: int, status: str) -> bool:
    if status not in VALID_STATUSES:
        return False
    with connect() as conn:
        conn.execute(update(LEADS).where(LEADS.c.id == lead_id).values(email_status=status))
    return True


def set_note(lead_id: int, note: str) -> None:
    with connect() as conn:
        conn.execute(update(LEADS).where(LEADS.c.id == lead_id).values(notes=note[:2000]))


def clear_all() -> int:
    """Wipe every lead. Returns how many were deleted."""
    from sqlalchemy import delete
    with connect() as conn:
        n = conn.execute(select(func.count()).select_from(LEADS)).scalar_one()
        conn.execute(delete(LEADS))
    return n


# International dialling prefix per collection country.
COUNTRY_DIAL = {"Україна": "+380", "Польща": "+48", "Велика Британія": "+44",
                "Німеччина": "+49", "Чехія": "+420", "США": "+1"}


def drop_cross_border() -> int:
    """Delete leads whose phone belongs to a different country than the search.

    Older scrapes used a bounding box, which spilled over borders — a Zakarpattia
    run could return Slovak, Romanian or Hungarian places.
    """
    from sqlalchemy import delete
    with connect() as conn:
        rows = conn.execute(
            select(LEADS.c.id, LEADS.c.country, LEADS.c.phone)
            .where(and_(LEADS.c.phone.is_not(None), LEADS.c.phone.like("%+%")))
        ).mappings().all()
        remove = []
        for r in rows:
            want = COUNTRY_DIAL.get(r["country"] or "")
            phone = re.sub(r"[^\d+]", "", r["phone"] or "")
            if want and phone.startswith("+") and not phone.startswith(want):
                remove.append(r["id"])
        if remove:
            conn.execute(delete(LEADS).where(LEADS.c.id.in_(remove)))
    return len(remove)


def dedupe_existing() -> int:
    """Collapse rows sharing (name, city), keeping the most useful one.

    Preference: a marked/worked status > has email > has website > lowest id.
    Returns the number of rows removed.
    """
    from sqlalchemy import delete
    with connect() as conn:
        rows = conn.execute(select(
            LEADS.c.id, LEADS.c.name, LEADS.c.city, LEADS.c.email, LEADS.c.lat,
            LEADS.c.lon, LEADS.c.website, LEADS.c.phone, LEADS.c.social,
            LEADS.c.email_status)).mappings().all()
        groups: dict[tuple, list] = {}
        for r in rows:
            nm = (r["name"] or "").strip().lower()
            if not nm:
                continue
            if r["lat"] is not None and r["lon"] is not None:
                key = (nm, round(r["lat"], 3), round(r["lon"], 3))  # same name, same spot
            elif (r["city"] or "").strip():
                key = (nm, r["city"].strip().lower())
            else:
                continue  # not enough to tell duplicates apart safely
            groups.setdefault(key, []).append(r)

        def score(r):
            return (0 if r["email_status"] in ("new", None) else 1,
                    1 if r["email"] else 0, 1 if r["website"] else 0, -r["id"])

        remove = []
        for members in groups.values():
            if len(members) < 2:
                continue
            keep = max(members, key=score)
            # carry over any contact the kept row is missing before dropping the rest
            fill = {}
            for field in ("email", "website", "phone", "social"):
                if not keep.get(field):
                    for m in members:
                        if m["id"] != keep["id"] and m.get(field):
                            fill[field] = m[field]
                            break
            if fill:
                conn.execute(update(LEADS).where(LEADS.c.id == keep["id"]).values(**fill))
            remove += [m["id"] for m in members if m["id"] != keep["id"]]
        if remove:
            conn.execute(delete(LEADS).where(LEADS.c.id.in_(remove)))
    return len(remove)


def set_status_bulk(ids: list[int], status: str) -> int:
    if status not in VALID_STATUSES or not ids:
        return 0
    with connect() as conn:
        conn.execute(update(LEADS).where(LEADS.c.id.in_(ids)).values(email_status=status))
    return len(ids)


def distinct_col(col: str) -> list[str]:
    if col not in ("country", "region", "city", "category"):
        return []
    c = getattr(LEADS.c, col)
    stmt = select(distinct(c)).where(and_(c.is_not(None), c != "")).order_by(c)
    with connect() as conn:
        return [r[0] for r in conn.execute(stmt)]


def distinct_categories() -> list[str]:
    return distinct_col("category")


def _prospect_condition():
    from leadgen.score import SOCIAL, AGGREGATORS
    weak = [LEADS.c.website.is_(None), LEADS.c.website == "",
            LEADS.c.website.ilike("http://%")]
    for token in (*SOCIAL, *AGGREGATORS):
        weak.append(LEADS.c.website.ilike(f"%{token}%"))
    return or_(*weak)


def prospect_count() -> int:
    with connect() as conn:
        return conn.execute(select(func.count()).where(_prospect_condition())).scalar_one()


def status_counts() -> dict:
    stmt = select(LEADS.c.email_status, func.count()).group_by(LEADS.c.email_status)
    with connect() as conn:
        return {r[0]: r[1] for r in conn.execute(stmt)}


def stats() -> dict:
    has_email = case((and_(LEADS.c.email.is_not(None), LEADS.c.email != ""), 1), else_=0)
    has_site = case((and_(LEADS.c.website.is_not(None), LEADS.c.website != ""), 1), else_=0)
    stmt = select(func.count(), func.coalesce(func.sum(has_email), 0),
                  func.coalesce(func.sum(has_site), 0))
    with connect() as conn:
        total, with_email, with_site = conn.execute(stmt).first()
    return {"total": total, "with_email": int(with_email), "with_website": int(with_site)}


def export_csv(out_path: Path, only_with_email: bool = True, dedupe_email: bool = True) -> int:
    """Dump leads to CSV (dedupe by email). Returns rows written."""
    cols = ["name", "email", "phone", "website", "address", "category", "rating", "email_status"]
    stmt = select(*[getattr(LEADS.c, c) for c in cols])
    if only_with_email:
        stmt = stmt.where(and_(LEADS.c.email.is_not(None), LEADS.c.email != ""))
    stmt = stmt.order_by(LEADS.c.name)
    with connect() as conn:
        rows = conn.execute(stmt).mappings().all()
    seen, out = set(), []
    for r in rows:
        key = (r["email"] or "").lower()
        if dedupe_email and key and key in seen:
            continue
        seen.add(key)
        out.append(r)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["name", "email", "phone", "website", "address", "category", "rating", "status"])
        for r in out:
            w.writerow([r[c] for c in cols])
    return len(out)


# One-off cleanup of leads collected before scrapes were scoped to exact borders.
drop_cross_border()
