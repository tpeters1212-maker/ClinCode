"""SQLite storage. One database file per laptop holds the project, notes,
batches, assignments and answers. The file never leaves the laptop; data
moves between laptops only as packages (see packages.py)."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .schema import Project, parse_schema

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS notes (
    note_id TEXT PRIMARY KEY, patient_id TEXT, note_date TEXT, note_type TEXT,
    text TEXT NOT NULL, sha256 TEXT NOT NULL, stratum TEXT NOT NULL, imported_at TEXT);
CREATE TABLE IF NOT EXISTS annotators (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY, uid TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'planned', position INTEGER DEFAULT 0,
    purpose TEXT DEFAULT '', dual_fraction REAL DEFAULT 0.2,
    quotas TEXT DEFAULT '{}', annotators TEXT DEFAULT '[]',
    created_at TEXT, started_at TEXT, closed_at TEXT);
CREATE TABLE IF NOT EXISTS batch_strata (
    batch_id INTEGER, stratum TEXT, available INTEGER, drawn INTEGER,
    PRIMARY KEY (batch_id, stratum));
CREATE TABLE IF NOT EXISTS batch_notes (
    batch_id INTEGER, note_id TEXT, dual INTEGER DEFAULT 0, seq INTEGER,
    PRIMARY KEY (batch_id, note_id));
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL, note_id TEXT NOT NULL,
    annotator TEXT NOT NULL, seq INTEGER, status TEXT DEFAULT 'pending',
    responses TEXT DEFAULT '{}', seconds REAL DEFAULT 0,
    updated_at TEXT, submitted_at TEXT,
    UNIQUE (batch_id, note_id, annotator));
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, ts TEXT, actor TEXT, action TEXT, detail TEXT);
"""

