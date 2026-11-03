"""Composition root: wires repositories and services together (dependency injection)."""
from datetime import date, datetime
from typing import Optional

from .ai_integration import CategorizationService, Classifier, default_classifier
from .auth import AuthService
from .models import Clock
from .patient_service import PatientService
from .persistence import Database, PatientRepository, UserRepository, VisitNoteRepository
from .visit_service import VisitService


class PRSApplication:
    def __init__(self, db_path: str = "prs.db", classifier: Optional[Classifier] = None,
                 clock: Clock = datetime.now):
        self._clock = clock
        self.db = Database(db_path)
        self.auth = AuthService(UserRepository(self.db), clock)
        self.categorizer = CategorizationService(classifier or default_classifier())
        self.patients = PatientService(PatientRepository(self.db, clock), self.auth)
        self.visits = VisitService(VisitNoteRepository(self.db, clock), self.patients,
                                   self.categorizer, self.auth, clock)

    def today(self) -> date:
        return self._clock().date()

    def close(self) -> None:
        self.db.close()