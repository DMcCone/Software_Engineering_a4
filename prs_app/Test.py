import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prs.ai_integration import (CategorizationService, Classifier, KeywordFallbackClassifier,
                                RawPrediction, SuggestionResult, SuggestionStatus)
from prs.app import PRSApplication
from prs.models import *
from prs.persistence import Database, VisitNoteRepository


class Fixed(Classifier):
    def __init__(self, label, conf): self.label, self.conf = label, conf
    def classify(self, text): return RawPrediction(self.label, self.conf)

class Boom(Classifier):
    def classify(self, text): raise RuntimeError("down")

class Slow(Classifier):
    def classify(self, text): time.sleep(1); return RawPrediction("Respiratory", 0.99)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "t.db")
        self.now = datetime(2026, 1, 1, 9, 0)
        self.make()

    def make(self, classifier=None):
        self.app = PRSApplication(self.path, classifier or KeywordFallbackClassifier(),
                                  lambda: self.now)
        auth = self.app.auth
        if not auth.has_users():
            auth.bootstrap_admin("admin", "pw")
            boot = auth.login("admin", "pw")
            auth.create_user(boot, "nurse", "pw", Role.CLINICIAN)
            auth.create_user(boot, "viewer", "pw", Role.VIEWER)
        self.admin = auth.login("admin", "pw")
        self.t = auth.login("nurse", "pw")

    def tearDown(self):
        self.app.close()
        self.dir.cleanup()


class PatientTests(Base):
    def test_create_requires_name(self):
        with self.assertRaises(ValidationError):
            self.app.patients.create_patient(self.t, "  ")

    def test_ids_unique(self):
        ids = {self.app.patients.create_patient(self.t, f"P{i}").patient_id for i in range(100)}
        self.assertEqual(len(ids), 100)

    def test_id_immutable_in_db(self):
        p = self.app.patients.create_patient(self.t, "A")
        with self.assertRaises(sqlite3.DatabaseError):
            with self.app.db.transaction() as conn:
                conn.execute("UPDATE patients SET patient_id=99 WHERE patient_id=?", (p.patient_id,))

    def test_not_found_message(self):
        with self.assertRaises(PatientNotFound) as ctx:
            self.app.patients.get_patient(self.t, "99999")
        self.assertEqual(str(ctx.exception), "No patient with ID: 99999")

    def test_update_returns_new_values_and_records_history(self):
        p = self.app.patients.create_patient(self.t, "Old", phone="1")
        updated = self.app.patients.update_patient(self.t, p.patient_id, name="New")
        self.assertEqual(updated.name, "New")
        self.assertEqual(self.app.patients.get_patient(self.t, p.patient_id).name, "New")
        h = self.app.patients.change_history(self.t, p.patient_id)
        self.assertEqual((h[0].field, h[0].old_value, h[0].new_value), ("name", "Old", "New"))

    def test_update_blocks_id_and_blank_name(self):
        p = self.app.patients.create_patient(self.t, "A")
        with self.assertRaises(ValidationError):
            self.app.patients.update_patient(self.t, p.patient_id, patient_id=5)
        with self.assertRaises(ValidationError):
            self.app.patients.update_patient(self.t, p.patient_id, name=" ")

    def test_sort_by_name(self):
        for n in ("Zed", "amy", "Bob"):
            self.app.patients.create_patient(self.t, n)
        names = [p.name for p in self.app.patients.list_patients(self.t, "name")]
        self.assertEqual(names, ["amy", "Bob", "Zed"])

    def test_persists_after_restart(self):
        p = self.app.patients.create_patient(self.t, "Persist")
        self.app.close()
        self.make()
        self.assertEqual(self.app.patients.get_patient(self.t, p.patient_id).name, "Persist")


