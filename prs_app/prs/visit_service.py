"""Visit Note Management package."""
from typing import List, Optional, Tuple

from .ai_integration import CategorizationService, SuggestionResult, SuggestionStatus
from .auth import AuthService
from .models import (Category, Clock, Permission, SuggestionOutcome, ValidationError,
                     VisitNote, parse_date)
from .patient_service import PatientService
from .persistence import VisitNoteRepository

_NON_SUGGESTION_OUTCOMES = {
    SuggestionStatus.NO_SUGGESTION: SuggestionOutcome.NO_SUGGESTION,
    SuggestionStatus.UNAVAILABLE: SuggestionOutcome.UNAVAILABLE,
}


class VisitService:
    def __init__(self, notes: VisitNoteRepository, patients: PatientService,
                 categorizer: CategorizationService, auth: AuthService, clock: Clock):
        self._notes = notes
        self._patients = patients
        self._categorizer = categorizer
        self._auth = auth
        self._clock = clock

    def request_suggestion(self, token: str, patient_id, note_text: str) -> SuggestionResult:
        """FR-05: shown BEFORE the note is saved. Sends the note text only (no identifiers)."""
        self._auth.authorize(token, Permission.NOTE_ADD)
        self._patients.get_patient(token, patient_id)
        return self._categorizer.suggest(self._require_text(note_text))

    def add_visit_note(self, token: str, patient_id, visit_date: str, text: str,
                       final_category: Optional[Category] = None,
                       suggestion: Optional[SuggestionResult] = None,
                       suggestion_accepted: bool = False) -> VisitNote:
        """FR-06. `final_category` is what STAFF accepted or chose (None = Uncategorized).
        Pass the `suggestion` shown to staff so the AI outcome is recorded for auditing.
        The AI can never set a category by itself: it is stored only through staff action."""
        user = self._auth.authorize(token, Permission.NOTE_ADD)
        patient = self._patients.get_patient(token, patient_id)       # existing patients only
        text = self._require_text(text)
        self._validate_visit_date(visit_date)
        outcome, suggested = self._resolve_outcome(suggestion, suggestion_accepted, final_category)
        return self._notes.add(patient.patient_id, visit_date.strip(), text, final_category,
                               user.username if final_category else None, suggested, outcome)

    def get_history(self, token: str, patient_id, category: Optional[Category] = None,
                    uncategorized: bool = False, start: Optional[str] = None,
                    end: Optional[str] = None) -> List[VisitNote]:
        """FR-07 / FR-08: newest first, only this patient's notes, optional filters."""
        self._auth.authorize(token, Permission.NOTE_READ)
        patient = self._patients.get_patient(token, patient_id)
        if start:
            parse_date(start, "Start date")
        if end:
            parse_date(end, "End date")
        return self._notes.list_for_patient(patient.patient_id, category, uncategorized,
                                            start, end)

    # ---- helpers ------------------------------------------------------
    @staticmethod
    def _require_text(text: Optional[str]) -> str:
        """Single rule for 'note description must be non-empty' (FR-06)."""
        if not text or not text.strip():
            raise ValidationError("Visit note description is required")
        return text.strip()

    def _validate_visit_date(self, visit_date: Optional[str]) -> None:
        if not visit_date or not str(visit_date).strip():
            raise ValidationError("Visit date is required")
        if parse_date(visit_date, "Visit date") > self._clock().date():
            raise ValidationError("Visit date cannot be in the future")

    @staticmethod
    def _resolve_outcome(suggestion: Optional[SuggestionResult], accepted: bool,
                         final_category: Optional[Category]
                         ) -> Tuple[SuggestionOutcome, Optional[Category]]:
        """Work out what to record about the AI. Enforces AIR-02: only an explicit staff
        Accept of the shown category may carry the AI's category into the note."""
        was_suggested = suggestion is not None and suggestion.status is SuggestionStatus.SUGGESTED
        if accepted and not was_suggested:
            raise ValidationError("There is no AI suggestion to accept")
        if suggestion is None:
            return SuggestionOutcome.NOT_REQUESTED, None
        if not was_suggested:
            return _NON_SUGGESTION_OUTCOMES[suggestion.status], None
        if accepted:
            if final_category != suggestion.category:
                raise ValidationError("Accepted category must match the AI suggestion")
            return SuggestionOutcome.ACCEPTED, suggestion.category
        return SuggestionOutcome.REJECTED, suggestion.category