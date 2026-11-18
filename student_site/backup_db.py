"""Create a consistent SQLite backup, including databases using WAL mode.

Run: python backup_db.py [optional-output-file]
Restore only after stopping the app: replace ак.db with the verified backup.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("STUDENTFLOW_DB", str(Path(__file__).resolve().parent / "ак.db"))).resolve()
BACKUP_DIR = Path(__file__).resolve().parent / "backups"


def backup_database(source_path: Path, destination_path: Path) -> Path:
    source_path = source_path.resolve()
    destination_path = destination_path.resolve()
    if source_path == destination_path:
        raise ValueError("Backup destination must not overwrite the live database")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = destination_path.with_name(destination_path.name + ".tmp")
    try:
        with sqlite3.connect(source_path, timeout=20) as source, sqlite3.connect(temporary_path) as target:
            source.backup(target)
            check = target.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise sqlite3.DatabaseError(f"Backup integrity check failed: {check}")
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, destination_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()
    return destination_path


def prune_old_backups(directory: Path, keep_days: int = 30) -> int:
    if keep_days < 1:
        raise ValueError("Backup retention must be at least one day")
    cutoff = datetime.now(timezone.utc).timestamp() - keep_days * 24 * 60 * 60  # noqa: UP017 - keep Python 3.10 compatibility
    removed = 0
    if directory.exists():
        for candidate in directory.glob("studentflow-*.db"):
            if candidate.is_file() and candidate.stat().st_mtime < cutoff:
                candidate.unlink()
                removed += 1
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Create and verify a StudentFlow SQLite backup")
    parser.add_argument("output", nargs="?", type=Path, help="Output file; defaults to backups/ with UTC timestamp")
    parser.add_argument("--keep-days", type=int, default=30, help="Retention for timestamped backups (default: 30)")
    args = parser.parse_args()
    if args.keep_days < 1:
        parser.error("--keep-days must be at least 1")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")  # noqa: UP017 - keep Python 3.10 compatibility
    output = args.output or BACKUP_DIR / f"studentflow-{stamp}.db"
    saved = backup_database(DB_PATH, output)
    removed = prune_old_backups(BACKUP_DIR, args.keep_days) if args.output is None else 0
    print(f"Verified database backup: {saved}; expired backups removed: {removed}")


if __name__ == "__main__":
    main()
