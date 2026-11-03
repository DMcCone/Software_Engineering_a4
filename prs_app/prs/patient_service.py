"""Patient Management package."""
from dataclasses import replace
from typing import List, Optional

from .auth import AuthService
from .models import (FieldChange, Patient, PatientNotFound, Permission, ValidationError,
                     parse_date)
from .persistence import PatientRepository

EDITABLE_FIELDS = ("name", "date_of_birth", "phone", "address")   # FR-04


class PatientService:
    def __init__(self, repo: PatientRepository, auth: AuthService):
        self._repo = repo
        self._auth = auth

    # ---- public operations (each authorizes first) --------------------
    def create_patient(self, token: str, name: str, date_of_birth: Optional[str] = None,
                       phone: Optional[str] = None, address: Optional[str] = None) -> Patient:
        """FR-01 / FR-02: the database assigns the unique, immutable ID."""
        self._auth.authorize(token, Permission.PATIENT_CREATE)
        fields = self._clean_fields(name=name, date_of_birth=date_of_birth,
                                    phone=phone, address=address)
        return self._repo.add(**fields)

    def get_patient(self, token: str, patient_id) -> Patient:
        """FR-03: raises PatientNotFound ("No patient with ID: [ID]") if absent."""
        self._auth.authorize(token, Permission.PATIENT_READ)
        return self._find_or_raise(patient_id)

    def update_patient(self, token: str, target_id, /, **changes) -> Patient:
        """FR-04: update permitted fields only; old/new values are kept in history."""
        user = self._auth.authorize(token, Permission.PATIENT_UPDATE)
        not_editable = set(changes) - set(EDITABLE_FIELDS)
        if not_editable:
            raise ValidationError(f"Field(s) not editable: {', '.join(sorted(not_editable))}")
        current = self._find_or_raise(target_id)
        cleaned = self._clean_fields(**changes)
        self._repo.update(current, cleaned, user.username)
        return replace(current, **cleaned)          # no re-query needed

    def list_patients(self, token: str, sort_by: str = "id") -> List[Patient]:
        """FR-08: sort by 'id' or 'name'."""
        self._auth.authorize(token, Permission.PATIENT_READ)
        return self._repo.list_all(sort_by)

    def change_history(self, token: str, patient_id) -> List[FieldChange]:
        self._auth.authorize(token, Permission.PATIENT_READ)
        return self._repo.change_history(self._find_or_raise(patient_id).patient_id)

    # ---- helpers ------------------------------------------------------
    def _find_or_raise(self, patient_id) -> Patient:
        patient = self._repo.find_by_id(self._to_id(patient_id))
        if patient is None:
            raise PatientNotFound(patient_id)
        return patient

    @staticmethod
    def _to_id(patient_id) -> int:
        try:
            return int(str(patient_id).strip())
        except ValueError:
            return -1      # never matches a real ID -> PatientNotFound

    @staticmethod
    def _clean_fields(**fields: Optional[str]) -> dict:
        """Trim values, turn blanks into None, and validate name / date of birth."""
        cleaned = {}
        for field, value in fields.items():
            value = value.strip() if value else None
            if field == "name" and not value:
                raise ValidationError("Patient name is required")        # FR-01
            if field == "date_of_birth" and value:
                parse_date(value, "Date of birth")
            cleaned[field] = value or None
        return cleaned