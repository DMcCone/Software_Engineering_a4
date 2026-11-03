import os, sqlite3, sys, tempfile, time, unittest
from datetime import datetime, timedelta, date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from prs.app import PRSApplication
from prs.ai_integration import (CategorizationService, Classifier, KeywordFallbackClassifier,
                                RawPrediction, SuggestionStatus)
from prs.models import *


class Fixed(Classifier):
    def __init__(self, label, conf): self.l, self.c = label, conf
    def classify(self, t): return RawPrediction(self.l, self.c)

class Boom(Classifier):
    def classify(self, t): raise RuntimeError("down")

class Slow(Classifier):
    def classify(self, t): time.sleep(1); return RawPrediction("Respiratory", 0.99)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "t.db")
        self.now = datetime(2026, 1, 1, 9, 0)
        self.make()
    def make(self, clf=None):
        self.app = PRSApplication(self.path, clf or KeywordFallbackClassifier(), lambda: self.now)
        if not self.app.users.find("nurse"):
            self.app.auth.create_user("nurse", "pw", Role.CLINICIAN)
            self.app.auth.create_user("viewer", "pw", Role.VIEWER)
        self.t = self.app.auth.login("nurse", "pw")
    def tearDown(self): self.app.close(); self.dir.cleanup()


class PatientTests(Base):
    def test_create_requires_name(self):
        with self.assertRaises(ValidationError): self.app.patients.create_patient(self.t, "  ")
    def test_ids_unique(self):
        ids = {self.app.patients.create_patient(self.t, f"P{i}").patient_id for i in range(100)}
        self.assertEqual(len(ids), 100)
    def test_id_immutable_in_db(self):
        p = self.app.patients.create_patient(self.t, "A")
        with self.assertRaises(sqlite3.DatabaseError):
            self.app.db.conn.execute("UPDATE patients SET patient_id=99 WHERE patient_id=?", (p.patient_id,))
    def test_not_found_message(self):
        with self.assertRaises(PatientNotFound) as c: self.app.patients.get_patient(self.t, "99999")
        self.assertEqual(str(c.exception), "No patient with ID: 99999")
    def test_update_records_history_and_blocks_id(self):
        p = self.app.patients.create_patient(self.t, "Old", phone="1")
        self.app.patients.update_patient(self.t, p.patient_id, name="New")
        h = self.app.patients.change_history(self.t, p.patient_id)
        self.assertEqual((h[0].field, h[0].old_value, h[0].new_value), ("name", "Old", "New"))
        with self.assertRaises(ValidationError):
            self.app.patients.update_patient(self.t, p.patient_id, patient_id=5)
    def test_sort_by_name(self):
        for n in ("Zed", "amy", "Bob"): self.app.patients.create_patient(self.t, n)
        self.assertEqual([p.name for p in self.app.patients.list_patients(self.t, "name")], ["amy", "Bob", "Zed"])
    def test_persists_after_restart(self):
        p = self.app.patients.create_patient(self.t, "Persist")
        self.app.close(); self.make()
        self.assertEqual(self.app.patients.get_patient(self.t, p.patient_id).name, "Persist")


class VisitTests(Base):
    def setUp(self):
        super().setUp()
        self.a = self.app.patients.create_patient(self.t, "A").patient_id
        self.b = self.app.patients.create_patient(self.t, "B").patient_id
    def test_note_requires_existing_patient(self):
        with self.assertRaises(PatientNotFound): self.app.visits.add_visit_note(self.t, 999, "2025-01-01", "x")
    def test_validation(self):
        v = self.app.visits
        with self.assertRaises(ValidationError): v.add_visit_note(self.t, self.a, "2025-01-01", " ")
        with self.assertRaises(ValidationError): v.add_visit_note(self.t, self.a, "", "x")
        future = (date.today() + timedelta(days=1)).isoformat()
        with self.assertRaises(ValidationError): v.add_visit_note(self.t, self.a, future, "x")
        self.assertEqual(v.get_history(self.t, self.a), [])
    def test_history_order_and_isolation(self):
        v = self.app.visits
        for d in ("2025-01-01", "2025-03-01", "2025-02-01"): v.add_visit_note(self.t, self.a, d, "n")
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
        with self.assertRaises(sqlite3.DatabaseError):
            self.app.db.conn.execute("UPDATE visit_notes SET text='y' WHERE note_id=?", (n.note_id,))
        with self.assertRaises(sqlite3.DatabaseError):
            self.app.db.conn.execute("DELETE FROM visit_notes WHERE note_id=?", (n.note_id,))
    def test_category_stored_only_when_staff_chooses(self):
        res = self.app.visits.request_suggestion(self.t, self.a, "persistent cough and wheezing")
        self.assertEqual(res.category, Category.RESPIRATORY)
        n = self.app.visits.add_visit_note(self.t, self.a, "2025-01-01", "persistent cough and wheezing")  # rejected
        self.assertIsNone(n.category)
        n2 = self.app.visits.add_visit_note(self.t, self.a, "2025-01-02", "x", res.category)             # accepted
        self.assertEqual((n2.category, n2.confirmed_by), (Category.RESPIRATORY, "nurse"))


class AITests(unittest.TestCase):
    def test_suggested(self):
        r = CategorizationService(Fixed("Digestive", 0.9)).suggest("x")
        self.assertEqual((r.status, r.category), (SuggestionStatus.SUGGESTED, Category.DIGESTIVE))
    def test_low_confidence(self):
        r = CategorizationService(Fixed("General/Other", 0.55)).suggest("Patient feels off")
        self.assertEqual(r.status, SuggestionStatus.NO_SUGGESTION)
        self.assertEqual(r.message, "No AI suggestion: choose a category or leave Uncategorized")
    def test_invalid_category(self):
        self.assertEqual(CategorizationService(Fixed("Cardiac", 0.99)).suggest("x").status,
                         SuggestionStatus.NO_SUGGESTION)
    def test_error_and_timeout(self):
        self.assertEqual(CategorizationService(Boom()).suggest("x").status, SuggestionStatus.UNAVAILABLE)
        self.assertEqual(CategorizationService(Slow(), timeout=0.1).suggest("x").status, SuggestionStatus.UNAVAILABLE)


class AIOutageDoesNotBlock(Base):
    def test_everything_works_when_ai_down(self):
        self.app.close(); self.make(Boom())
        pid = self.app.patients.create_patient(self.t, "A").patient_id
        res = self.app.visits.request_suggestion(self.t, pid, "cough")
        self.assertEqual(res.message, "AI suggestion unavailable")
        self.app.visits.add_visit_note(self.t, pid, "2025-01-01", "cough")
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


if __name__ == "__main__":
    unittest.main()