"""Composition root: wires repositories and services together."""
from .ai_integration import CategorizationService, Classifier, default_classifier
from .auth import AuthService
from .patient_service import PatientService
from .persistence import Database, PatientRepository, UserRepository, VisitNoteRepository
from .visit_service import VisitService


class PRSApplication:
    def __init__(self, db_path="prs.db", classifier: Classifier = None, clock=None):
        self.db = Database(db_path)
        users = UserRepository(self.db)
        self.auth = AuthService(users, clock) if clock else AuthService(users)
        self.categorizer = CategorizationService(classifier or default_classifier())
        self.patients = PatientService(PatientRepository(self.db), self.auth)
        self.visits = VisitService(VisitNoteRepository(self.db), self.patients,
                                   self.categorizer, self.auth)
        self.users = users

    def close(self):
        self.db.close()