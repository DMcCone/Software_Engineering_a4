"""User Interface package: command-line interface. Contains no business rules."""
import getpass
from typing import Optional, Tuple

from .ai_integration import SuggestionResult, SuggestionStatus
from .app import PRSApplication
from .models import AuthenticationError, Category, PRSError

CATEGORIES = list(Category)
MENU = ("\n1 Register patient   2 Find patient   3 Update patient\n"
        "4 Add visit note     5 Visit history  6 List patients\n"
        "7 Patient change log 0 Logout")


def ask(prompt: str, default: Optional[str] = None) -> Optional[str]:
    answer = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return answer or default


def show_patient(patient) -> None:
    print(f"\n  Patient ID : {patient.patient_id}\n  Name       : {patient.name}"
          f"\n  DOB        : {patient.date_of_birth or '-'}"
          f"\n  Phone      : {patient.phone or '-'}\n  Address    : {patient.address or '-'}")


def show_notes(notes) -> None:
    if not notes:
        print("  (no visit notes)")
        return
    print(f"\n  {'Date':<11}{'Category':<17}{'AI outcome':<15}Note")
    for note in notes:
        category = note.category.value if note.category else "Uncategorized"
        print(f"  {note.visit_date:<11}{category:<17}{note.suggestion_status.value:<15}{note.text}")


def pick_category() -> Optional[Category]:
    for number, category in enumerate(CATEGORIES, 1):
        print(f"   {number}. {category.value}")
    print("   0. Leave Uncategorized")
    while True:
        choice = input("  Choose: ").strip()
        if choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(CATEGORIES):
            return CATEGORIES[int(choice) - 1]


class CLI:
    def __init__(self, app: PRSApplication):
        self._app = app
        self._token: Optional[str] = None
        self._actions = {"1": self._create, "2": self._retrieve, "3": self._update,
                         "4": self._add_note, "5": self._history, "6": self._list_patients,
                         "7": self._change_log}

    def run(self) -> None:
        if not self._login():
            return
        while True:
            print(MENU)
            choice = input("> ").strip()
            if choice == "0":
                self._app.auth.logout(self._token)
                print("Logged out.")
                return
            action = self._actions.get(choice)
            if action is None:
                continue
            try:
                action()
            except AuthenticationError as error:      # not logged in / session expired
                print(f"  ! {error}")
                return
            except PRSError as error:
                print(f"  ! {error}")

    def _login(self) -> bool:
        print("=== Patient Record System (synthetic data only) ===")
        while not self._token:
            username = input("Username: ").strip()
            if not username:
                return False
            try:
                self._token = self._app.auth.login(username, getpass.getpass("Password: "))
            except PRSError as error:
                print(f"  ! {error}")
        return True

    # ---- menu actions -------------------------------------------------
    def _create(self) -> None:
        patient = self._app.patients.create_patient(
            self._token, ask("Name"), ask("Date of birth YYYY-MM-DD (optional)"),
            ask("Phone (optional)"), ask("Address (optional)"))
        print(f"  Created. Assigned Patient ID: {patient.patient_id}")

    def _retrieve(self) -> None:
        show_patient(self._app.patients.get_patient(self._token, ask("Patient ID")))

    def _update(self) -> None:
        patient_id = ask("Patient ID")
        show_patient(self._app.patients.get_patient(self._token, patient_id))
        print("  Press Enter to keep a field unchanged.")
        changes = {f: v for f in ("name", "date_of_birth", "phone", "address")
                   if (v := ask(f"New {f}"))}
        show_patient(self._app.patients.update_patient(self._token, patient_id, **changes))

    def _add_note(self) -> None:
        patient_id = ask("Patient ID")
        self._app.patients.get_patient(self._token, patient_id)
        visit_date = ask("Visit date YYYY-MM-DD", self._app.today().isoformat())
        text = ask("Visit note")
        print("  Requesting AI suggestion (note text only is sent)...")
        suggestion = self._app.visits.request_suggestion(self._token, patient_id, text)
        final_category, accepted = self._choose_category(suggestion)
        note = self._app.visits.add_visit_note(self._token, patient_id, visit_date, text,
                                               final_category, suggestion, accepted)
        label = final_category.value if final_category else "Uncategorized"
        print(f"  Saved note #{note.note_id} as {label}.")

    @staticmethod
    def _choose_category(suggestion: SuggestionResult) -> Tuple[Optional[Category], bool]:
        """Return (final category, whether staff accepted the AI suggestion)."""
        print(f"  [{suggestion.source}]")
        if suggestion.status is not SuggestionStatus.SUGGESTED:
            print(f"  {suggestion.message}")
            return pick_category(), False
        print(f"  {suggestion.message}: {suggestion.category.value} "
              f"(confidence {suggestion.confidence:.2f})")
        if (ask("  [A]ccept or [R]eject", "R") or "R").upper().startswith("A"):
            return suggestion.category, True
        print("  Pick a category yourself:")
        return pick_category(), False

    def _history(self) -> None:
        patient_id = ask("Patient ID")
        label = ask("Filter by category (Respiratory/Digestive/Musculoskeletal/General/Other/"
                    "Uncategorized, Enter=all)")
        start, end = ask("From date (optional)"), ask("To date (optional)")
        uncategorized = bool(label) and label.lower() == "uncategorized"
        category = Category.from_label(label) if label and not uncategorized else None
        show_notes(self._app.visits.get_history(self._token, patient_id, category,
                                                uncategorized, start, end))

    def _list_patients(self) -> None:
        sort_by = "name" if ask("Sort by [id/name]", "id").lower().startswith("n") else "id"
        for patient in self._app.patients.list_patients(self._token, sort_by):
            print(f"  {patient.patient_id:<6}{patient.name}")

    def _change_log(self) -> None:
        for change in self._app.patients.change_history(self._token, ask("Patient ID")):
            print(f"  {change.changed_at}  {change.field}: {change.old_value!r} -> "
                  f"{change.new_value!r} (by {change.changed_by})")