class VisitTests(Base):
    def setUp(self):
        super().setUp()
        self.a = self.app.patients.create_patient(self.t, "A").patient_id
        self.b = self.app.patients.create_patient(self.t, "B").patient_id

    def test_note_requires_existing_patient(self):
        with self.assertRaises(PatientNotFound):
            self.app.visits.add_visit_note(self.t, 999, "2025-01-01", "x")

    def test_validation(self):
        v = self.app.visits
        with self.assertRaises(ValidationError): v.add_visit_note(self.t, self.a, "2025-01-01", " ")
        with self.assertRaises(ValidationError): v.add_visit_note(self.t, self.a, "", "x")
        with self.assertRaises(ValidationError): v.add_visit_note(self.t, self.a, "2026-01-02", "x")
        self.assertEqual(v.get_history(self.t, self.a), [])

    def test_visit_date_uses_injected_clock(self):
        v = self.app.visits
        v.add_visit_note(self.t, self.a, "2026-01-01", "today is allowed")     # clock = 2026-01-01
        with self.assertRaises(ValidationError):
            v.add_visit_note(self.t, self.a, "2026-01-02", "future per the clock")

    def test_timestamps_use_injected_clock(self):
        n = self.app.visits.add_visit_note(self.t, self.a, "2025-01-01", "x")
        self.assertEqual(n.created_at, "2026-01-01T09:00:00")
        self.app.patients.update_patient(self.t, self.a, phone="5")
        self.assertEqual(self.app.patients.change_history(self.t, self.a)[0].changed_at,
                         "2026-01-01T09:00:00")

    def test_history_order_and_isolation(self):
        v = self.app.visits
        for d in ("2025-01-01", "2025-03-01", "2025-02-01"):
            v.add_visit_note(self.t, self.a, d, "n")
        v.add_visit_note(self.t, self.b, "2025-05-01", "other")
        h = v.get_history(self.t, self.a)
        self.assertEqual([n.visit_date for n in h], ["2025-03-01", "2025-02-01", "2025-01-01"])
        self.assertTrue(all(n.patient_id == self.a for n in h))

    def test_filter_category_and_dates(self):
        v = self.app.visits
        v.add_visit_note(self.t, self.a, "2025-01-01", "cough", Category.RESPIRATORY)
        v.add_visit_note(self.t, self.a, "2025-06-01", "cough", Category.RESPIRATORY)
        v.add_visit_note(self.t, self.a, "2025-06-01", "knee", Category.MUSCULOSKELETAL)
        v.add_visit_note(self.t, self.a, "2025-06-02", "misc")
        r = v.get_history(self.t, self.a, Category.RESPIRATORY, start="2025-05-01", end="2025-12-31")
        self.assertEqual(len(r), 1)
        self.assertEqual(len(v.get_history(self.t, self.a, uncategorized=True)), 1)

    def test_notes_immutable_in_db(self):
        n = self.app.visits.add_visit_note(self.t, self.a, "2025-01-01", "x")
        for sql in ("UPDATE visit_notes SET text='y' WHERE note_id=?",
                    "DELETE FROM visit_notes WHERE note_id=?"):
            with self.assertRaises(sqlite3.DatabaseError):
                with self.app.db.transaction() as conn:
                    conn.execute(sql, (n.note_id,))


