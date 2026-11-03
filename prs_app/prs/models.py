"""Domain model: exceptions, enums and entities shared by every package.

Contains no database, network or UI logic.
"""
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Callable, Optional

# A clock is any zero-argument callable returning the current datetime.
# Every component that needs "now" receives one, so time is injectable/testable.
Clock = Callable[[], datetime]


# ---- exceptions ---------------------------------------------------------
class PRSError(Exception):
    """Base class for all expected PRS errors (caught and shown by the UI)."""


class ValidationError(PRSError):
    """Input failed a business rule."""


class PatientNotFound(PRSError):
    def __init__(self, patient_id):
        super().__init__(f"No patient with ID: {patient_id}")
        self.patient_id = patient_id


class AuthenticationError(PRSError):
    """Not logged in, bad credentials, or session no longer valid."""


class SessionExpired(AuthenticationError):
    """Session exceeded the idle timeout."""


class PermissionDenied(PRSError):
    """Logged in, but the role lacks the required permission."""


# ---- enums --------------------------------------------------------------
class Category(Enum):
    RESPIRATORY = "Respiratory"
    DIGESTIVE = "Digestive"
    MUSCULOSKELETAL = "Musculoskeletal"
    GENERAL_OTHER = "General/Other"

    @classmethod
    def from_label(cls, label: str) -> "Category":
        """Convert text to a Category; raises ValidationError if it is not one of the four."""
        wanted = str(label).strip().lower()
        for category in cls:
            if category.value.lower() == wanted:
                return category
        raise ValidationError(f"Not a valid category: {label!r}")


class SuggestionOutcome(Enum):
    """What happened with the AI for one saved note (lets us audit AI behaviour)."""
    ACCEPTED = "accepted"              # AI suggested; staff accepted
    REJECTED = "rejected"              # AI suggested; staff rejected
    NO_SUGGESTION = "no_suggestion"    # AI was uncertain / invalid output
    UNAVAILABLE = "unavailable"        # AI errored or timed out
    NOT_REQUESTED = "not_requested"    # no AI call was made (also used for V1 notes)


class Role(Enum):
    ADMIN = "admin"
    CLINICIAN = "clinician"
    VIEWER = "viewer"


class Permission(Enum):
    PATIENT_READ = "patient.read"
    PATIENT_CREATE = "patient.create"
    PATIENT_UPDATE = "patient.update"
    NOTE_READ = "note.read"
    NOTE_ADD = "note.add"
    USER_MANAGE = "user.manage"


ROLE_PERMISSIONS = {
    Role.ADMIN: set(Permission),
    Role.CLINICIAN: set(Permission) - {Permission.USER_MANAGE},
    Role.VIEWER: {Permission.PATIENT_READ, Permission.NOTE_READ},
}


# ---- entities (immutable) -----------------------------------------------
@dataclass(frozen=True)
class Patient:
    patient_id: int
    name: str
    date_of_birth: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None


@dataclass(frozen=True)
class VisitNote:
    note_id: int
    patient_id: int
    visit_date: str
    text: str
    category: Optional[Category]            # final, staff-confirmed; None == Uncategorized
    confirmed_by: Optional[str]             # staff username who accepted/chose the category
    created_at: str
    suggested_category: Optional[Category] = None
    suggestion_status: SuggestionOutcome = SuggestionOutcome.NOT_REQUESTED


@dataclass(frozen=True)
class StaffUser:
    username: str
    role: Role


@dataclass(frozen=True)
class FieldChange:
    patient_id: int
    field: str
    old_value: Optional[str]
    new_value: Optional[str]
    changed_at: str
    changed_by: str


def parse_date(value: str, label: str = "date") -> date:
    """Parse a YYYY-MM-DD string or raise ValidationError."""
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(f"{label} must be in YYYY-MM-DD format")