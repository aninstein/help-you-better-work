"""SQLite document repository for a single-process local application."""

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.getenv("DUET_DATA_DIR", str(Path(__file__).resolve().parents[2] / "data")))


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DATA_DIR / "duet.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS talkers (id TEXT PRIMARY KEY, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS deleted_talkers (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS deleted_sessions (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                    session_id TEXT, sequence INTEGER, data TEXT,
                    PRIMARY KEY(session_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS commands (key TEXT PRIMARY KEY, hash TEXT, data TEXT);
                CREATE TABLE IF NOT EXISTS guidance_events (
                    revision INTEGER PRIMARY KEY AUTOINCREMENT,
                    guidance_id TEXT NOT NULL, scope_key TEXT NOT NULL, payload TEXT NOT NULL
                );
            """)

    def talkers(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT state FROM talkers ORDER BY rowid").fetchall()
        return [json.loads(row[0]) for row in rows]

    def save_talker(self, talker: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM deleted_talkers WHERE id=?", (talker["id"],)).fetchone():
                raise ValueError("Talker 已删除。")
            db.execute(
                "INSERT OR REPLACE INTO talkers VALUES (?,?)",
                (talker["id"], json.dumps(talker, ensure_ascii=False)),
            )

    def talker_deleted(self, talker_id: str) -> bool:
        with self.connect() as db:
            return (
                db.execute("SELECT 1 FROM deleted_talkers WHERE id=?", (talker_id,)).fetchone()
                is not None
            )

    def session_deleted(self, session_id: str) -> bool:
        with self.connect() as db:
            return (
                db.execute("SELECT 1 FROM deleted_sessions WHERE id=?", (session_id,)).fetchone()
                is not None
            )

    def delete_talker(self, talker_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO deleted_talkers VALUES (?)", (talker_id,))
            states = [json.loads(row[0]) for row in db.execute("SELECT state FROM sessions")]
            removed = [s for s in states if s.get("talker_id") == talker_id]
            for state in removed:
                sid, prefix = state["id"], state["id"] + ":"
                db.execute("INSERT OR IGNORE INTO deleted_sessions VALUES (?)", (sid,))
                db.execute("DELETE FROM sessions WHERE id=?", (sid,))
                db.execute("DELETE FROM events WHERE session_id=?", (sid,))
                db.execute(
                    "DELETE FROM commands WHERE key=? OR substr(key,1,?)=?",
                    (sid, len(prefix), prefix),
                )
                db.execute(
                    "DELETE FROM settings WHERE key='active_session' AND value=?",
                    (json.dumps(sid),),
                )
            db.execute("DELETE FROM settings WHERE key=?", ("active_session:" + talker_id,))
            db.execute(
                "DELETE FROM guidance_events WHERE json_extract(payload,'$.talker_id')=?",
                (talker_id,),
            )
            db.execute("DELETE FROM talkers WHERE id=?", (talker_id,))
        return removed

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def load(self, session_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT state FROM sessions WHERE id=?", (session_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def sessions(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT state FROM sessions ORDER BY rowid DESC").fetchall()
        return [json.loads(r[0]) for r in rows]

    def save(self, state: dict[str, Any], event: dict[str, Any] | None = None) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM deleted_sessions WHERE id=?", (state["id"],)).fetchone():
                raise ValueError("会话所属 Talker 已删除。")
            if db.execute(
                "SELECT 1 FROM deleted_talkers WHERE id=?", (state.get("talker_id"),)
            ).fetchone():
                raise ValueError("Talker 已删除。")
            db.execute(
                "INSERT OR REPLACE INTO sessions VALUES (?,?)",
                (state["id"], json.dumps(state, ensure_ascii=False)),
            )
            if event:
                db.execute(
                    "INSERT INTO events VALUES (?,?,?)",
                    (state["id"], event["sequence"], json.dumps(event, ensure_ascii=False)),
                )

    def events(self, session_id: str, sequence: int) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT data FROM events WHERE session_id=? AND sequence>? "
                "ORDER BY sequence LIMIT 100",
                (session_id, sequence),
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def setting(self, key: str, default: Any = None) -> Any:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set_setting(self, key: str, value: Any) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if (
                key.startswith("active_session:")
                and db.execute(
                    "SELECT 1 FROM deleted_talkers WHERE id=?",
                    (key.removeprefix("active_session:"),),
                ).fetchone()
            ):
                raise ValueError("Talker 已删除。")
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def command(self, key: str) -> tuple[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT hash,data FROM commands WHERE key=?", (key,)).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def save_command(self, key: str, digest: str, data: Any) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM deleted_sessions WHERE id=?", (key.split(":", 1)[0],)
            ).fetchone():
                raise ValueError("会话已删除。")
            db.execute("INSERT INTO commands VALUES (?,?,?)", (key, digest, json.dumps(data)))

    def append_guidance(self, scope_key: str, record: dict[str, Any]) -> dict[str, Any]:
        """The append commit is the guidance effect point, independent of Markdown IO."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM deleted_talkers WHERE id=?", (record.get("talker_id"),)
            ).fetchone():
                raise ValueError("Talker 已删除。")
            cursor = db.execute(
                "INSERT INTO guidance_events(guidance_id,scope_key,payload) VALUES (?,?,?)",
                (record["id"], scope_key, json.dumps(record, ensure_ascii=False)),
            )
            record = {**record, "revision": cursor.lastrowid}
            db.execute(
                "UPDATE guidance_events SET payload=? WHERE revision=?",
                (json.dumps(record, ensure_ascii=False), cursor.lastrowid),
            )
        return record

    def guidance(self, scope_keys: list[str]) -> list[dict[str, Any]]:
        if not scope_keys:
            return []
        placeholders = ",".join("?" for _ in scope_keys)
        with self.connect() as db:
            rows = db.execute(
                "SELECT payload FROM guidance_events WHERE revision IN "
                "(SELECT MAX(revision) FROM guidance_events "
                f"WHERE scope_key IN ({placeholders}) GROUP BY guidance_id) ORDER BY revision",
                scope_keys,
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def claim_legacy_guidance(self, talker_id: str) -> None:
        # Earlier versions had only one Talker. Preserve revisions and scope keys.
        with self.connect() as db:
            db.execute(
                "UPDATE guidance_events SET payload=json_set(payload, '$.talker_id', ?) "
                "WHERE json_extract(payload, '$.talker_id') IS NULL",
                (talker_id,),
            )

    def clear_guidance(
        self, scope_keys: list[str] | None = None, talker_id: str | None = None
    ) -> None:
        with self.connect() as db:
            if talker_id is not None:
                db.execute(
                    "DELETE FROM guidance_events WHERE json_extract(payload, '$.talker_id') = ?",
                    (talker_id,),
                )
            elif scope_keys is None:
                db.execute("DELETE FROM guidance_events")
            elif scope_keys:
                placeholders = ",".join("?" for _ in scope_keys)
                db.execute(
                    f"DELETE FROM guidance_events WHERE scope_key IN ({placeholders})", scope_keys
                )
