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
CREATE TABLE IF NOT EXISTS crawled (
    search TEXT NOT NULL,
    query TEXT NOT NULL,  -- the query's params as JSON
    pages INTEGER NOT NULL,  -- how deep it was fetched in full once
    PRIMARY KEY (search, query)
);
CREATE TABLE IF NOT EXISTS runs (
    search TEXT PRIMARY KEY,
    last_run REAL NOT NULL  -- when the search last started, for per-search intervals
);
CREATE TABLE IF NOT EXISTS podium (
    search TEXT PRIMARY KEY,
    ranking TEXT NOT NULL,  -- JSON list of {"id", "price", "title"}, best first
    updated REAL
);
"""
# Added after the first release; existing databases get them on open.
COLUMNS = {"total_price": "REAL", "brand": "TEXT", "condition": "TEXT", "gone": "REAL", "extra": "TEXT",
           "partial": "INTEGER",  # 1: description is only the snippet from search results
           "shipping": "REAL"}  # the site's own shipping cost, NULL when unknown


@dataclass
class Previous:
    price: float
    description: str
    notified_price: float | None
    partial: bool = False  # the description is only a search snippet


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
        extra=row["extra"] or "", partial_description=bool(row["partial"]), shipping=row["shipping"],
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
            "SELECT price, notified_price FROM items WHERE search=? AND id=?", (search, item_id)
        ).fetchone()
        # A description fetched by any search serves them all: the same listing often turns up in several.
        best = self.db.execute(
            "SELECT description, partial FROM items WHERE id=? AND description != '' "
            "ORDER BY COALESCE(partial, 0), length(description) DESC LIMIT 1",
            (item_id,),
        ).fetchone()
        if not row and not best:
            return None
        return Previous(row["price"] if row else 0.0, best["description"] if best else "",
                        row["notified_price"] if row else None, bool(best and best["partial"]))

    def upsert(self, search: str, listing, score) -> None:
        now = time.time()
        # Showing up in search results means it's for sale, so `gone` is cleared.
        self.db.execute(
            """INSERT INTO items (search, id, title, price, currency, cost, points, value, summary, url, photo,
                                  description, reject, first_seen, last_seen, total_price, brand, condition, extra,
                                  partial, shipping)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT (search, id) DO UPDATE SET
                 title=excluded.title, price=excluded.price, cost=excluded.cost, points=excluded.points,
                 value=excluded.value, summary=excluded.summary, photo=excluded.photo,
                 description=CASE WHEN length(excluded.description) >= length(COALESCE(items.description, ''))
                                  THEN excluded.description ELSE items.description END,
                 partial=CASE WHEN length(excluded.description) >= length(COALESCE(items.description, ''))
                              THEN excluded.partial ELSE items.partial END,
                 reject=excluded.reject, last_seen=excluded.last_seen, total_price=excluded.total_price,
                 brand=excluded.brand, condition=excluded.condition, extra=excluded.extra,
                 shipping=excluded.shipping, gone=NULL""",
            (search, listing.id, listing.title, listing.price, listing.currency, score.cost, score.points,
             score.value, score.summary, listing.url, listing.photo, listing.description, score.reject, now, now,
             # Never NULL for new rows: NULL marks rows stored before the column existed.
             listing.total_price or listing.price, listing.brand, listing.condition, listing.extra,
             int(listing.partial_description), listing.shipping),
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

    def listing(self, search: str, item_id: int) -> Listing | None:
        row = self.db.execute("SELECT * FROM items WHERE search=? AND id=?", (search, item_id)).fetchone()
        return listing_from_row(row) if row else None

    def set_description(self, search: str, item_id: int, description: str) -> None:
        self.db.execute("UPDATE items SET description=?, partial=0 WHERE search=? AND id=?", (description, search, item_id))

    def set_price(self, search: str, item_id: int, price: float) -> None:
        """The listing page shows a new price. Vinted's buyer fee in `total_price` scales with it."""
        self.db.execute(
            "UPDATE items SET total_price=CASE WHEN price > 0 AND total_price IS NOT NULL "
            "THEN round(total_price * ? / price, 2) END, price=? WHERE search=? AND id=?",
            (price, price, search, item_id),
        )

    def update_score(self, search: str, item_id: int, score) -> None:
        self.db.execute(
            "UPDATE items SET cost=?, points=?, value=?, summary=?, reject=? WHERE search=? AND id=?",
            (score.cost, score.points, score.value, score.summary, score.reject, search, item_id),
        )

    def known(self, search: str, ids: list[int]) -> int:
        """How many of these listings were stored before."""
        marks = ",".join("?" * len(ids))
        return self.db.execute(f"SELECT COUNT(*) FROM items WHERE search=? AND id IN ({marks})",
                               (search, *ids)).fetchone()[0] if ids else 0

    def crawled_pages(self, search: str, query: str) -> int:
        row = self.db.execute("SELECT pages FROM crawled WHERE search=? AND query=?", (search, query)).fetchone()
        return row["pages"] if row else 0

    def set_crawled_pages(self, search: str, query: str, pages: int) -> None:
        self.db.execute("INSERT INTO crawled (search, query, pages) VALUES (?,?,?) "
                        "ON CONFLICT (search, query) DO UPDATE SET pages=excluded.pages", (search, query, pages))

    def last_run(self, search: str) -> float | None:
        row = self.db.execute("SELECT last_run FROM runs WHERE search=?", (search,)).fetchone()
        return row["last_run"] if row else None

    def set_last_run(self, search: str, when: float) -> None:
        self.db.execute("INSERT INTO runs (search, last_run) VALUES (?,?) "
                        "ON CONFLICT (search) DO UPDATE SET last_run=excluded.last_run", (search, when))
        self.db.commit()

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
