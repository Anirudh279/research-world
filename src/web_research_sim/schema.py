"""Transactional workspace metadata upgrades; frozen objects are never rewritten."""
import sqlite3
from datetime import datetime, timezone
from .core import SimError

CURRENT_VERSION = '2'
SCHEMA = '\n                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);\n                CREATE TABLE IF NOT EXISTS worlds(id TEXT PRIMARY KEY, question TEXT NOT NULL,\n                    created_at TEXT NOT NULL, budgets TEXT NOT NULL);\n                CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, world_id TEXT NOT NULL,\n                    kind TEXT NOT NULL, operation_key TEXT NOT NULL, request_hash TEXT NOT NULL,\n                    started_at TEXT NOT NULL, status TEXT NOT NULL, result TEXT,\n                    UNIQUE(world_id,operation_key));\n                CREATE TABLE IF NOT EXISTS captures(id TEXT PRIMARY KEY,world_id TEXT NOT NULL,\n                    url TEXT NOT NULL,captured_at TEXT NOT NULL,archived_at TEXT NOT NULL,record_hash TEXT NOT NULL);\n                CREATE TABLE IF NOT EXISTS snapshots(id TEXT PRIMARY KEY,world_id TEXT NOT NULL,\n                    cutoff TEXT NOT NULL,count INTEGER NOT NULL);\n                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,world_id TEXT NOT NULL,\n                    snapshot_id TEXT NOT NULL,data TEXT NOT NULL);\n                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT,\n                    world_id TEXT NOT NULL,at TEXT NOT NULL,kind TEXT NOT NULL,data TEXT NOT NULL);\n                CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,world_id TEXT NOT NULL,\n                    kind TEXT NOT NULL,status TEXT NOT NULL,result TEXT,error TEXT);\n                CREATE TABLE IF NOT EXISTS original_responses(id TEXT PRIMARY KEY,world_id TEXT NOT NULL,\n                    body_hash TEXT NOT NULL,metadata TEXT NOT NULL,archived_at TEXT NOT NULL);\n            '


def initialize_database(path):
    db = sqlite3.connect(path, timeout=15, isolation_level=None)
    try:
        db.execute('BEGIN IMMEDIATE')
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        previous = None
        if tables:
            if 'metadata' not in tables:
                raise SimError('schema_version', 'Unrecognized database; use a new workspace.')
            row = db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
            previous = row[0] if row else None
            if previous not in ('1', CURRENT_VERSION):
                raise SimError('schema_version', 'Unsupported workspace schema; no migration was performed.')
        # Individual DDL statements participate in this transaction. executescript
        # would implicitly commit and break atomic upgrade/rollback.
        for statement in SCHEMA.split(';'):
            if statement.strip(): db.execute(statement)
        db.execute('CREATE TABLE IF NOT EXISTS schema_migrations(from_version TEXT, to_version TEXT NOT NULL, applied_at TEXT NOT NULL)')
        if previous != CURRENT_VERSION:
            db.execute("INSERT OR REPLACE INTO metadata VALUES('schema_version',?)", (CURRENT_VERSION,))
            db.execute('INSERT INTO schema_migrations VALUES(?,?,?)', (previous, CURRENT_VERSION, datetime.now(timezone.utc).isoformat()))
        db.commit()
        db.execute('PRAGMA journal_mode=WAL')
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()