class SuggestionAuditTests(Base):
    """Change 5: AI outcome is recorded so 'AI down' differs from 'staff rejected'."""
    def setUp(self):
        super().setUp()
        self.pid = self.app.patients.create_patient(self.t, "A").patient_id

    def _suggest(self, text="persistent cough and wheezing"):
        return self.app.visits.request_suggestion(self.t, self.pid, text)

    def _save(self, **kw):
        return self.app.visits.add_visit_note(self.t, self.pid, "2025-01-01", "cough wheezing", **kw)

    def test_accepted(self):
        s = self._suggest()
        n = self._save(final_category=s.category, suggestion=s, suggestion_accepted=True)
        self.assertEqual((n.category, n.suggested_category, n.suggestion_status, n.confirmed_by),
                         (Category.RESPIRATORY, Category.RESPIRATORY, SuggestionOutcome.ACCEPTED, "nurse"))

    def test_rejected_keeps_ai_suggestion_but_not_category(self):
        s = self._suggest()
        n = self._save(final_category=None, suggestion=s)
        self.assertEqual((n.category, n.suggested_category, n.suggestion_status),
                         (None, Category.RESPIRATORY, SuggestionOutcome.REJECTED))

    def test_rejected_then_manual_pick(self):
        s = self._suggest()
        n = self._save(final_category=Category.DIGESTIVE, suggestion=s)
        self.assertEqual((n.category, n.suggestion_status), (Category.DIGESTIVE, SuggestionOutcome.REJECTED))

    def test_no_suggestion(self):
        n = self._save(suggestion=self._suggest("Patient feels off"))
        self.assertEqual((n.suggested_category, n.suggestion_status), (None, SuggestionOutcome.NO_SUGGESTION))

    def test_unavailable_differs_from_rejected(self):
        self.app.close(); self.make(Boom())
        pid = self.app.patients.create_patient(self.t, "B").patient_id
        s = self.app.visits.request_suggestion(self.t, pid, "cough")
        n = self.app.visits.add_visit_note(self.t, pid, "2025-01-01", "cough", None, s)
        self.assertEqual(n.suggestion_status, SuggestionOutcome.UNAVAILABLE)

    def test_not_requested(self):
        self.assertEqual(self._save().suggestion_status, SuggestionOutcome.NOT_REQUESTED)

    def test_accept_must_match_suggestion(self):
        s = self._suggest()
        with self.assertRaises(ValidationError):
            self._save(final_category=Category.DIGESTIVE, suggestion=s, suggestion_accepted=True)
        with self.assertRaises(ValidationError):                      # nothing to accept
            self._save(final_category=Category.DIGESTIVE, suggestion_accepted=True)

    def test_outcome_persists_across_restart(self):
        s = self._suggest()
        self._save(final_category=s.category, suggestion=s, suggestion_accepted=True)
        self.app.close(); self.make()
        n = self.app.visits.get_history(self.t, self.pid)[0]
        self.assertEqual(n.suggestion_status, SuggestionOutcome.ACCEPTED)


class MigrationTests(unittest.TestCase):
    def test_v1_database_is_upgraded_in_place(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "v1.db")
            conn = sqlite3.connect(path)
            conn.executescript("""
                CREATE TABLE patients (patient_id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
                    date_of_birth TEXT, phone TEXT, address TEXT);
                CREATE TABLE visit_notes (note_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patient_id INTEGER NOT NULL, visit_date TEXT NOT NULL, text TEXT NOT NULL,
                    category TEXT, confirmed_by TEXT, created_at TEXT NOT NULL);
                INSERT INTO patients (name) VALUES ('Old');
                INSERT INTO visit_notes (patient_id, visit_date, text, category, confirmed_by, created_at)
                    VALUES (1, '2025-01-01', 'cough', 'Respiratory', 'nurse', '2025-01-01T00:00:00');""")
            conn.close()
            db = Database(path)
            note = VisitNoteRepository(db, datetime.now).list_for_patient(1)[0]
            self.assertEqual((note.category, note.suggestion_status),
                             (Category.RESPIRATORY, SuggestionOutcome.NOT_REQUESTED))
            db.close()


class ModelTests(unittest.TestCase):
    def test_from_label_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            Category.from_label("Cardiac")
        self.assertIsInstance(ValidationError("x"), PRSError)
        self.assertEqual(Category.from_label(" respiratory "), Category.RESPIRATORY)

    def test_only_admin_has_user_manage(self):
        self.assertIn(Permission.USER_MANAGE, ROLE_PERMISSIONS[Role.ADMIN])
        self.assertNotIn(Permission.USER_MANAGE, ROLE_PERMISSIONS[Role.CLINICIAN])
        self.assertNotIn(Permission.USER_MANAGE, ROLE_PERMISSIONS[Role.VIEWER])


