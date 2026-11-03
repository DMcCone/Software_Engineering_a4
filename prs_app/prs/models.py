"""Domain model: entities, enums and exceptions shared by every package."""
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional


class Category(Enum):
    RESPIRATORY = "Respiratory"
    DIGESTIVE = "Digestive"
    MUSCULOSKELETAL = "Musculoskeletal"
    GENERAL_OTHER = "General/Other"

    @classmethod
    def from_label(cls, label: str) -> "Category":
        for c in cls:
            if c.value.lower() == str(label).strip().lower():
                return c
        raise ValueError(f"Not a valid category: {label!r}")


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


ROLE_PERMISSIONS = {
    Role.ADMIN: set(Permission),
    Role.CLINICIAN: set(Permission),
    Role.VIEWER: {Permission.PATIENT_READ, Permission.NOTE_READ},
}


# ---- exceptions ---------------------------------------------------------
class PRSError(Exception):
    """Base class for all expected PRS errors."""

class ValidationError(PRSError): ...
class PatientNotFound(PRSError):
    def __init__(self, patient_id):
        super().__init__(f"No patient with ID: {patient_id}")
        self.patient_id = patient_id
class AuthenticationError(PRSError): ...
class SessionExpired(AuthenticationError): ...
class PermissionDenied(PRSError): ...


# ---- entities -----------------------------------------------------------
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
    category: Optional[Category]          # None == Uncategorized
    confirmed_by: Optional[str]           # staff username who accepted/chose it
    created_at: str


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
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(f"{label} must be in YYYY-MM-DD format")