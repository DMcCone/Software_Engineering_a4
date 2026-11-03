# Patient Record System (PRS) – Version 1

This is the original GenAI-generated implementation, kept unchanged for Assignment 4.
The corrected and refactored code is on the `version-2` branch.

Written in Python 3.9+ using OOP and only the standard library (SQLite storage and a
command-line interface), so there is nothing to install. All patient data is synthetic.

## Install and run
```bash
git clone https://github.com/DMcCone/Software_Engineering_a4.git
cd Software_Engineering_a4
git switch main           # Version 1 (use `git switch version-2` for Version 2)
cd prs_app
python main.py --demo     # first run: creates prs.db with demo users/patients, then starts the CLI
python main.py            # later runs
python Test.py -v         # automated tests (use a temporary database, never touch prs.db)
```
Demo logins: `admin/admin123` (admin), `nurse/nurse123` (clinician), `viewer/viewer123` (read-only).

Data is saved in `prs.db` in the folder you run from, so records persist between runs.
Delete `prs.db` to start over.

## AI categorization
| Setting | Behaviour |
|---|---|
| `ANTHROPIC_API_KEY` set | Real Anthropic API (model via `PRS_AI_MODEL`, default `claude-haiku-5-5`) |
| no key (default) | TEMPORARY keyword fallback – **not an AI model**, labelled as such |
| `PRS_AI=off` | Simulates an outage ("AI suggestion unavailable"); notes can still be saved |
| `PRS_AI=fallback` | Force the keyword fallback even if a key is set |

Example: `PRS_AI=off python main.py`

## Layout
`prs_app/main.py` (entry point), `prs_app/Test.py` (tests), and the `prs_app/prs/` package:
`models.py`, `persistence.py`, `auth.py`, `patient_service.py`, `visit_service.py`,
`ai_integration.py`, `app.py` (wiring), `cli.py` (UI).
Run everything from the `prs_app` folder (otherwise you get `ModuleNotFoundError: No module named 'prs'`).
