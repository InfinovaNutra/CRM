import json
import os
from datetime import datetime

DATABASE_URL = os.environ.get("DATABASE_URL", "")

# Determine database type
USE_POSTGRES = DATABASE_URL.startswith("postgres")


def get_db():
    if USE_POSTGRES:
        import psycopg2
        import psycopg2.extras
        # Railway sometimes gives postgres:// but psycopg2 needs postgresql://
        url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        conn = psycopg2.connect(url)
        conn.autocommit = False
        return conn
    else:
        import sqlite3
        db_path = os.path.join(os.path.dirname(__file__), "crm_data.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn


def _fetchone(conn, query, params=()):
    if USE_POSTGRES:
        import psycopg2.extras
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, params)
        row = cur.fetchone()
        cur.close()
        return row
    else:
        return conn.execute(query, params).fetchone()


def _fetchall(conn, query, params=()):
    if USE_POSTGRES:
        import psycopg2.extras
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(query, params)
        rows = cur.fetchall()
        cur.close()
        return rows
    else:
        return conn.execute(query, params).fetchall()


def _execute(conn, query, params=()):
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute(query, params)
        return cur
    else:
        return conn.execute(query, params)


def _placeholder():
    return "%s" if USE_POSTGRES else "?"


def init_db():
    conn = get_db()
    if USE_POSTGRES:
        cur = conn.cursor()
        cur.execute("""
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
        """)
        cur.execute("""
        INSERT INTO profile (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id SERIAL PRIMARY KEY,
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
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            id SERIAL PRIMARY KEY,
            lead_id INTEGER REFERENCES leads(id) ON DELETE SET NULL,
            ingredient TEXT NOT NULL,
            quantity_kg REAL DEFAULT 0,
            price_per_kg REAL DEFAULT 0,
            total_value REAL DEFAULT 0,
            status TEXT DEFAULT 'draft',
            notes TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT NOW(),
            updated_at TIMESTAMP DEFAULT NOW()
        );
        """)
        cur.close()
        conn.commit()
    else:
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


def _row_to_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return row
    return dict(row)


# --- Profile ---
def get_profile():
    conn = get_db()
    ph = _placeholder()
    row = _fetchone(conn, f"SELECT * FROM profile WHERE id={ph}", (1,))
    conn.close()
    return _row_to_dict(row) or {}


def update_profile(**kwargs):
    conn = get_db()
    ph = _placeholder()
    fields = ", ".join(f"{k}={ph}" for k in kwargs)
    values = list(kwargs.values())
    _execute(conn, f"UPDATE profile SET {fields} WHERE id={ph}", values + [1])
    conn.commit()
    conn.close()


# --- Leads ---
def create_lead(**kwargs):
    conn = get_db()
    ph = _placeholder()
    if "email_sequence" in kwargs and isinstance(kwargs["email_sequence"], list):
        kwargs["email_sequence"] = json.dumps(kwargs["email_sequence"])
    if "linkedin_messages" in kwargs and isinstance(kwargs["linkedin_messages"], list):
        kwargs["linkedin_messages"] = json.dumps(kwargs["linkedin_messages"])
    fields = ", ".join(kwargs.keys())
    placeholders = ", ".join(ph for _ in kwargs)
    if USE_POSTGRES:
        cur = _execute(
            conn,
            f"INSERT INTO leads ({fields}) VALUES ({placeholders}) RETURNING id",
            list(kwargs.values()),
        )
        lead_id = cur.fetchone()[0]
        cur.close()
    else:
        cur = _execute(
            conn,
            f"INSERT INTO leads ({fields}) VALUES ({placeholders})",
            list(kwargs.values()),
        )
        lead_id = cur.lastrowid
    conn.commit()
    conn.close()
    return lead_id


def get_leads():
    conn = get_db()
    rows = _fetchall(conn, "SELECT * FROM leads ORDER BY created_at DESC")
    conn.close()
    results = []
    for r in rows:
        d = _row_to_dict(r)
        d["email_sequence"] = json.loads(d.get("email_sequence") or "[]")
        d["linkedin_messages"] = json.loads(d.get("linkedin_messages") or "[]")
        results.append(d)
    return results


def get_lead(lead_id):
    conn = get_db()
    ph = _placeholder()
    row = _fetchone(conn, f"SELECT * FROM leads WHERE id={ph}", (lead_id,))
    conn.close()
    if not row:
        return None
    d = _row_to_dict(row)
    d["email_sequence"] = json.loads(d.get("email_sequence") or "[]")
    d["linkedin_messages"] = json.loads(d.get("linkedin_messages") or "[]")
    return d


