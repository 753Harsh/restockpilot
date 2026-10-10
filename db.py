"""Saves purchase orders and run logs.

Uses PostgreSQL when DATABASE_URL is set (course sessions 19-20: AI agent and Postgres),
otherwise a local SQLite file so the demo works anywhere.
"""
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
SQLITE_FILE = Path(__file__).parent / "output" / "restockpilot.db"

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS purchase_orders (
        po_id TEXT PRIMARY KEY, case_id TEXT, created_at TEXT, sku TEXT, sku_name TEXT,
        supplier_id TEXT, supplier_name TEXT, qty INTEGER, unit_cost REAL, value REAL,
        priority TEXT, decision TEXT, decided_by TEXT, reasons TEXT)""",
    """CREATE TABLE IF NOT EXISTS run_log (
        run_id TEXT, case_id TEXT, created_at TEXT, mode TEXT, step TEXT, detail TEXT)""",
]


def _pg():
    url = os.getenv("DATABASE_URL", "")
    if url.startswith("postgres"):
        import psycopg2
        return psycopg2.connect(url)
    return None


def connect():
    conn = _pg()
    if conn is None:
        SQLITE_FILE.parent.mkdir(exist_ok=True)
        conn = sqlite3.connect(SQLITE_FILE)
    cur = conn.cursor()
    for s in SCHEMA:
        cur.execute(s)
    conn.commit()
    return conn


def backend():
    return "PostgreSQL" if os.getenv("DATABASE_URL", "").startswith("postgres") else "SQLite"


def _ph(conn):
    return "%s" if conn.__class__.__module__.startswith("psycopg2") else "?"


def save_po(row: dict):
    conn = connect()
    p = _ph(conn)
    cols = ["po_id", "case_id", "created_at", "sku", "sku_name", "supplier_id", "supplier_name",
            "qty", "unit_cost", "value", "priority", "decision", "decided_by", "reasons"]
    vals = [row.get(c) for c in cols]
    vals[cols.index("reasons")] = json.dumps(row.get("reasons", []))
    conn.cursor().execute(
        f"INSERT INTO purchase_orders ({','.join(cols)}) VALUES ({','.join([p]*len(cols))})", vals)
    conn.commit()
    conn.close()


def log(run_id, case_id, mode, step, detail):
    try:
        conn = connect()
        p = _ph(conn)
        conn.cursor().execute(f"INSERT INTO run_log VALUES ({','.join([p]*6)})",
                              (run_id, case_id, datetime.now().isoformat(timespec="seconds"),
                               mode, step, str(detail)[:2000]))
        conn.commit()
        conn.close()
    except Exception:
        pass        # logging must never stop the workflow


def recent_pos(limit=50):
    conn = connect()
    cur = conn.cursor()
    cur.execute(f"SELECT po_id, case_id, created_at, sku_name, supplier_name, qty, value, priority, "
                f"decision, decided_by FROM purchase_orders ORDER BY created_at DESC LIMIT {int(limit)}")
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    conn.close()
    return rows
