"""Small, transactional SQLite migration runner for StudentFlow."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
MIGRATION_NAME = re.compile(r"\d{4}_[a-z0-9_]+\Z")


def migrate_database(database_path: Path) -> list[str]:
    """Apply unapplied numbered SQL migrations exactly once; safe to call at startup."""
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    applied: list[str] = []
    migrations = sorted(MIGRATIONS_DIR.glob("*.sql"))

    with sqlite3.connect(database_path, timeout=20) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()

        for migration in migrations:
            version = migration.stem
            if not MIGRATION_NAME.fullmatch(version):
                raise ValueError(f"Invalid migration filename: {migration.name}")
            try:
                conn.execute("BEGIN IMMEDIATE")
                exists = conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
                ).fetchone()
                if exists:
                    conn.commit()
                    continue
                sql = migration.read_text(encoding="utf-8")
                statement = ""
                for line in sql.splitlines():
                    statement += line + "\n"
                    if sqlite3.complete_statement(statement):
                        if statement.strip():
                            conn.execute(statement)
                        statement = ""
                if statement.strip():
                    raise sqlite3.OperationalError(f"Incomplete SQL in migration {migration.name}")
                conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            applied.append(version)

        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise sqlite3.IntegrityError(f"Foreign key check failed after migrations: {violations[:3]}")
    return applied
