"""SQLite-backed Repository. One instance per connection (per request)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from .models import Claim, Household, Item, Notice, NoticeKind, Person, User

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                 INTEGER PRIMARY KEY,
    google_sub         TEXT NOT NULL UNIQUE,
    email              TEXT NOT NULL,
    name               TEXT NOT NULL,
    created_at         TEXT NOT NULL,
    last_seen_at       TEXT,
    gerry_ghost        INTEGER NOT NULL DEFAULT 0,
    gerry_last_line    TEXT,
    visit_count        INTEGER NOT NULL DEFAULT 0,
    visits_since_claim INTEGER NOT NULL DEFAULT 0,
    punishment_mode    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS households (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    invite_token TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS people (
    id           INTEGER PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    name         TEXT NOT NULL,
    user_id      INTEGER UNIQUE REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES people(id),
    title       TEXT NOT NULL,
    url         TEXT,
    image       TEXT,
    price_minor INTEGER NOT NULL CHECK (price_minor > 0),
    note        TEXT,
    really_want INTEGER NOT NULL DEFAULT 0,
    position    INTEGER NOT NULL,
    bought_at   TEXT,
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS claims (
    item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    claimed_at   TEXT NOT NULL,
    PRIMARY KEY (item_id, user_id)
);
CREATE TABLE IF NOT EXISTS notices (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    params     TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reveals (
    viewer_user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    PRIMARY KEY (viewer_user_id, target_person_id)
);
CREATE TABLE IF NOT EXISTS gerry_events (
    id      INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    trigger TEXT NOT NULL,
    at      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS magic_links (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    token   TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_people_household ON people(household_id);
CREATE INDEX IF NOT EXISTS idx_items_person ON items(person_id, position);
CREATE INDEX IF NOT EXISTS idx_gerry_events_user ON gerry_events(user_id);
CREATE INDEX IF NOT EXISTS idx_gerry_events_at ON gerry_events(at);
CREATE INDEX IF NOT EXISTS idx_claims_user ON claims(user_id);
CREATE INDEX IF NOT EXISTS idx_notices_user ON notices(user_id);
"""


