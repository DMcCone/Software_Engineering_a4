"""Persistence / Data Access package: SQLite database and repositories.

Only this module contains SQL. Repositories return model objects.
"""
import sqlite3
from contextlib import contextmanager
from typing import Dict, Iterable, List, Optional

from .models import (Category, Clock, FieldChange, Patient, Role, SuggestionOutcome,
                     VisitNote)

SCHEMA = """
CREATE TABLE IF NOT EXISTS patients (
    patient_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK (length(trim(name)) > 0),
    date_of_birth TEXT, phone TEXT, address TEXT
);
CREATE TABLE IF NOT EXISTS visit_notes (
    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES patients(patient_id),
    visit_date TEXT NOT NULL,
    text TEXT NOT NULL CHECK (length(trim(text)) > 0),
    category TEXT, confirmed_by TEXT, created_at TEXT NOT NULL,
    suggested_category TEXT,
    suggestion_status TEXT NOT NULL DEFAULT 'not_requested'
);
CREATE TABLE IF NOT EXISTS field_change_history (
    change_id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL REFERENCES patients(patient_id),
    field TEXT NOT NULL, old_value TEXT, new_value TEXT,
    changed_at TEXT NOT NULL, changed_by TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS staff_users (
    username TEXT PRIMARY KEY, salt TEXT NOT NULL,
    password_hash TEXT NOT NULL, role TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_patient_date ON visit_notes (patient_id, visit_date);
CREATE INDEX IF NOT EXISTS idx_changes_patient ON field_change_history (patient_id);
-- Business rules enforced in the database as a second line of defence:
CREATE TRIGGER IF NOT EXISTS patient_id_immutable BEFORE UPDATE OF patient_id ON patients
  BEGIN SELECT RAISE(ABORT, 'patient_id is immutable'); END;
CREATE TRIGGER IF NOT EXISTS patients_no_delete BEFORE DELETE ON patients
  BEGIN SELECT RAISE(ABORT, 'patients cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS notes_no_update BEFORE UPDATE ON visit_notes
  BEGIN SELECT RAISE(ABORT, 'saved visit notes cannot be edited'); END;
CREATE TRIGGER IF NOT EXISTS notes_no_delete BEFORE DELETE ON visit_notes
  BEGIN SELECT RAISE(ABORT, 'visit notes cannot be deleted'); END;
"""

# Columns added in Version 2; existing Version 1 databases are upgraded in place.
_V2_NOTE_COLUMNS = {
    "suggested_category": "TEXT",
    "suggestion_status": "TEXT NOT NULL DEFAULT 'not_requested'",
}


def _category_or_none(label: Optional[str]) -> Optional[Category]:
    return Category.from_label(label) if label else None


class Database:
    """Owns the SQLite connection; exposes query helpers and transactions."""

    def __init__(self, path: str = "prs.db"):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        existing = {row["name"] for row in self._conn.execute("PRAGMA table_info(visit_notes)")}
        with self.transaction() as conn:
            for column, definition in _V2_NOTE_COLUMNS.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE visit_notes ADD COLUMN {column} {definition}")

    @contextmanager
    def transaction(self):
        """Commit if the block succeeds, roll back if it raises."""
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def fetch_one(self, sql: str, args: Iterable = ()) -> Optional[sqlite3.Row]:
        return self._conn.execute(sql, tuple(args)).fetchone()

    def fetch_all(self, sql: str, args: Iterable = ()) -> List[sqlite3.Row]:
        return self._conn.execute(sql, tuple(args)).fetchall()

    def close(self) -> None:
        self._conn.close()


class UserRepository:
    def __init__(self, db: Database):
        self._db = db

    def add(self, username: str, salt: str, password_hash: str, role: Role) -> None:
        with self._db.transaction() as conn:
            conn.execute("INSERT INTO staff_users VALUES (?,?,?,?)",
                         (username, salt, password_hash, role.value))

    def find(self, username: str) -> Optional[dict]:
        row = self._db.fetch_one("SELECT * FROM staff_users WHERE username = ?", (username,))
        return dict(row) if row else None

    def count(self) -> int:
        return self._db.fetch_one("SELECT COUNT(*) FROM staff_users")[0]


