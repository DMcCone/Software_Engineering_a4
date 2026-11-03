"""Entry point.  python main.py [--demo]"""
import sys
from prs.app import PRSApplication
from prs.cli import CLI
from prs.models import Role


def seed_demo(app: PRSApplication):
    """Create synthetic demo accounts and patients (only if DB has no users)."""
    if app.users.find("admin"):
        return
    for u, pw, role in (("admin", "admin123", Role.ADMIN), ("nurse", "nurse123", Role.CLINICIAN),
                        ("viewer", "viewer123", Role.VIEWER)):
        app.auth.create_user(u, pw, role)
    t = app.auth.login("admin", "admin123")
    for n, dob, ph, ad in (("Jane Testpatient", "1980-04-12", "555-0101", "1 Example St"),
                           ("John Sample", "1975-09-30", "555-0102", "2 Demo Ave"),
                           ("Alex Synthetic", "1992-01-15", "555-0103", "3 Fake Rd")):
        app.patients.create_patient(t, n, dob, ph, ad)
    app.auth.logout(t)
    print("Demo data created. Logins: admin/admin123, nurse/nurse123, viewer/viewer123")


if __name__ == "__main__":
    app = PRSApplication("prs.db")
    if "--demo" in sys.argv:
        seed_demo(app)
    if app.users.count() == 0:
        print("No users exist yet. Run:  python main.py --demo")
    else:
        from prs.ai_integration import default_classifier
        print(f"AI categorizer in use: {default_classifier().name}")
        CLI(app).run()
    app.close()