def update_lead(lead_id, **kwargs):
    conn = get_db()
    ph = _placeholder()
    if "email_sequence" in kwargs and isinstance(kwargs["email_sequence"], list):
        kwargs["email_sequence"] = json.dumps(kwargs["email_sequence"])
    if "linkedin_messages" in kwargs and isinstance(kwargs["linkedin_messages"], list):
        kwargs["linkedin_messages"] = json.dumps(kwargs["linkedin_messages"])
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    fields = ", ".join(f"{k}={ph}" for k in kwargs)
    values = list(kwargs.values()) + [lead_id]
    _execute(conn, f"UPDATE leads SET {fields} WHERE id={ph}", values)
    conn.commit()
    conn.close()


def delete_lead(lead_id):
    conn = get_db()
    ph = _placeholder()
    _execute(conn, f"DELETE FROM leads WHERE id={ph}", (lead_id,))
    conn.commit()
    conn.close()


# --- Quotes ---
def create_quote(**kwargs):
    conn = get_db()
    ph = _placeholder()
    kwargs["total_value"] = round(
        kwargs.get("quantity_kg", 0) * kwargs.get("price_per_kg", 0), 2
    )
    fields = ", ".join(kwargs.keys())
    placeholders = ", ".join(ph for _ in kwargs)
    if USE_POSTGRES:
        cur = _execute(
            conn,
            f"INSERT INTO quotes ({fields}) VALUES ({placeholders}) RETURNING id",
            list(kwargs.values()),
        )
        qid = cur.fetchone()[0]
        cur.close()
    else:
        cur = _execute(
            conn,
            f"INSERT INTO quotes ({fields}) VALUES ({placeholders})",
            list(kwargs.values()),
        )
        qid = cur.lastrowid
    conn.commit()
    conn.close()
    return qid


def get_quotes():
    conn = get_db()
    rows = _fetchall(conn, """
        SELECT q.*, l.company_name as lead_company_name
        FROM quotes q
        LEFT JOIN leads l ON q.lead_id = l.id
        ORDER BY q.created_at DESC
    """)
    conn.close()
    return [_row_to_dict(r) for r in rows]


def get_quote(qid):
    conn = get_db()
    ph = _placeholder()
    row = _fetchone(conn, f"SELECT * FROM quotes WHERE id={ph}", (qid,))
    conn.close()
    return _row_to_dict(row)


def update_quote(qid, **kwargs):
    conn = get_db()
    ph = _placeholder()
    if "quantity_kg" in kwargs or "price_per_kg" in kwargs:
        existing = get_quote(qid) or {}
        qty = kwargs.get("quantity_kg", existing.get("quantity_kg", 0))
        ppk = kwargs.get("price_per_kg", existing.get("price_per_kg", 0))
        kwargs["total_value"] = round(qty * ppk, 2)
    kwargs["updated_at"] = datetime.utcnow().isoformat()
    fields = ", ".join(f"{k}={ph}" for k in kwargs)
    values = list(kwargs.values()) + [qid]
    _execute(conn, f"UPDATE quotes SET {fields} WHERE id={ph}", values)
    conn.commit()
    conn.close()


def delete_quote(qid):
    conn = get_db()
    ph = _placeholder()
    _execute(conn, f"DELETE FROM quotes WHERE id={ph}", (qid,))
    conn.commit()
    conn.close()


def get_quote_stats():
    conn = get_db()
    stats = {}
    row = _fetchone(conn, """
        SELECT
            COUNT(*) as total_quotes,
            COALESCE(SUM(total_value), 0) as total_value,
            COALESCE(AVG(price_per_kg), 0) as avg_price_per_kg,
            COALESCE(SUM(quantity_kg), 0) as total_quantity_kg
        FROM quotes
    """)
    d = _row_to_dict(row)
    stats["total_quotes"] = d["total_quotes"]
    stats["total_value"] = round(float(d["total_value"]), 2)
    stats["avg_price_per_kg"] = round(float(d["avg_price_per_kg"]), 2)
    stats["total_quantity_kg"] = round(float(d["total_quantity_kg"]), 2)

    status_rows = _fetchall(conn, """
        SELECT status, COUNT(*) as cnt, COALESCE(SUM(total_value), 0) as val
        FROM quotes GROUP BY status
    """)
    stats["by_status"] = {
        _row_to_dict(r)["status"]: {
            "count": _row_to_dict(r)["cnt"],
            "value": round(float(_row_to_dict(r)["val"]), 2)
        }
        for r in status_rows
    }
    conn.close()
    return stats


def get_pipeline_stats():
    conn = get_db()
    rows = _fetchall(conn, """
        SELECT pipeline_status, COUNT(*) as cnt
        FROM leads GROUP BY pipeline_status
    """)
    conn.close()
    return {_row_to_dict(r)["pipeline_status"]: _row_to_dict(r)["cnt"] for r in rows}
