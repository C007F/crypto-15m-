from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    ticker TEXT,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_kind_ts ON events(kind, ts);
"""


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def log(self, kind: str, payload: dict, ticker: str | None = None) -> None:
        def _default(o):
            if isinstance(o, Decimal):
                return str(o)
            if isinstance(o, datetime):
                return o.isoformat()
            return str(o)

        self.conn.execute(
            "INSERT INTO events (ts, kind, ticker, payload) VALUES (?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                kind,
                ticker,
                json.dumps(payload, default=_default),
            ),
        )
        self.conn.commit()

    def recent(self, limit: int = 30) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)))