def connect(path: str) -> sqlite3.Connection:
    # isolation_level=None: we issue BEGIN/COMMIT ourselves.
    conn = sqlite3.connect(path, isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Add a column to an already-deployed DB. SCHEMA covers fresh ones; this
    covers the live one, where CREATE TABLE IF NOT EXISTS is a no-op."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _ensure_column(conn, "users", "punishment_mode", "INTEGER NOT NULL DEFAULT 0")


def _dt(text: str | None) -> datetime | None:
    return datetime.fromisoformat(text) if text else None


def _user(r) -> User:
    return User(r["id"], r["google_sub"], r["email"], r["name"], _dt(r["created_at"]), _dt(r["last_seen_at"]),
                bool(r["gerry_ghost"]), r["gerry_last_line"], r["visit_count"], r["visits_since_claim"],
                bool(r["punishment_mode"]))


def _household(r) -> Household:
    return Household(r["id"], r["name"], r["invite_token"])


def _person(r) -> Person:
    return Person(r["id"], r["household_id"], r["name"], r["user_id"])


def _item(r) -> Item:
    return Item(r["id"], r["person_id"], r["title"], r["url"], r["image"], r["price_minor"], r["note"],
                bool(r["really_want"]), r["position"], _dt(r["bought_at"]), _dt(r["created_at"]))


def _claim(r) -> Claim:
    return Claim(r["item_id"], r["user_id"], r["amount_minor"], _dt(r["claimed_at"]))


def _notice(r) -> Notice:
    return Notice(r["id"], r["user_id"], NoticeKind(r["kind"]), json.loads(r["params"]), _dt(r["created_at"]))


class SqliteRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._depth = 0

    @contextmanager
    def write(self) -> Iterator[None]:
        if self._depth:
            self._depth += 1
            try:
                yield
            finally:
                self._depth -= 1
            return
        # IMMEDIATE takes the write lock up front, so check-then-write
        # sequences ("is there room left to claim?") can't interleave.
        self._conn.execute("BEGIN IMMEDIATE")
        self._depth = 1
        try:
            yield
        except BaseException:
            self._conn.execute("ROLLBACK")
            raise
        else:
            self._conn.execute("COMMIT")
        finally:
            self._depth = 0

    def _one(self, sql, *args):
        return self._conn.execute(sql, args).fetchone()

    def _all(self, sql, *args):
        return self._conn.execute(sql, args).fetchall()

    def _run(self, sql, *args):
        return self._conn.execute(sql, args)

    # users ----------------------------------------------------------------

    def add_user(self, google_sub, email, name, at):
        cur = self._run("INSERT INTO users (google_sub, email, name, created_at, last_seen_at) VALUES (?, ?, ?, ?, ?)",
                        google_sub, email, name, at.isoformat(), at.isoformat())
        return self.user(cur.lastrowid)

    def user(self, user_id):
        r = self._one("SELECT * FROM users WHERE id = ?", user_id)
        return _user(r) if r else None

    def user_by_sub(self, google_sub):
        r = self._one("SELECT * FROM users WHERE google_sub = ?", google_sub)
        return _user(r) if r else None

    def all_users(self):
        return [_user(r) for r in self._all("SELECT * FROM users ORDER BY name COLLATE NOCASE")]

    def touch_user(self, user_id, at):
        self._run("UPDATE users SET last_seen_at = ? WHERE id = ?", at.isoformat(), user_id)

    def rename_user(self, user_id, name):
        self._run("UPDATE users SET name = ? WHERE id = ?", name, user_id)

    def start_visit(self, user_id):
        self._run("UPDATE users SET visit_count = visit_count + 1, visits_since_claim = visits_since_claim + 1 "
                  "WHERE id = ?", user_id)

    def reset_visits_since_claim(self, user_id):
        self._run("UPDATE users SET visits_since_claim = 0 WHERE id = ?", user_id)

    def reset_all_visits_since_claim(self):
        self._run("UPDATE users SET visits_since_claim = 0")

    def set_gerry(self, user_id, ghost, last_line):
        self._run("UPDATE users SET gerry_ghost = ?, gerry_last_line = ? WHERE id = ?", int(ghost), last_line, user_id)

    def delete_user(self, user_id):
        self._run("DELETE FROM users WHERE id = ?", user_id)

    def user_by_magic_token(self, token):
        r = self._one("SELECT users.* FROM users JOIN magic_links ON magic_links.user_id = users.id "
                      "WHERE magic_links.token = ?", token)
        return _user(r) if r else None

    def set_magic_token(self, user_id, token):
        self._run("INSERT INTO magic_links (user_id, token) VALUES (?, ?) "
                  "ON CONFLICT(user_id) DO UPDATE SET token = excluded.token", user_id, token)

    def magic_token_for_user(self, user_id):
        r = self._one("SELECT token FROM magic_links WHERE user_id = ?", user_id)
        return r["token"] if r else None

    def set_punishment_mode(self, user_id, enabled):
        self._run("UPDATE users SET punishment_mode = ? WHERE id = ?", int(enabled), user_id)

    # households -----------------------------------------------------------

    def add_household(self, name, invite_token):
        cur = self._run("INSERT INTO households (name, invite_token) VALUES (?, ?)", name, invite_token)
        return Household(cur.lastrowid, name, invite_token)

    def household(self, household_id):
        r = self._one("SELECT * FROM households WHERE id = ?", household_id)
        return _household(r) if r else None

    def household_by_invite(self, invite_token):
        r = self._one("SELECT * FROM households WHERE invite_token = ?", invite_token)
        return _household(r) if r else None

    def all_households(self):
        return [_household(r) for r in self._all("SELECT * FROM households ORDER BY name COLLATE NOCASE")]

    def rename_household(self, household_id, name):
        self._run("UPDATE households SET name = ? WHERE id = ?", name, household_id)

    def delete_household(self, household_id):
        self._run("DELETE FROM households WHERE id = ?", household_id)

    # people ---------------------------------------------------------------

    def add_person(self, household_id, name, user_id):
        cur = self._run("INSERT INTO people (household_id, name, user_id) VALUES (?, ?, ?)", household_id, name, user_id)
        return Person(cur.lastrowid, household_id, name, user_id)

    def person(self, person_id):
        r = self._one("SELECT * FROM people WHERE id = ?", person_id)
        return _person(r) if r else None

    def person_for_user(self, user_id):
        r = self._one("SELECT * FROM people WHERE user_id = ?", user_id)
        return _person(r) if r else None

    def people_in_household(self, household_id):
        # users first, then dependents, each by name
        return [_person(r) for r in self._all(
            "SELECT * FROM people WHERE household_id = ? ORDER BY user_id IS NULL, name COLLATE NOCASE", household_id)]

    def rename_person(self, person_id, name):
        self._run("UPDATE people SET name = ? WHERE id = ?", name, person_id)

    def move_person(self, person_id, household_id):
        self._run("UPDATE people SET household_id = ? WHERE id = ?", household_id, person_id)

    def delete_person(self, person_id):
        self._run("DELETE FROM people WHERE id = ?", person_id)

    def dependents_count_by_household(self):
        return [(r["household_id"], r["n"]) for r in self._all(
            "SELECT household_id, COUNT(*) AS n FROM people WHERE user_id IS NULL GROUP BY household_id")]

    # items ----------------------------------------------------------------

    def add_item(self, person_id, title, url, image, price_minor, note, really_want, at):
        pos = self._one("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM items WHERE person_id = ?", person_id)["p"]
        cur = self._run(
            "INSERT INTO items (person_id, title, url, image, price_minor, note, really_want, position, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            person_id, title, url, image, price_minor, note, int(really_want), pos, at.isoformat())
        return self.item(cur.lastrowid)

    def item(self, item_id):
        r = self._one("SELECT * FROM items WHERE id = ?", item_id)
        return _item(r) if r else None

    def items_for_person(self, person_id):
        return [_item(r) for r in self._all("SELECT * FROM items WHERE person_id = ? ORDER BY position, id", person_id)]

    def update_item(self, item_id, title, url, image, price_minor, note, really_want):
        self._run("UPDATE items SET title = ?, url = ?, image = ?, price_minor = ?, note = ?, really_want = ? WHERE id = ?",
                  title, url, image, price_minor, note, int(really_want), item_id)

    def set_positions(self, ordered_item_ids):
        for pos, item_id in enumerate(ordered_item_ids):
            self._run("UPDATE items SET position = ? WHERE id = ?", pos, item_id)

    def set_bought(self, item_id, at):
        self._run("UPDATE items SET bought_at = ? WHERE id = ?", at.isoformat() if at else None, item_id)

    def delete_item(self, item_id):
        self._run("DELETE FROM items WHERE id = ?", item_id)

    def delete_all_items(self):
        self._run("DELETE FROM claims")
        self._run("DELETE FROM items")

    def item_stats_by_person(self):
        return [(r["person_id"], r["n"], r["total"], r["sulk"]) for r in self._all(
            "SELECT person_id, COUNT(*) AS n, SUM(price_minor) AS total, SUM(really_want) AS sulk "
            "FROM items GROUP BY person_id")]

    def all_items(self):
        return [_item(r) for r in self._all("SELECT * FROM items")]

    # claims ---------------------------------------------------------------

    def claims_for_item(self, item_id):
        return [_claim(r) for r in self._all("SELECT * FROM claims WHERE item_id = ? ORDER BY claimed_at", item_id)]

    def claims_by_user(self, user_id):
        return [_claim(r) for r in self._all("SELECT * FROM claims WHERE user_id = ? ORDER BY claimed_at", user_id)]

    def add_claim(self, item_id, user_id, amount_minor, at):
        self._run("INSERT INTO claims (item_id, user_id, amount_minor, claimed_at) VALUES (?, ?, ?, ?)",
                  item_id, user_id, amount_minor, at.isoformat())

    def set_claim_amount(self, item_id, user_id, amount_minor):
        self._run("UPDATE claims SET amount_minor = ? WHERE item_id = ? AND user_id = ?", amount_minor, item_id, user_id)

    def delete_claim(self, item_id, user_id):
        self._run("DELETE FROM claims WHERE item_id = ? AND user_id = ?", item_id, user_id)

    # notices --------------------------------------------------------------

    def add_notice(self, user_id, kind, params, at):
        self._run("INSERT INTO notices (user_id, kind, params, created_at) VALUES (?, ?, ?, ?)",
                  user_id, kind.value, json.dumps(params), at.isoformat())

    def notices_for_user(self, user_id):
        return [_notice(r) for r in self._all("SELECT * FROM notices WHERE user_id = ? ORDER BY id DESC", user_id)]

    def delete_notice(self, notice_id, user_id):
        self._run("DELETE FROM notices WHERE id = ? AND user_id = ?", notice_id, user_id)

    def delete_all_notices(self):
        self._run("DELETE FROM notices")

    # settings -------------------------------------------------------------

    def setting(self, key):
        r = self._one("SELECT value FROM settings WHERE key = ?", key)
        return r["value"] if r else None

    def set_setting(self, key, value):
        self._run("INSERT INTO settings (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value", key, value)

    # reveals ----------------------------------------------------------------

    def set_reveal(self, viewer_user_id, target_person_id, revealed):
        if revealed:
            self._run("INSERT OR IGNORE INTO reveals (viewer_user_id, target_person_id) VALUES (?, ?)",
                      viewer_user_id, target_person_id)
        else:
            self._run("DELETE FROM reveals WHERE viewer_user_id = ? AND target_person_id = ?",
                      viewer_user_id, target_person_id)

    def revealed_person_ids(self, viewer_user_id):
        return {r["target_person_id"] for r in self._all(
            "SELECT target_person_id FROM reveals WHERE viewer_user_id = ?", viewer_user_id)}

    def is_revealed(self, viewer_user_id, target_person_id):
        return self._one("SELECT 1 FROM reveals WHERE viewer_user_id = ? AND target_person_id = ?",
                         viewer_user_id, target_person_id) is not None

    # gerry events -------------------------------------------------------------

    def add_gerry_event(self, user_id, trigger, at):
        self._run("INSERT INTO gerry_events (user_id, trigger, at) VALUES (?, ?, ?)",
                  user_id, trigger, at.isoformat())

    def gerry_event_totals(self):
        return [(r["user_id"], r["trigger"], r["n"]) for r in self._all(
            "SELECT user_id, trigger, COUNT(*) AS n FROM gerry_events GROUP BY user_id, trigger")]

    def delete_all_gerry_events(self):
        self._run("DELETE FROM gerry_events")
