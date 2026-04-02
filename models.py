import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(__file__), "crm_data.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS profile (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        company_name TEXT DEFAULT '',
        contact_name TEXT DEFAULT '',
        email TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        website TEXT DEFAULT '',
        ingredients_offered TEXT DEFAULT '',
        tagline TEXT DEFAULT '',
        anthropic_api_key TEXT DEFAULT ''
    );
    INSERT OR IGNORE INTO profile (id) VALUES (1);

    CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        company_name TEXT NOT NULL,
        company_type TEXT DEFAULT '',
        website TEXT DEFAULT '',
        location TEXT DEFAULT '',
        description TEXT DEFAULT '',
        fit_score INTEGER DEFAULT 0,
        fit_reasoning TEXT DEFAULT '',
        contact_name TEXT DEFAULT '',
        contact_email TEXT DEFAULT '',
        contact_phone TEXT DEFAULT '',
        contact_linkedin TEXT DEFAULT '',
        contact_role TEXT DEFAULT '',
        email_sequence TEXT DEFAULT '[]',
        linkedin_messages TEXT DEFAULT '[]',
        pipeline_status TEXT DEFAULT 'new',
        notes TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS quotes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lead_id INTEGER,
        ingredient TEXT NOT NULL,
        quantity_kg REAL DEFAULT 0,
        price_per_kg REAL DEFAULT 0,
        total_value REAL DEFAULT 0,
        status TEXT DEFAULT 'draft',
        notes TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (lead_id) REFERENCES leads(id) ON DELETE SET NULL
    );
    """)
    conn.commit()
    conn.close()


# --- Profile ---
def get_profile():
    conn = get_db()
    row = conn.execute("SELECT * FROM profile WHERE id=1").fetchone()
    conn.close()
    return dict(row) if row else {}


def update_profile(**kwargs):
    conn = get_db()
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values())
    conn.execute(f"UPDATE profile SET {fields} WHERE id=1", values)
    conn.commit()
    conn.close()


# --- Leads ---
def create_lead(**kwargs):
    conn = get_db()
    if "email_sequence" in kwargs and isinstance(kwargs["email_sequence"], list):
        kwargs["email_sequence"] = json.dumps(kwargs["email_sequence"])
    if "linkedin_messages" in kwargs and isinstance(kwargs["linkedin_messages"], list):
        kwargs["linkedin_messages"] = json.dumps(kwargs["linkedin_messages"])
    fields = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    cur = conn.execute(
        f"INSERT INTO leads ({fields}) VALUES ({placeholders})",
        list(kwargs.values()),
    )
    lead_id = cur.lastrowid
    conn.commit()
    conn.close()
    return lead_id


def get_leads():
    conn = get_db()
    rows = conn.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    conn.close()
    results = []
    for r in rows:
        d = dict(r)
        d["email_sequence"] = json.loads(d.get("email_sequence") or "[]")
        d["linkedin_messages"] = json.loads(d.get("linkedin_messages") or "[]")
        results.append(d)
    return results


def get_lead(lead_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["email_sequence"] = json.loads(d.get("email_sequence") or "[]")
    d["linkedin_messages"] = json.loads(d.get("linkedin_messages") or "[]")
    return d


def update_lead(lead_id, **kwargs):
    conn = get_db()
    if "email_sequence" in kwargs and isinstance(kwargs["email_sequence"], list):
        kwargs["email_sequence"] = json.dumps(kwargs["email_sequence"])
    if "linkedin_messages" in kwargs and isinstance(kwargs["linkedin_messages"], list):
        kwargs["linkedin_messages"] = json.dumps(kwargs["linkedin_messages"])
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [lead_id]
    conn.execute(f"UPDATE leads SET {fields} WHERE id=?", values)
    conn.commit()
    conn.close()


def delete_lead(lead_id):
    conn = get_db()
    conn.execute("DELETE FROM leads WHERE id=?", (lead_id,))
    conn.commit()
    conn.close()


# --- Quotes ---
def create_quote(**kwargs):
    conn = get_db()
    kwargs["total_value"] = round(
        kwargs.get("quantity_kg", 0) * kwargs.get("price_per_kg", 0), 2
    )
    fields = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" for _ in kwargs)
    cur = conn.execute(
        f"INSERT INTO quotes ({fields}) VALUES ({placeholders})",
        list(kwargs.values()),
    )
    qid = cur.lastrowid
    conn.commit()
    conn.close()
    return qid


def get_quotes():
    conn = get_db()
    rows = conn.execute("""
        SELECT q.*, l.company_name as lead_company_name
        FROM quotes q
        LEFT JOIN leads l ON q.lead_id = l.id
        ORDER BY q.created_at DESC
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_quote(qid):
    conn = get_db()
    row = conn.execute("SELECT * FROM quotes WHERE id=?", (qid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def update_quote(qid, **kwargs):
    conn = get_db()
    if "quantity_kg" in kwargs or "price_per_kg" in kwargs:
        existing = get_quote(qid) or {}
        qty = kwargs.get("quantity_kg", existing.get("quantity_kg", 0))
        ppk = kwargs.get("price_per_kg", existing.get("price_per_kg", 0))
        kwargs["total_value"] = round(qty * ppk, 2)
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    fields = ", ".join(f"{k}=?" for k in kwargs)
    values = list(kwargs.values()) + [qid]
    conn.execute(f"UPDATE quotes SET {fields} WHERE id=?", values)
    conn.commit()
    conn.close()


def delete_quote(qid):
    conn = get_db()
    conn.execute("DELETE FROM quotes WHERE id=?", (qid,))
    conn.commit()
    conn.close()


def get_quote_stats():
    conn = get_db()
    stats = {}
    row = conn.execute("""
        SELECT
            COUNT(*) as total_quotes,
            COALESCE(SUM(total_value), 0) as total_value,
            COALESCE(AVG(price_per_kg), 0) as avg_price_per_kg,
            COALESCE(SUM(quantity_kg), 0) as total_quantity_kg
        FROM quotes
    """).fetchone()
    stats["total_quotes"] = row["total_quotes"]
    stats["total_value"] = round(row["total_value"], 2)
    stats["avg_price_per_kg"] = round(row["avg_price_per_kg"], 2)
    stats["total_quantity_kg"] = round(row["total_quantity_kg"], 2)

    status_rows = conn.execute("""
        SELECT status, COUNT(*) as cnt, COALESCE(SUM(total_value), 0) as val
        FROM quotes GROUP BY status
    """).fetchall()
    stats["by_status"] = {r["status"]: {"count": r["cnt"], "value": round(r["val"], 2)} for r in status_rows}
    conn.close()
    return stats


def get_pipeline_stats():
    conn = get_db()
    rows = conn.execute("""
        SELECT pipeline_status, COUNT(*) as cnt
        FROM leads GROUP BY pipeline_status
    """).fetchall()
    conn.close()
    return {r["pipeline_status"]: r["cnt"] for r in rows}
