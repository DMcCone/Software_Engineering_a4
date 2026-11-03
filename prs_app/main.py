"""Entry point.   python main.py [--demo]"""
import sys

from prs.app import PRSApplication
from prs.cli import CLI
from prs.models import Role


def seed_demo(app: PRSApplication) -> None:
    """Create synthetic demo accounts and patients (only if no users exist yet)."""
    auth = app.auth
    if auth.has_users():
        return
    auth.bootstrap_admin("admin", "admin123")
    admin = auth.login("admin", "admin123")
    auth.create_user(admin, "nurse", "nurse123", Role.CLINICIAN)
    auth.create_user(admin, "viewer", "viewer123", Role.VIEWER)
    for name, dob, phone, address in (
            ("Jane Testpatient", "1980-04-12", "555-0101", "1 Example St"),
            ("John Sample", "1975-09-30", "555-0102", "2 Demo Ave"),
            ("Alex Synthetic", "1992-01-15", "555-0103", "3 Fake Rd")):
        app.patients.create_patient(admin, name, dob, phone, address)
    auth.logout(admin)
    print("Demo data created. Logins: admin/admin123, nurse/nurse123, viewer/viewer123")


def main() -> None:
    app = PRSApplication("prs.db")
    try:
        if "--demo" in sys.argv:
            seed_demo(app)
        if not app.auth.has_users():
            print("No users exist yet. Run:  python main.py --demo")
        else:
            print(f"AI categorizer in use: {app.categorizer.source_name}")
            CLI(app).run()
    finally:
        app.close()


if __name__ == "__main__":
    main()