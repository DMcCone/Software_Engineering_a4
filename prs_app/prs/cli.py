"""User Interface package: command-line interface."""
import getpass
from datetime import date

from .app import PRSApplication
from .ai_integration import SuggestionStatus
from .models import Category, PRSError, Role

CATS = list(Category)


def ask(prompt, default=None):
    v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return v or default


def show_patient(p):
    print(f"\n  Patient ID : {p.patient_id}\n  Name       : {p.name}\n  DOB        : {p.date_of_birth or '-'}"
          f"\n  Phone      : {p.phone or '-'}\n  Address    : {p.address or '-'}")


def show_notes(notes):
    if not notes:
        print("  (no visit notes)")
        return
    print(f"\n  {'Date':<11}{'Category':<17}Note")
    for n in notes:
        print(f"  {n.visit_date:<11}{(n.category.value if n.category else 'Uncategorized'):<17}{n.text}")


def pick_category():
    for i, c in enumerate(CATS, 1):
        print(f"   {i}. {c.value}")
    print("   0. Leave Uncategorized")
    while True:
        ch = input("  Choose: ").strip()
        if ch == "0":
            return None
        if ch.isdigit() and 1 <= int(ch) <= len(CATS):
            return CATS[int(ch) - 1]


class CLI:
    def __init__(self, app: PRSApplication):
        self.app, self.token = app, None

    def login(self):
        print("=== Patient Record System (synthetic data only) ===")
        while not self.token:
            u = input("Username: ").strip()
            if not u:
                return False
            try:
                self.token = self.app.auth.login(u, getpass.getpass("Password: "))
            except PRSError as e:
                print(f"  ! {e}")
        return True

    def run(self):
        if not self.login():
            return
        actions = {"1": self.create, "2": self.retrieve, "3": self.update, "4": self.add_note,
                   "5": self.history, "6": self.list_patients, "7": self.change_log}
        while True:
            print("\n1 Register patient   2 Find patient   3 Update patient\n"
                  "4 Add visit note     5 Visit history  6 List patients\n"
                  "7 Patient change log 0 Logout")
            ch = input("> ").strip()
            if ch == "0":
                self.app.auth.logout(self.token)
                print("Logged out.")
                return
            if ch not in actions:
                continue
            try:
                actions[ch]()
            except PRSError as e:
                print(f"  ! {e}")
                if "Session expired" in str(e) or "Not logged in" in str(e):
                    return

    def create(self):
        p = self.app.patients.create_patient(
            self.token, ask("Name"), ask("Date of birth YYYY-MM-DD (optional)"),
            ask("Phone (optional)"), ask("Address (optional)"))
        print(f"  Created. Assigned Patient ID: {p.patient_id}")

    def retrieve(self):
        show_patient(self.app.patients.get_patient(self.token, ask("Patient ID")))

    def update(self):
        pid = ask("Patient ID")
        show_patient(self.app.patients.get_patient(self.token, pid))
        print("  Press Enter to keep a field unchanged.")
        changes = {}
        for f in ("name", "date_of_birth", "phone", "address"):
            v = ask(f"New {f}")
            if v:
                changes[f] = v
        show_patient(self.app.patients.update_patient(self.token, pid, **changes))

    def add_note(self):
        pid = ask("Patient ID")
        self.app.patients.get_patient(self.token, pid)
        vdate = ask("Visit date YYYY-MM-DD", date.today().isoformat())
        text = ask("Visit note")
        print("  Requesting AI suggestion (note text only is sent)...")
        res = self.app.visits.request_suggestion(self.token, pid, text)
        print(f"  [{res.source}]")
        if res.status is SuggestionStatus.SUGGESTED:
            print(f"  {res.message}: {res.category.value} (confidence {res.confidence:.2f})")
            if (ask("  [A]ccept or [R]eject", "R") or "R").upper().startswith("A"):
                final = res.category
            else:
                print("  Pick a category yourself:")
                final = pick_category()
        else:
            print(f"  {res.message}")
            final = pick_category()
        n = self.app.visits.add_visit_note(self.token, pid, vdate, text, final)
        print(f"  Saved note #{n.note_id} as {final.value if final else 'Uncategorized'}.")

    def history(self):
        pid = ask("Patient ID")
        cat_label = ask("Filter by category (Respiratory/Digestive/Musculoskeletal/General/Other/Uncategorized, Enter=all)")
        start, end = ask("From date (optional)"), ask("To date (optional)")
        cat, unc = None, False
        if cat_label:
            if cat_label.lower() == "uncategorized":
                unc = True
            else:
                cat = Category.from_label(cat_label) if cat_label else None
        show_notes(self.app.visits.get_history(self.token, pid, cat, unc, start, end))

    def list_patients(self):
        sort = "name" if (ask("Sort by [id/name]", "id") or "id").startswith("n") else "id"
        for p in self.app.patients.list_patients(self.token, sort):
            print(f"  {p.patient_id:<6}{p.name}")

    def change_log(self):
        for c in self.app.patients.change_history(self.token, ask("Patient ID")) or []:
            print(f"  {c.changed_at}  {c.field}: {c.old_value!r} -> {c.new_value!r} (by {c.changed_by})")