BATCH_STATUSES = ("planned", "active", "closed")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        # Deleted rows are overwritten with zeros rather than left in free pages.
        self.db.execute("PRAGMA secure_delete=ON")
        self.db.executescript(SCHEMA_SQL)
        self._project: Project | None = None

    @contextmanager
    def tx(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    # settings ---------------------------------------------------------------
    def get(self, key: str, default: str | None = None) -> str | None:
        row = self.db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def put(self, key: str, value: str | None) -> None:
        self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))

    @property
    def role(self) -> str | None:
        """'coordinator' on the laptop that plans batches, 'annotator' on a
        laptop set up from a package, None before setup."""
        return self.get("role")

    def log(self, actor: str, action: str, detail: str = "") -> None:
        self.db.execute("INSERT INTO audit (ts, actor, action, detail) VALUES (?,?,?,?)",
                        (now(), actor, action, detail))

    # project ----------------------------------------------------------------
    def set_schema(self, yaml_text: str) -> Project:
        project = parse_schema(yaml_text)
        self.put("schema_yaml", yaml_text)
        self._project = project
        # Strata depend on the schema, so recompute them for existing notes.
        with self.tx():
            for row in self.db.execute("SELECT note_id, text FROM notes").fetchall():
                self.db.execute("UPDATE notes SET stratum=? WHERE note_id=?",
                                (project.stratum_of(row["text"]), row["note_id"]))
        return project

    @property
    def project(self) -> Project | None:
        if self._project is None:
            text = self.get("schema_yaml")
            self._project = parse_schema(text) if text else None
        return self._project

    # notes ------------------------------------------------------------------
    def import_notes_csv(self, data: bytes | str) -> dict:
        """Add notes from CSV with columns note_id, text and optional
        patient_id, note_date, note_type. Existing note ids are skipped."""
        if self.project is None:
            raise ValueError("Load a schema before importing notes.")
        text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
        reader = csv.DictReader(io.StringIO(text))
        cols = {c.strip().lower() for c in (reader.fieldnames or [])}
        missing = {"note_id", "text"} - cols
        if missing:
            raise ValueError(f"CSV is missing column(s): {', '.join(sorted(missing))}")
        added = skipped = 0
        errors = []
        stamp = now()
        with self.tx():
            for i, raw in enumerate(reader, start=2):
                row = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
                if not row.get("note_id") or not row.get("text"):
                    errors.append(f"row {i}: empty note_id or text")
                    continue
                body = raw.get("text") or raw.get("Text") or row["text"]
                cur = self.db.execute(
                    "INSERT OR IGNORE INTO notes VALUES (?,?,?,?,?,?,?,?)",
                    (row["note_id"], row.get("patient_id", ""), row.get("note_date", ""),
                     row.get("note_type", ""), body, hashlib.sha256(body.encode()).hexdigest(),
                     self.project.stratum_of(body), stamp))
                if cur.rowcount:
                    added += 1
                else:
                    skipped += 1
        return {"added": added, "skipped": skipped, "errors": errors[:20], "error_count": len(errors)}

    def note(self, note_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM notes WHERE note_id=?", (note_id,)).fetchone()

    def note_count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM notes").fetchone()[0]

    def strata_counts(self) -> list[dict]:
        """Per stratum: total notes and notes not yet placed in any batch."""
        rows = self.db.execute("""
            SELECT n.stratum, COUNT(*) AS total,
                   SUM(CASE WHEN bn.note_id IS NULL THEN 1 ELSE 0 END) AS unused
            FROM notes n LEFT JOIN (SELECT DISTINCT note_id FROM batch_notes) bn
                 ON bn.note_id = n.note_id
            GROUP BY n.stratum""").fetchall()
        by = {r["stratum"]: dict(r) for r in rows}
        labels = self.project.stratum_labels() if self.project else {}
        order = list(labels) + [s for s in by if s not in labels]
        return [{"stratum": s, "label": labels.get(s, s), "total": by.get(s, {}).get("total", 0),
                 "unused": by.get(s, {}).get("unused", 0)} for s in order]

    # annotators -------------------------------------------------------------
    def annotators(self, active_only: bool = True) -> list[sqlite3.Row]:
        q = "SELECT * FROM annotators" + (" WHERE active=1" if active_only else "") + " ORDER BY name"
        return self.db.execute(q).fetchall()

    def add_annotator(self, name: str) -> None:
        name = name.strip()
        if not name:
            raise ValueError("Name is required.")
        self.db.execute("INSERT INTO annotators (name) VALUES (?) ON CONFLICT(name) DO UPDATE SET active=1", (name,))

    def set_annotator_active(self, name: str, active: bool) -> None:
        self.db.execute("UPDATE annotators SET active=? WHERE name=?", (int(active), name))

    # batches ----------------------------------------------------------------
    def batches(self) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM batches ORDER BY position, id").fetchall()

    def batch(self, batch_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()

    def batch_by_uid(self, uid: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM batches WHERE uid=?", (uid,)).fetchone()

    def create_batch(self, name: str, purpose: str = "", dual_fraction: float = 0.2,
                     quotas: dict | None = None, annotators: list[str] | None = None,
                     uid: str | None = None, status: str = "planned") -> int:
        pos = self.db.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM batches").fetchone()[0]
        cur = self.db.execute(
            "INSERT INTO batches (uid, name, status, position, purpose, dual_fraction, quotas, annotators, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (uid or uuid.uuid4().hex, name.strip() or f"Batch {pos}", status, pos, purpose,
             dual_fraction, json.dumps(quotas or {}), json.dumps(annotators or []), now()))
        return cur.lastrowid

    def update_batch(self, batch_id: int, **fields) -> None:
        allowed = {"name", "purpose", "dual_fraction", "quotas", "annotators", "status",
                   "position", "started_at", "closed_at"}
        sets, vals = [], []
        for k, v in fields.items():
            if k not in allowed:
                raise KeyError(k)
            sets.append(f"{k}=?")
            vals.append(json.dumps(v) if k in {"quotas", "annotators"} else v)
        self.db.execute(f"UPDATE batches SET {', '.join(sets)} WHERE id=?", (*vals, batch_id))

    def delete_batch(self, batch_id: int) -> None:
        with self.tx():
            for t in ("items", "batch_notes", "batch_strata"):
                self.db.execute(f"DELETE FROM {t} WHERE batch_id=?", (batch_id,))
            self.db.execute("DELETE FROM batches WHERE id=?", (batch_id,))

    def move_batch(self, batch_id: int, direction: int) -> None:
        rows = [r["id"] for r in self.batches()]
        i = rows.index(batch_id)
        j = i + direction
        if 0 <= j < len(rows):
            rows[i], rows[j] = rows[j], rows[i]
            with self.tx():
                for pos, bid in enumerate(rows, start=1):
                    self.db.execute("UPDATE batches SET position=? WHERE id=?", (pos, bid))

    def batch_notes(self, batch_id: int) -> list[sqlite3.Row]:
        return self.db.execute("""
            SELECT bn.*, n.patient_id, n.note_date, n.note_type, n.stratum
            FROM batch_notes bn JOIN notes n ON n.note_id = bn.note_id
            WHERE bn.batch_id=? ORDER BY bn.seq""", (batch_id,)).fetchall()

    def batch_strata(self, batch_id: int) -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM batch_strata WHERE batch_id=?", (batch_id,)).fetchall()

    # items ------------------------------------------------------------------
    def items(self, batch_id: int | None = None, annotator: str | None = None) -> list[sqlite3.Row]:
        q, args = "SELECT i.*, b.name AS batch_name, b.status AS batch_status FROM items i JOIN batches b ON b.id=i.batch_id WHERE 1=1", []
        if batch_id is not None:
            q += " AND i.batch_id=?"
            args.append(batch_id)
        if annotator is not None:
            q += " AND i.annotator=?"
            args.append(annotator)
        return self.db.execute(q + " ORDER BY b.position, i.seq", args).fetchall()

    def item(self, item_id: int) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()

    def save_item(self, item_id: int, responses: dict, status: str, add_seconds: float = 0) -> None:
        if status not in {"draft", "submitted"}:
            raise ValueError(status)
        stamp = now()
        self.db.execute(
            "UPDATE items SET responses=?, status=?, seconds=seconds+?, updated_at=?,"
            " submitted_at=CASE WHEN ?='submitted' THEN ? ELSE submitted_at END WHERE id=?",
            (json.dumps(responses), status, max(0.0, min(add_seconds, 3600)), stamp, status, stamp, item_id))

    def progress(self, batch_id: int | None = None) -> list[dict]:
        q = """SELECT annotator, COUNT(*) AS total,
                   SUM(status='submitted') AS submitted, SUM(status='draft') AS drafts,
                   SUM(status='pending') AS pending,
                   SUM(CASE WHEN json_array_length(json_extract(responses,'$.needs_review')) > 0 THEN 1 ELSE 0 END) AS flagged,
                   AVG(CASE WHEN status='submitted' THEN seconds END) AS avg_seconds
               FROM items""" + (" WHERE batch_id=?" if batch_id is not None else "") + " GROUP BY annotator ORDER BY annotator"
        return [dict(r) for r in self.db.execute(q, (batch_id,) if batch_id is not None else ())]

    # custody --------------------------------------------------------------
    def last_change(self, batch_id: int, annotator: str) -> str | None:
        return self.db.execute("SELECT MAX(updated_at) FROM items WHERE batch_id=? AND annotator=?",
                               (batch_id, annotator)).fetchone()[0]

    def purge_batch(self, batch_id: int) -> int:
        """Annotator laptop: delete a batch and every note that no other batch
        on this laptop still uses. Returns the number of notes removed."""
        with self.tx() as db:
            ids = [r[0] for r in db.execute("SELECT note_id FROM batch_notes WHERE batch_id=?", (batch_id,))]
            for t in ("items", "batch_notes", "batch_strata"):
                db.execute(f"DELETE FROM {t} WHERE batch_id=?", (batch_id,))
            db.execute("DELETE FROM batches WHERE id=?", (batch_id,))
            removed = 0
            for nid in ids:
                if not db.execute("SELECT 1 FROM batch_notes WHERE note_id=?", (nid,)).fetchone():
                    removed += db.execute("DELETE FROM notes WHERE note_id=?", (nid,)).rowcount
        # Reclaim the freed pages so deleted note text does not linger in the file.
        self.db.execute("VACUUM")
        self.db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return removed
