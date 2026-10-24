"""SQLite store: remembers what was seen/notified so you only hear about new deals."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .models import Listing

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
CREATE TABLE IF NOT EXISTS podium (
    search TEXT PRIMARY KEY,
    ranking TEXT NOT NULL,  -- JSON list of {"id", "price", "title"}, best first
    updated REAL
);
"""
# Added after the first release; existing databases get them on open.
COLUMNS = {"total_price": "REAL", "brand": "TEXT", "condition": "TEXT", "gone": "REAL"}


@dataclass
class Previous:
    price: float
    description: str
    notified_price: float | None


@dataclass
class Placed:
    id: int
    price: float
    title: str


def listing_from_row(row: sqlite3.Row) -> Listing:
    """Rebuild a stored listing so it can be scored again."""
    total = row["total_price"]
    if total is None:
        # Stored before total_price was kept: estimate Vinted's buyer protection fee
        # (0.70 + 5 %) so these don't look cheaper than freshly scraped items.
        total = round(row["price"] * 1.05 + 0.7, 2)
    return Listing(
        id=row["id"], title=row["title"] or "", price=row["price"], currency=row["currency"] or "EUR",
        url=row["url"] or "", total_price=total, brand=row["brand"] or "",
        condition=row["condition"] or "", photo=row["photo"] or "", description=row["description"] or "",
    )


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        have = {row["name"] for row in self.db.execute("PRAGMA table_info(items)")}
        for name, kind in COLUMNS.items():
            if name not in have:
                self.db.execute(f"ALTER TABLE items ADD COLUMN {name} {kind}")

    def previous(self, search: str, item_id: int) -> Previous | None:
        row = self.db.execute(
            "SELECT price, description, notified_price FROM items WHERE search=? AND id=?", (search, item_id)
        ).fetchone()
        return Previous(row["price"], row["description"] or "", row["notified_price"]) if row else None

    def upsert(self, search: str, listing, score) -> None:
        now = time.time()
        # Showing up in search results means it's for sale, so `gone` is cleared.
        self.db.execute(
            """INSERT INTO items (search, id, title, price, currency, cost, points, value, summary, url, photo,
                                  description, reject, first_seen, last_seen, total_price, brand, condition)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (search, id) DO UPDATE SET
                 title=excluded.title, price=excluded.price, cost=excluded.cost, points=excluded.points,
                 value=excluded.value, summary=excluded.summary, photo=excluded.photo,
                 description=COALESCE(NULLIF(excluded.description, ''), items.description),
                 reject=excluded.reject, last_seen=excluded.last_seen, total_price=excluded.total_price,
                 brand=excluded.brand, condition=excluded.condition, gone=NULL""",
            (search, listing.id, listing.title, listing.price, listing.currency, score.cost, score.points,
             score.value, score.summary, listing.url, listing.photo, listing.description, score.reject, now, now,
             listing.total_price, listing.brand, listing.condition),
        )

    def rows(self, search: str, max_age_hours: float | None = None) -> list[sqlite3.Row]:
        """Stored listings that may still be for sale."""
        sql = "SELECT * FROM items WHERE search=? AND gone IS NULL"
        args: list = [search]
        if max_age_hours:
            sql += " AND last_seen>=?"
            args.append(time.time() - max_age_hours * 3600)
        return self.db.execute(sql, args).fetchall()

    def listings(self, search: str, max_age_hours: float | None = None) -> list[Listing]:
        return [listing_from_row(row) for row in self.rows(search, max_age_hours)]

    def update_score(self, search: str, item_id: int, score) -> None:
        self.db.execute(
            "UPDATE items SET cost=?, points=?, value=?, summary=?, reject=? WHERE search=? AND id=?",
            (score.cost, score.points, score.value, score.summary, score.reject, search, item_id),
        )

    def mark_gone(self, search: str, item_id: int) -> None:
        self.db.execute("UPDATE items SET gone=? WHERE search=? AND id=?", (time.time(), search, item_id))

    def mark_seen(self, search: str, item_id: int) -> None:
        self.db.execute("UPDATE items SET last_seen=? WHERE search=? AND id=?", (time.time(), search, item_id))

    def mark_notified(self, search: str, item_id: int, price: float) -> None:
        self.db.execute("UPDATE items SET notified_price=? WHERE search=? AND id=?", (price, search, item_id))

    def podium(self, search: str) -> list[Placed]:
        row = self.db.execute("SELECT ranking FROM podium WHERE search=?", (search,)).fetchone()
        return [Placed(**p) for p in json.loads(row["ranking"])] if row else []

    def save_podium(self, search: str, ranking: list[Placed]) -> None:
        self.db.execute(
            "INSERT INTO podium (search, ranking, updated) VALUES (?,?,?) "
            "ON CONFLICT (search) DO UPDATE SET ranking=excluded.ranking, updated=excluded.updated",
            (search, json.dumps([p.__dict__ for p in ranking], ensure_ascii=False), time.time()),
        )

    def commit(self) -> None:
        self.db.commit()

    def top(self, search: str | None = None, limit: int = 20, max_age_hours: float | None = 72,
            min_value: float | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM items WHERE reject IS NULL AND gone IS NULL"
        args: list = []
        if search:
            sql += " AND search=?"
            args.append(search)
        if max_age_hours:
            sql += " AND last_seen>=?"
            args.append(time.time() - max_age_hours * 3600)
        if min_value is not None:
            sql += " AND value>=?"
            args.append(min_value)
        sql += " ORDER BY value DESC, price ASC LIMIT ?"
        args.append(limit)
        return self.db.execute(sql, args).fetchall()