class PatientRepository:
    _SORT_COLUMNS = {"id": "patient_id", "name": "name COLLATE NOCASE"}

    def __init__(self, db: Database, clock: Clock):
        self._db, self._clock = db, clock

    @staticmethod
    def _to_patient(row) -> Patient:
        return Patient(row["patient_id"], row["name"], row["date_of_birth"],
                       row["phone"], row["address"])

    def add(self, name: str, date_of_birth: Optional[str], phone: Optional[str],
            address: Optional[str]) -> Patient:
        with self._db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO patients (name, date_of_birth, phone, address) VALUES (?,?,?,?)",
                (name, date_of_birth, phone, address))
        return Patient(cur.lastrowid, name, date_of_birth, phone, address)

    def find_by_id(self, patient_id: int) -> Optional[Patient]:
        row = self._db.fetch_one("SELECT * FROM patients WHERE patient_id = ?", (patient_id,))
        return self._to_patient(row) if row else None

    def list_all(self, sort_by: str = "id") -> List[Patient]:
        column = self._SORT_COLUMNS.get(sort_by, "patient_id")   # whitelist: never user text in SQL
        return [self._to_patient(r)
                for r in self._db.fetch_all(f"SELECT * FROM patients ORDER BY {column}")]

    def update(self, current: Patient, changes: Dict[str, Optional[str]], changed_by: str) -> None:
        """Apply changed fields and record old/new value + timestamp in one transaction."""
        changed_at = self._clock().isoformat(timespec="seconds")
        with self._db.transaction() as conn:
            for field, new_value in changes.items():
                old_value = getattr(current, field)
                if old_value == new_value:
                    continue
                conn.execute(f"UPDATE patients SET {field} = ? WHERE patient_id = ?",
                             (new_value, current.patient_id))
                conn.execute(
                    "INSERT INTO field_change_history "
                    "(patient_id, field, old_value, new_value, changed_at, changed_by) "
                    "VALUES (?,?,?,?,?,?)",
                    (current.patient_id, field, old_value, new_value, changed_at, changed_by))

    def change_history(self, patient_id: int) -> List[FieldChange]:
        rows = self._db.fetch_all(
            "SELECT * FROM field_change_history WHERE patient_id = ? ORDER BY change_id",
            (patient_id,))
        return [FieldChange(r["patient_id"], r["field"], r["old_value"], r["new_value"],
                            r["changed_at"], r["changed_by"]) for r in rows]


class VisitNoteRepository:
    def __init__(self, db: Database, clock: Clock):
        self._db, self._clock = db, clock

    @staticmethod
    def _to_note(row) -> VisitNote:
        return VisitNote(
            row["note_id"], row["patient_id"], row["visit_date"], row["text"],
            _category_or_none(row["category"]), row["confirmed_by"], row["created_at"],
            _category_or_none(row["suggested_category"]),
            SuggestionOutcome(row["suggestion_status"]))

    def add(self, patient_id: int, visit_date: str, text: str,
            category: Optional[Category], confirmed_by: Optional[str],
            suggested_category: Optional[Category],
            suggestion_status: SuggestionOutcome) -> VisitNote:
        created_at = self._clock().isoformat(timespec="seconds")
        with self._db.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO visit_notes (patient_id, visit_date, text, category, confirmed_by, "
                "created_at, suggested_category, suggestion_status) VALUES (?,?,?,?,?,?,?,?)",
                (patient_id, visit_date, text, category.value if category else None,
                 confirmed_by, created_at,
                 suggested_category.value if suggested_category else None,
                 suggestion_status.value))
        return VisitNote(cur.lastrowid, patient_id, visit_date, text, category, confirmed_by,
                         created_at, suggested_category, suggestion_status)

    def list_for_patient(self, patient_id: int, category: Optional[Category] = None,
                         uncategorized: bool = False, start: Optional[str] = None,
                         end: Optional[str] = None) -> List[VisitNote]:
        conditions, args = ["patient_id = ?"], [patient_id]
        if category:
            conditions.append("category = ?")
            args.append(category.value)
        elif uncategorized:
            conditions.append("category IS NULL")
        if start:
            conditions.append("visit_date >= ?")
            args.append(start)
        if end:
            conditions.append("visit_date <= ?")
            args.append(end)
        sql = ("SELECT * FROM visit_notes WHERE " + " AND ".join(conditions) +
               " ORDER BY visit_date DESC, note_id DESC")
        return [self._to_note(r) for r in self._db.fetch_all(sql, args)]