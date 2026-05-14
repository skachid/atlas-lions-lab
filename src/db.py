"""Database connection helpers."""
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "soccer.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema() -> None:
    schema_file = PROJECT_ROOT / "src" / "schema.sql"
    schema_sql = schema_file.read_text()
    with get_connection() as conn:
        conn.executescript(schema_sql)
    print(f"Schema initialized at {DB_PATH}")
