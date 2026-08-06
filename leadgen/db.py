"""SQLite storage for scraped leads.

One table `leads`, keyed by a normalized business identity so re-running the
scraper is idempotent (no duplicate rows for the same business).
"""
from __future__ import annotations

import sqlite3
import csv
from pathlib import Path
from contextlib import contextmanager

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "leads.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT,
    website     TEXT,
    email       TEXT,
    phone       TEXT,
    address     TEXT,
    category    TEXT,
    rating      TEXT,
    query       TEXT,               -- the search that found this lead
    maps_url    TEXT UNIQUE,        -- stable identity for dedup (OSM url)
    email_status TEXT DEFAULT 'new',-- new | contacted | called | replied | bounced | skip
    lat         REAL,
    lon         REAL,
    country     TEXT,
    region      TEXT,               -- oblast / voivodeship / state
    city        TEXT,
    notes       TEXT,               -- free notes (e.g. cold-call outcome)
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_leads_email ON leads(email);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(email_status);
"""

# Indexes on columns that may be added by migration — created after _migrate.
POST_MIGRATE = "CREATE INDEX IF NOT EXISTS idx_leads_country ON leads(country);"

# Columns added after the first release — backfilled onto existing DBs.
_ADDED_COLUMNS = {
    "lat": "REAL", "lon": "REAL", "country": "TEXT", "region": "TEXT",
    "city": "TEXT", "notes": "TEXT",
    "site_ok": "INTEGER", "mobile": "INTEGER",  # live site signals (1/0/NULL)
}


def _migrate(conn: sqlite3.Connection) -> None:
    have = {r[1] for r in conn.execute("PRAGMA table_info(leads)").fetchall()}
    for col, decl in _ADDED_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE leads ADD COLUMN {col} {decl}")


@contextmanager
def connect(db_path: Path = DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.executescript(POST_MIGRATE)
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_lead(conn: sqlite3.Connection, lead: dict) -> bool:
    """Insert a lead, or fill in missing fields on an existing one.

    Returns True if a new row was inserted, False if it already existed.
    Identity is `maps_url` when present, otherwise (name, address).
    """
    cur = conn.execute(
        "SELECT id, email, website FROM leads WHERE maps_url = ? OR (maps_url IS NULL AND name = ? AND address = ?)",
        (lead.get("maps_url"), lead.get("name"), lead.get("address")),
    )
    row = cur.fetchone()
    if row is None:
        cols = ("name", "website", "email", "phone", "address", "category", "rating",
                "query", "maps_url", "lat", "lon", "country", "region", "city",
                "site_ok", "mobile")
        conn.execute(
            f"INSERT INTO leads ({', '.join(cols)}) VALUES ({', '.join(':' + c for c in cols)})",
            {c: lead.get(c) for c in cols},
        )
        return True
    # Backfill email/website/signals if we learned them this run.
    updates, params = [], []
    if not row["email"] and lead.get("email"):
        updates.append("email = ?"); params.append(lead["email"])
    if not row["website"] and lead.get("website"):
        updates.append("website = ?"); params.append(lead["website"])
    for sig in ("site_ok", "mobile"):
        if lead.get(sig) is not None:
            updates.append(f"{sig} = ?"); params.append(lead[sig])
    if updates:
        params.append(row["id"])
        conn.execute(f"UPDATE leads SET {', '.join(updates)} WHERE id = ?", params)
    return False


def export_csv(out_path: Path, only_with_email: bool = True, dedupe_email: bool = True,
               db_path: Path = DB_PATH) -> int:
    """Dump leads to CSV. Returns number of rows written.

    dedupe_email collapses rows that share an email (e.g. chain branches) to one.
    """
    with connect(db_path) as conn:
        q = "SELECT name, email, phone, website, address, category, rating, email_status FROM leads"
        if only_with_email:
            q += " WHERE email IS NOT NULL AND email != ''"
        if dedupe_email:
            q += " GROUP BY LOWER(email)" if only_with_email else \
                 " GROUP BY COALESCE(NULLIF(LOWER(email),''), maps_url)"
        q += " ORDER BY name"
        rows = conn.execute(q).fetchall()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "email", "phone", "website", "address", "category", "rating", "status"])
        for r in rows:
            writer.writerow([r[k] for k in r.keys()])
    return len(rows)


VALID_STATUSES = ("new", "contacted", "called", "replied", "bounced", "skip")


def query_leads(search: str = "", category: str = "", status: str = "",
                has_email: str = "", country: str = "", region: str = "",
                limit: int = 1000, db_path: Path = DB_PATH) -> list[dict]:
    """Filtered lead list for the web panel."""
    where, params = [], []
    if search:
        where.append("(name LIKE ? OR email LIKE ? OR address LIKE ?)")
        params += [f"%{search}%"] * 3
    if category:
        where.append("category = ?"); params.append(category)
    if status:
        where.append("email_status = ?"); params.append(status)
    if country:
        where.append("country = ?"); params.append(country)
    if region:
        where.append("region = ?"); params.append(region)
    if has_email == "yes":
        where.append("email IS NOT NULL AND email != ''")
    elif has_email == "no":
        where.append("(email IS NULL OR email = '')")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT * FROM leads{clause} ORDER BY (email IS NULL OR email=''), name LIMIT ?",
            params + [limit],
        ).fetchall()
    return [dict(r) for r in rows]


def set_status_bulk(ids: list[int], status: str, db_path: Path = DB_PATH) -> int:
    if status not in VALID_STATUSES or not ids:
        return 0
    with connect(db_path) as conn:
        conn.executemany("UPDATE leads SET email_status = ? WHERE id = ?",
                         [(status, i) for i in ids])
    return len(ids)


def distinct_col(col: str, db_path: Path = DB_PATH) -> list[str]:
    if col not in ("country", "region", "city", "category"):
        return []
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT DISTINCT {col} FROM leads WHERE {col} IS NOT NULL AND {col} != '' ORDER BY {col}"
        ).fetchall()
    return [r[0] for r in rows]


def set_status(lead_id: int, status: str, db_path: Path = DB_PATH) -> bool:
    if status not in VALID_STATUSES:
        return False
    with connect(db_path) as conn:
        conn.execute("UPDATE leads SET email_status = ? WHERE id = ?", (status, lead_id))
    return True


def distinct_categories(db_path: Path = DB_PATH) -> list[str]:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT category FROM leads WHERE category != '' ORDER BY category"
        ).fetchall()
    return [r[0] for r in rows]


def prospect_count(db_path: Path = DB_PATH) -> int:
    """Leads with a missing or weak website (the best sales prospects)."""
    from leadgen.score import PROSPECT_SQL
    with connect(db_path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM leads WHERE {PROSPECT_SQL}").fetchone()[0]


def status_counts(db_path: Path = DB_PATH) -> dict:
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT email_status, COUNT(*) c FROM leads GROUP BY email_status"
        ).fetchall()
    return {r[0]: r[1] for r in rows}


def stats(db_path: Path = DB_PATH) -> dict:
    with connect(db_path) as conn:
        total = conn.execute("SELECT COUNT(*) FROM leads").fetchone()[0]
        with_email = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE email IS NOT NULL AND email != ''").fetchone()[0]
        with_site = conn.execute(
            "SELECT COUNT(*) FROM leads WHERE website IS NOT NULL AND website != ''").fetchone()[0]
    return {"total": total, "with_email": with_email, "with_website": with_site}
