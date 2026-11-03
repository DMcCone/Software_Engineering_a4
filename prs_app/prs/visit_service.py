"""Visit Note Management package."""
from datetime import date
from typing import List, Optional

from .ai_integration import CategorizationService, SuggestionResult
from .auth import AuthService
from .models import Category, Permission, ValidationError, VisitNote, parse_date
from .patient_service import PatientService
from .persistence import VisitNoteRepository


class VisitService:
    def __init__(self, notes: VisitNoteRepository, patients: PatientService,
                 categorizer: CategorizationService, auth: AuthService):
        self.notes, self.patients, self.categorizer, self.auth = notes, patients, categorizer, auth

    def request_suggestion(self, token, patient_id, note_text: str) -> SuggestionResult:
        """FR-05: shown BEFORE the note is saved. Sends note text only (no identifiers)."""
        self.auth.authorize(token, Permission.NOTE_ADD)
        self.patients.get_patient(token, patient_id)
        if not note_text or not note_text.strip():
            raise ValidationError("Visit note description is required")
        return self.categorizer.suggest(note_text.strip())

    def add_visit_note(self, token, patient_id, visit_date: str, text: str,
                       final_category: Optional[Category] = None) -> VisitNote:
        """FR-06. `final_category` is the category STAFF accepted or chose (None =
        Uncategorized). The AI can never set it directly."""
        user = self.auth.authorize(token, Permission.NOTE_ADD)
        patient = self.patients.get_patient(token, patient_id)       # existing patients only
        if not text or not text.strip():
            raise ValidationError("Visit note description is required")
        if not visit_date or not str(visit_date).strip():
            raise ValidationError("Visit date is required")
        if parse_date(visit_date, "Visit date") > date.today():
            raise ValidationError("Visit date cannot be in the future")
        return self.notes.add(patient.patient_id, visit_date.strip(), text.strip(),
                              final_category, user.username if final_category else None)

    def get_history(self, token, patient_id, category: Optional[Category] = None,
                    uncategorized=False, start=None, end=None) -> List[VisitNote]:
        """FR-07 / FR-08: newest first, only this patient's notes."""
        self.auth.authorize(token, Permission.NOTE_READ)
        patient = self.patients.get_patient(token, patient_id)
        if start: parse_date(start, "Start date")
        if end: parse_date(end, "End date")
        return self.notes.list_for_patient(patient.patient_id, category, uncategorized, start, end)