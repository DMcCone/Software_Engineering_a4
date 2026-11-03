# Patient Record System (PRS) – Version 2

Python 3.9+ OOP implementation, standard library only (SQLite + command-line interface). Synthetic data only.
See **CHANGES.md** for everything that changed from Version 1.

## Install and run
```bash
cd prs_app
python main.py --demo     # first run: creates prs.db with demo users/patients, then starts the CLI
python main.py            # later runs
python Test.py -v         # automated tests (use a temporary database, never touch prs.db)
```
Demo logins: `admin/admin123` (can create users), `nurse/nurse123` (clinical access), `viewer/viewer123` (read-only).
A Version 1 `prs.db` can be reused: it is upgraded automatically on first open.

## AI categorization
| Setting | Behaviour |
|---|---|
| `ANTHROPIC_API_KEY` set | Real Anthropic API (model via `PRS_AI_MODEL`, default `claude-haiku-5-5`) |
| no key (default) | TEMPORARY keyword fallback – **not an AI model**, labelled as such |
| `PRS_AI=off` | Simulates an outage ("AI suggestion unavailable") |
| `PRS_AI=fallback` | Force the keyword fallback |

Layout: `prs/models.py`, `persistence.py`, `auth.py`, `patient_service.py`, `visit_service.py`,
`ai_integration.py`, `app.py` (wiring), `cli.py` (UI); tests in `Test.py`.
Run everything from the `prs_app` folder (otherwise you get `ModuleNotFoundError: No module named 'prs'`).
