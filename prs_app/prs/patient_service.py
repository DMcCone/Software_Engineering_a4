"""Patient Management package."""
from typing import List, Optional

from .auth import AuthService
from .models import Patient, Permission, PatientNotFound, ValidationError, parse_date, FieldChange
from .persistence import PatientRepository

EDITABLE_FIELDS = ("name", "date_of_birth", "phone", "address")   # FR-04


class PatientService:
    def __init__(self, repo: PatientRepository, auth: AuthService):
        self.repo, self.auth = repo, auth

    @staticmethod
    def _clean(field: str, value: Optional[str]) -> Optional[str]:
        value = value.strip() if value else None
        if field == "name" and not value:
            raise ValidationError("Patient name is required")        # FR-01
        if field == "date_of_birth" and value:
            parse_date(value, "Date of birth")
        return value or None

    def create_patient(self, token, name, date_of_birth=None, phone=None, address=None) -> Patient:
        self.auth.authorize(token, Permission.PATIENT_CREATE)
        vals = {f: self._clean(f, v) for f, v in
                zip(EDITABLE_FIELDS, (name, date_of_birth, phone, address))}
        return self.repo.add(**{"name": vals["name"], "dob": vals["date_of_birth"],
                                "phone": vals["phone"], "address": vals["address"]})  # FR-02

    def get_patient(self, token, patient_id) -> Patient:
        self.auth.authorize(token, Permission.PATIENT_READ)
        p = self.repo.find_by_id(self._to_id(patient_id))
        if p is None:
            raise PatientNotFound(patient_id)                          # FR-03
        return p

    def update_patient(self, token, target_id, /, **changes) -> Patient:
        user = self.auth.authorize(token, Permission.PATIENT_UPDATE)
        bad = set(changes) - set(EDITABLE_FIELDS)
        if bad:
            raise ValidationError(f"Field(s) not editable: {', '.join(sorted(bad))}")
        patient = self.get_patient(token, target_id)                   # existence check
        cleaned = {f: self._clean(f, v) for f, v in changes.items()}
        self.repo.update(patient.patient_id, cleaned, user.username)
        return self.get_patient(token, target_id)

    def list_patients(self, token, sort_by="id") -> List[Patient]:     # FR-08
        self.auth.authorize(token, Permission.PATIENT_READ)
        return self.repo.list_all(sort_by)

    def change_history(self, token, patient_id) -> List[FieldChange]:
        self.get_patient(token, patient_id)
        return self.repo.change_history(int(patient_id))

    @staticmethod
    def _to_id(patient_id) -> int:
        try:
            return int(str(patient_id).strip())
        except ValueError:
            return -1   # never matches -> PatientNotFound