class AITests(unittest.TestCase):
    def test_suggested(self):
        r = CategorizationService(Fixed("Digestive", 0.9)).suggest("x")
        self.assertEqual((r.status, r.category), (SuggestionStatus.SUGGESTED, Category.DIGESTIVE))
        self.assertEqual(r.message, "Suggested category (not a diagnosis)")

    def test_low_confidence(self):
        r = CategorizationService(Fixed("General/Other", 0.55)).suggest("Patient feels off")
        self.assertEqual(r.status, SuggestionStatus.NO_SUGGESTION)
        self.assertEqual(r.message, "No AI suggestion: choose a category or leave Uncategorized")

    def test_invalid_category_still_handled_after_from_label_change(self):
        self.assertEqual(CategorizationService(Fixed("Cardiac", 0.99)).suggest("x").status,
                         SuggestionStatus.NO_SUGGESTION)

    def test_bad_confidence_values(self):
        for bad in (1.5, -0.1, float("nan"), "high"):
            self.assertEqual(CategorizationService(Fixed("Digestive", bad)).suggest("x").status,
                             SuggestionStatus.NO_SUGGESTION)

    def test_error_and_timeout_both_unavailable(self):
        self.assertEqual(CategorizationService(Boom()).suggest("x").status, SuggestionStatus.UNAVAILABLE)
        self.assertEqual(CategorizationService(Slow(), timeout=0.1).suggest("x").status,
                         SuggestionStatus.UNAVAILABLE)


class AIOutageDoesNotBlock(Base):
    def test_everything_works_when_ai_down(self):
        self.app.close(); self.make(Boom())
        pid = self.app.patients.create_patient(self.t, "A").patient_id
        res = self.app.visits.request_suggestion(self.t, pid, "cough")
        self.assertEqual(res.message, "AI suggestion unavailable")
        self.app.visits.add_visit_note(self.t, pid, "2025-01-01", "cough", None, res)
        self.assertEqual(len(self.app.visits.get_history(self.t, pid)), 1)


class AuthTests(Base):
    def test_not_logged_in_denied(self):
        with self.assertRaises(AuthenticationError): self.app.patients.list_patients("bogus")

    def test_bad_password(self):
        with self.assertRaises(AuthenticationError): self.app.auth.login("nurse", "wrong")

    def test_idle_timeout(self):
        self.now += timedelta(minutes=16)
        with self.assertRaises(SessionExpired): self.app.patients.list_patients(self.t)
        with self.assertRaises(AuthenticationError): self.app.patients.list_patients(self.t)

    def test_activity_extends_session(self):
        self.now += timedelta(minutes=10); self.app.patients.list_patients(self.t)
        self.now += timedelta(minutes=10); self.app.patients.list_patients(self.t)

    def test_viewer_cannot_write(self):
        v = self.app.auth.login("viewer", "pw")
        with self.assertRaises(PermissionDenied): self.app.patients.create_patient(v, "X")
        self.app.patients.list_patients(v)


class UserManagementTests(Base):
    """Change 1: account creation is protected."""
    def test_admin_can_create_user_who_can_log_in(self):
        self.app.auth.create_user(self.admin, "newbie", "pw2", Role.CLINICIAN)
        self.assertTrue(self.app.auth.login("newbie", "pw2"))

    def test_non_admins_and_anonymous_cannot_create_users(self):
        viewer = self.app.auth.login("viewer", "pw")
        for token in (self.t, viewer):
            with self.assertRaises(PermissionDenied):
                self.app.auth.create_user(token, "evil", "pw", Role.ADMIN)
        with self.assertRaises(AuthenticationError):
            self.app.auth.create_user("no-such-token", "evil", "pw", Role.ADMIN)
        with self.assertRaises(AuthenticationError):
            self.app.auth.login("evil", "pw")

    def test_duplicate_and_blank_rejected(self):
        with self.assertRaises(ValidationError):
            self.app.auth.create_user(self.admin, "nurse", "pw", Role.VIEWER)
        with self.assertRaises(ValidationError):
            self.app.auth.create_user(self.admin, " ", "pw", Role.VIEWER)

    def test_bootstrap_only_when_no_users(self):
        with self.assertRaises(PermissionDenied):
            self.app.auth.bootstrap_admin("second", "pw")


if __name__ == "__main__":
    unittest.main()