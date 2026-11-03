"""Persistence / Data Access package: SQLite database and repositories."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List, Optional

from .models import (Category, FieldChange, Patient, Role, StaffUser, VisitNote)

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
    category TEXT, confirmed_by TEXT, created_at TEXT NOT NULL
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
-- Business rules enforced at the database level as a second line of defence:
CREATE TRIGGER IF NOT EXISTS patient_id_immutable BEFORE UPDATE OF patient_id ON patients
  BEGIN SELECT RAISE(ABORT, 'patient_id is immutable'); END;
CREATE TRIGGER IF NOT EXISTS patients_no_delete BEFORE DELETE ON patients
  BEGIN SELECT RAISE(ABORT, 'patients cannot be deleted'); END;
CREATE TRIGGER IF NOT EXISTS notes_no_update BEFORE UPDATE ON visit_notes
  BEGIN SELECT RAISE(ABORT, 'saved visit notes cannot be edited'); END;
CREATE TRIGGER IF NOT EXISTS notes_no_delete BEFORE DELETE ON visit_notes
  BEGIN SELECT RAISE(ABORT, 'visit notes cannot be deleted'); END;
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    """Owns the connection and provides transactions."""

    def __init__(self, path: str = "prs.db"):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    @contextmanager
    def transaction(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def close(self):
        self.conn.close()


class UserRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, username: str, salt: str, password_hash: str, role: Role):
        with self.db.transaction() as c:
            c.execute("INSERT INTO staff_users VALUES (?,?,?,?)",
                      (username, salt, password_hash, role.value))

    def count(self) -> int:
        return self.db.conn.execute("SELECT COUNT(*) FROM staff_users").fetchone()[0]

    def find(self, username: str) -> Optional[dict]:
        row = self.db.conn.execute(
            "SELECT * FROM staff_users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None


class PatientRepository:
    SORT_COLUMNS = {"id": "patient_id", "name": "name COLLATE NOCASE"}

    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _to_patient(r) -> Patient:
        return Patient(r["patient_id"], r["name"], r["date_of_birth"], r["phone"], r["address"])

    def add(self, name, dob, phone, address) -> Patient:
        with self.db.transaction() as c:
            cur = c.execute(
                "INSERT INTO patients (name, date_of_birth, phone, address) VALUES (?,?,?,?)",
                (name, dob, phone, address))
            return Patient(cur.lastrowid, name, dob, phone, address)

    def find_by_id(self, patient_id: int) -> Optional[Patient]:
        r = self.db.conn.execute(
            "SELECT * FROM patients WHERE patient_id = ?", (patient_id,)).fetchone()
        return self._to_patient(r) if r else None

    def list_all(self, sort_by: str = "id") -> List[Patient]:
        col = self.SORT_COLUMNS.get(sort_by, "patient_id")
        rows = self.db.conn.execute(f"SELECT * FROM patients ORDER BY {col}").fetchall()
        return [self._to_patient(r) for r in rows]

    def update(self, patient_id: int, changes: Dict[str, Optional[str]], changed_by: str):
        """Apply changes and record old/new value + timestamp atomically."""
        current = self.find_by_id(patient_id)
        with self.db.transaction() as c:
            for field, new in changes.items():
                old = getattr(current, field)
                if old == new:
                    continue
                c.execute(f"UPDATE patients SET {field} = ? WHERE patient_id = ?",
                          (new, patient_id))
                c.execute("INSERT INTO field_change_history "
                          "(patient_id, field, old_value, new_value, changed_at, changed_by) "
                          "VALUES (?,?,?,?,?,?)",
                          (patient_id, field, old, new, _now(), changed_by))

    def change_history(self, patient_id: int) -> List[FieldChange]:
        rows = self.db.conn.execute(
            "SELECT * FROM field_change_history WHERE patient_id = ? ORDER BY change_id",
            (patient_id,)).fetchall()
        return [FieldChange(r["patient_id"], r["field"], r["old_value"], r["new_value"],
                            r["changed_at"], r["changed_by"]) for r in rows]


class VisitNoteRepository:
    def __init__(self, db: Database):
        self.db = db

    @staticmethod
    def _to_note(r) -> VisitNote:
        cat = Category.from_label(r["category"]) if r["category"] else None
        return VisitNote(r["note_id"], r["patient_id"], r["visit_date"], r["text"],
                         cat, r["confirmed_by"], r["created_at"])

    def add(self, patient_id, visit_date, text, category: Optional[Category],
            confirmed_by: Optional[str]) -> VisitNote:
        created = _now()
        cat = category.value if category else None
        with self.db.transaction() as c:
            cur = c.execute(
                "INSERT INTO visit_notes (patient_id, visit_date, text, category, "
                "confirmed_by, created_at) VALUES (?,?,?,?,?,?)",
                (patient_id, visit_date, text, cat, confirmed_by, created))
            return VisitNote(cur.lastrowid, patient_id, visit_date, text, category,
                             confirmed_by, created)

    def list_for_patient(self, patient_id, category=None, uncategorized=False,
                         start=None, end=None) -> List[VisitNote]:
        sql, args = "SELECT * FROM visit_notes WHERE patient_id = ?", [patient_id]
        if category:
            sql += " AND category = ?"; args.append(category.value)
        elif uncategorized:
            sql += " AND category IS NULL"
        if start:
            sql += " AND visit_date >= ?"; args.append(start)
        if end:
            sql += " AND visit_date <= ?"; args.append(end)
        sql += " ORDER BY visit_date DESC, note_id DESC"
        return [self._to_note(r) for r in self.db.conn.execute(sql, args).fetchall()]