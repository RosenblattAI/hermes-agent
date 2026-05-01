#!/usr/bin/env python3
"""Dream Log DB — SQLite store for dream log entries."""
import sqlite3, os

DB_PATH = os.environ.get("DREAM_LOG_DB", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "dashboard.db"))

def get_db():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("""
        CREATE TABLE IF NOT EXISTS dream_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            headline TEXT NOT NULL,
            permalink TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS news_radar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE NOT NULL,
            headline TEXT NOT NULL,
            permalink TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.commit()
    return db

def save_dream_log(date, headline, permalink=None):
    db = get_db()
    db.execute("""
        INSERT OR REPLACE INTO dream_logs (date, headline, permalink)
        VALUES (?, ?, ?)
    """, (date, headline, permalink))
    db.commit()
    db.close()

def get_latest_dream_log():
    db = get_db()
    row = db.execute(
        "SELECT * FROM dream_logs ORDER BY date DESC LIMIT 1"
    ).fetchone()
    db.close()
    if row:
        return {"date": row["date"], "headline": row["headline"], "permalink": row["permalink"]}
    return None

def get_dream_log_by_date(date):
    db = get_db()
    row = db.execute(
        "SELECT * FROM dream_logs WHERE date = ?", (date,)
    ).fetchone()
    db.close()
    if row:
        return {"date": row["date"], "headline": row["headline"], "permalink": row["permalink"]}
    return None

# --- News Radar ---

def save_news_radar(date, headline, permalink=None):
    db = get_db()
    db.execute("""
        INSERT OR REPLACE INTO news_radar (date, headline, permalink)
        VALUES (?, ?, ?)
    """, (date, headline, permalink))
    db.commit()
    db.close()

def get_latest_news_radar():
    db = get_db()
    row = db.execute(
        "SELECT * FROM news_radar ORDER BY date DESC LIMIT 1"
    ).fetchone()
    db.close()
    if row:
        return {"date": row["date"], "headline": row["headline"], "permalink": row["permalink"]}
    return None

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "latest":
        result = get_latest_dream_log()
        if result:
            import json
            print(json.dumps(result))
        else:
            print("No dream logs found")
    elif len(sys.argv) > 1 and sys.argv[1] == "init":
        get_db()
        print(f"DB initialized at {DB_PATH}")
    else:
        print("Usage: dream_log_db.py [latest|init]")
