"""SQLite store: remembers what was seen/notified so you only hear about new deals."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    search TEXT NOT NULL,
    id INTEGER NOT NULL,
    title TEXT,
    price REAL,
    currency TEXT,
    cost REAL,
    points REAL,
    value REAL,
    summary TEXT,
    url TEXT,
    photo TEXT,
    description TEXT,
    reject TEXT,
    first_seen REAL,
    last_seen REAL,
    notified_price REAL,
    PRIMARY KEY (search, id)
);
CREATE INDEX IF NOT EXISTS items_value ON items (search, value DESC);
"""


@dataclass
class Previous:
    price: float
    description: str
    notified_price: float | None


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def previous(self, search: str, item_id: int) -> Previous | None:
        row = self.db.execute(
            "SELECT price, description, notified_price FROM items WHERE search=? AND id=?", (search, item_id)
        ).fetchone()
        return Previous(row["price"], row["description"] or "", row["notified_price"]) if row else None

    def upsert(self, search: str, listing, score) -> None:
        now = time.time()
        self.db.execute(
            """INSERT INTO items (search, id, title, price, currency, cost, points, value, summary, url, photo,
                                  description, reject, first_seen, last_seen)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (search, id) DO UPDATE SET
                 title=excluded.title, price=excluded.price, cost=excluded.cost, points=excluded.points,
                 value=excluded.value, summary=excluded.summary, photo=excluded.photo,
                 description=COALESCE(NULLIF(excluded.description, ''), items.description),
                 reject=excluded.reject, last_seen=excluded.last_seen""",
            (search, listing.id, listing.title, listing.price, listing.currency, score.cost, score.points,
             score.value, score.summary, listing.url, listing.photo, listing.description, score.reject, now, now),
        )

    def mark_notified(self, search: str, item_id: int, price: float) -> None:
        self.db.execute("UPDATE items SET notified_price=? WHERE search=? AND id=?", (price, search, item_id))

    def commit(self) -> None:
        self.db.commit()

    def top(self, search: str | None = None, limit: int = 20, max_age_hours: float | None = 72) -> list[sqlite3.Row]:
        sql = "SELECT * FROM items WHERE reject IS NULL"
        args: list = []
        if search:
            sql += " AND search=?"
            args.append(search)
        if max_age_hours:
            sql += " AND last_seen>=?"
            args.append(time.time() - max_age_hours * 3600)
        sql += " ORDER BY value DESC LIMIT ?"
        args.append(limit)
        return self.db.execute(sql, args).fetchall()
