"""SQLite storage for the web app: accounts, login sessions, conversations, settings."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets as pysecrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE NOT NULL, display TEXT,
    pw_salt TEXT NOT NULL, pw_hash TEXT NOT NULL, is_admin INTEGER DEFAULT 0, created_at REAL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at REAL, expires_at REAL
);
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, title TEXT, model TEXT,
    created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER NOT NULL, role TEXT NOT NULL,
    content TEXT NOT NULL, model TEXT, tokens_in INTEGER, tokens_out INTEGER, created_at REAL
);
CREATE INDEX IF NOT EXISTS messages_conv ON messages(conversation_id);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL,   -- upload | image | video
    prompt TEXT, model TEXT, status TEXT NOT NULL, file TEXT, mime TEXT, source_id INTEGER,
    task_id TEXT, error TEXT, created_at REAL, updated_at REAL
);
CREATE INDEX IF NOT EXISTS media_user ON media(user_id, kind);
CREATE TABLE IF NOT EXISTS code_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, project TEXT NOT NULL, task_id TEXT,
    title TEXT, status TEXT NOT NULL, log TEXT, created_at REAL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS login_failures (name TEXT, ts REAL);
"""

PBKDF2_ROUNDS = 240_000
SESSION_DAYS = 30


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or pysecrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return salt, digest.hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DB:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(users)")}
        if "can_code" not in cols:  # web step 2: who may give coding tasks to orch
            self.db.execute("ALTER TABLE users ADD COLUMN can_code INTEGER DEFAULT 0")
        self.lock = threading.RLock()

    def close(self) -> None:
        self.db.close()

    def q(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def x(self, sql: str, args: tuple = ()) -> int:
        with self.lock:
            return self.db.execute(sql, args).lastrowid or 0

    # -- accounts ---------------------------------------------------------------
    def user_count(self) -> int:
        return self.q("SELECT COUNT(*) AS n FROM users")[0]["n"]

    def add_user(self, name: str, password: str, display: str = "", admin: bool = False) -> int:
        salt, digest = hash_password(password)
        return self.x("INSERT INTO users(name, display, pw_salt, pw_hash, is_admin, created_at) VALUES(?,?,?,?,?,?)",
                      (name, display or name, salt, digest, int(admin), time.time()))

    def set_password(self, user_id: int, password: str) -> None:
        salt, digest = hash_password(password)
        self.x("UPDATE users SET pw_salt=?, pw_hash=? WHERE id=?", (salt, digest, user_id))
        self.x("DELETE FROM sessions WHERE user_id=?", (user_id,))

    def check_password(self, name: str, password: str) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM users WHERE name=?", (name,))
        if not rows:
            hash_password(password)  # same work either way: no timing hint about which names exist
            return None
        u = rows[0]
        _, digest = hash_password(password, u["pw_salt"])
        return u if hmac.compare_digest(digest, u["pw_hash"]) else None

    def recent_failures(self, name: str, seconds: float = 600) -> int:
        cutoff = time.time() - seconds
        self.x("DELETE FROM login_failures WHERE ts < ?", (cutoff,))
        return self.q("SELECT COUNT(*) AS n FROM login_failures WHERE name=?", (name,))[0]["n"]

    def note_failure(self, name: str) -> None:
        self.x("INSERT INTO login_failures(name, ts) VALUES(?,?)", (name, time.time()))

    def users(self) -> list[sqlite3.Row]:
        return self.q("SELECT id, name, display, is_admin, can_code, created_at FROM users ORDER BY id")

    def set_can_code(self, user_id: int, value: bool) -> None:
        self.x("UPDATE users SET can_code=? WHERE id=?", (int(value), user_id))

    def delete_user(self, user_id: int) -> None:
        conv = [r["id"] for r in self.q("SELECT id FROM conversations WHERE user_id=?", (user_id,))]
        for cid in conv:
            self.x("DELETE FROM messages WHERE conversation_id=?", (cid,))
        self.x("DELETE FROM conversations WHERE user_id=?", (user_id,))
        self.x("DELETE FROM sessions WHERE user_id=?", (user_id,))
        self.x("DELETE FROM users WHERE id=?", (user_id,))

    # -- sessions ---------------------------------------------------------------
    def new_session(self, user_id: int) -> str:
        token = pysecrets.token_urlsafe(32)
        now = time.time()
        self.x("INSERT INTO sessions(token_hash, user_id, created_at, expires_at) VALUES(?,?,?,?)",
               (_token_hash(token), user_id, now, now + SESSION_DAYS * 86400))
        return token

    def session_user(self, token: str | None) -> sqlite3.Row | None:
        if not token:
            return None
        rows = self.q("SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id "
                      "WHERE s.token_hash=? AND s.expires_at > ?", (_token_hash(token), time.time()))
        return rows[0] if rows else None

    def end_session(self, token: str) -> None:
        self.x("DELETE FROM sessions WHERE token_hash=?", (_token_hash(token),))

    # -- conversations ----------------------------------------------------------
    def conversations(self, user_id: int) -> list[sqlite3.Row]:
        return self.q("SELECT id, title, model, updated_at FROM conversations WHERE user_id=? "
                      "ORDER BY updated_at DESC LIMIT 200", (user_id,))

    def conversation(self, user_id: int, cid: int) -> sqlite3.Row | None:
        rows = self.q("SELECT * FROM conversations WHERE id=? AND user_id=?", (cid, user_id))
        return rows[0] if rows else None

    def new_conversation(self, user_id: int, title: str, model: str) -> int:
        now = time.time()
        return self.x("INSERT INTO conversations(user_id, title, model, created_at, updated_at) VALUES(?,?,?,?,?)",
                      (user_id, title, model, now, now))

    def rename(self, cid: int, title: str) -> None:
        self.x("UPDATE conversations SET title=? WHERE id=?", (title, cid))

    def delete_conversation(self, cid: int) -> None:
        self.x("DELETE FROM messages WHERE conversation_id=?", (cid,))
        self.x("DELETE FROM conversations WHERE id=?", (cid,))

    def messages(self, cid: int) -> list[sqlite3.Row]:
        return self.q("SELECT id, role, content, model, created_at FROM messages WHERE conversation_id=? ORDER BY id",
                      (cid,))

    def add_message(self, cid: int, role: str, content: str, model: str = "",
                    tokens_in: int | None = None, tokens_out: int | None = None) -> int:
        now = time.time()
        mid = self.x("INSERT INTO messages(conversation_id, role, content, model, tokens_in, tokens_out, created_at) "
                     "VALUES(?,?,?,?,?,?,?)", (cid, role, content, model, tokens_in, tokens_out, now))
        self.x("UPDATE conversations SET updated_at=?, model=COALESCE(NULLIF(?, ''), model) WHERE id=?",
               (now, model, cid))
        return mid

    # -- settings ---------------------------------------------------------------
    def setting(self, key: str, default: Any = None) -> Any:
        rows = self.q("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key: str, value: Any) -> None:
        self.x("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)", (key, json.dumps(value, ensure_ascii=False)))


def data_dir() -> Path:
    """Where the web app keeps its database and keys: MAO_WEB_DATA, else .maoweb next to the maoweb folder (gitignored)."""
    env = os.environ.get("MAO_WEB_DATA")
    return Path(env) if env else Path(__file__).resolve().parents[1] / ".maoweb"


def _now() -> float:
    return time.time()


def add_media(db: DB, user_id: int, kind: str, status: str, **kw) -> int:
    cols = ["user_id", "kind", "status", "created_at", "updated_at"] + list(kw)
    vals = [user_id, kind, status, _now(), _now()] + list(kw.values())
    return db.x(f"INSERT INTO media({', '.join(cols)}) VALUES({', '.join('?' for _ in cols)})", tuple(vals))


def update_media(db: DB, mid: int, **kw) -> None:
    kw["updated_at"] = _now()
    db.x(f"UPDATE media SET {', '.join(k + '=?' for k in kw)} WHERE id=?", (*kw.values(), mid))


def get_media(db: DB, mid: int, user_id: int | None = None):
    rows = db.q("SELECT * FROM media WHERE id=?" + (" AND user_id=?" if user_id is not None else ""),
                (mid,) if user_id is None else (mid, user_id))
    return rows[0] if rows else None
