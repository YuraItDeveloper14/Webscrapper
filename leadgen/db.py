"""Lead storage — one `leads` table, works on SQLite (local) and Postgres (prod).

Backend is chosen by the DATABASE_URL env var: when set (e.g. Render Postgres)
we use it, otherwise a local SQLite file. All queries go through SQLAlchemy Core
so the same code emits correct SQL for either dialect.
"""
from __future__ import annotations

import os
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
    Column("site_ok", Integer), Column("mobile", Integer),
    Column("created_at", DateTime, server_default=func.now()),
    Index("idx_leads_email", "email"),
    Index("idx_leads_status", "email_status"),
    Index("idx_leads_country", "country"),
)

VALID_STATUSES = ("new", "target", "called", "client", "no")
_LEAD_COLS = ("name", "website", "email", "phone", "address", "category", "rating",
              "query", "maps_url", "lat", "lon", "country", "region", "city",
              "site_ok", "mobile")

# Columns added to the SQLite file after its first release (Postgres is created fresh).
_ADDED_COLUMNS = {"lat": "REAL", "lon": "REAL", "country": "TEXT", "region": "TEXT",
                  "city": "TEXT", "notes": "TEXT", "site_ok": "INTEGER", "mobile": "INTEGER"}


def _migrate_sqlite(conn) -> None:
    have = {r[1] for r in conn.execute(text("PRAGMA table_info(leads)"))}
    for col, decl in _ADDED_COLUMNS.items():
        if col not in have:
            conn.execute(text(f"ALTER TABLE leads ADD COLUMN {col} {decl}"))


def init_db() -> None:
    _meta.create_all(ENGINE)
    if IS_SQLITE:
        with ENGINE.begin() as conn:
            _migrate_sqlite(conn)


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
    # Identity: same OSM url, OR same business name in the same city (dedups the
    # node+way duplicates OSM often has for one place).
    ident = [LEADS.c.maps_url == lead.get("maps_url")]
    nm, city = (lead.get("name") or "").strip(), (lead.get("city") or "").strip()
    if nm and city:
        ident.append(and_(func.lower(LEADS.c.name) == nm.lower(),
                          func.lower(LEADS.c.city) == city.lower()))
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


def query_leads(search: str = "", category: str = "", status: str = "",
                has_email: str = "", country: str = "", region: str = "",
                limit: int = 1000) -> list[dict]:
    conds = []
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
    if has_email == "yes":
        conds.append(and_(LEADS.c.email.is_not(None), LEADS.c.email != ""))
    elif has_email == "no":
        conds.append(or_(LEADS.c.email.is_(None), LEADS.c.email == ""))
    stmt = (select(LEADS).where(and_(*conds)) if conds else select(LEADS))
    stmt = stmt.order_by(case((or_(LEADS.c.email.is_(None), LEADS.c.email == ""), 1),
                              else_=0), LEADS.c.name).limit(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(stmt).mappings().all()]


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


def dedupe_existing() -> int:
    """Collapse rows sharing (name, city), keeping the most useful one.

    Preference: a marked/worked status > has email > has website > lowest id.
    Returns the number of rows removed.
    """
    from sqlalchemy import delete
    with connect() as conn:
        rows = conn.execute(select(
            LEADS.c.id, LEADS.c.name, LEADS.c.city, LEADS.c.email,
            LEADS.c.website, LEADS.c.email_status)).mappings().all()
        groups: dict[tuple, list] = {}
        for r in rows:
            nm = (r["name"] or "").strip().lower()
            city = (r["city"] or "").strip().lower()
            if not nm or not city:
                continue  # can't safely dedup without both
            groups.setdefault((nm, city), []).append(r)

        def score(r):
            return (0 if r["email_status"] in ("new", None) else 1,
                    1 if r["email"] else 0, 1 if r["website"] else 0, -r["id"])

        remove = []
        for members in groups.values():
            if len(members) < 2:
                continue
            keep = max(members, key=score)
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
