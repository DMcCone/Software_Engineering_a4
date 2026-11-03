# Version 1 → Version 2: change log

Scope rule followed: **no new features**. Every change fixes a weakness found in the Version 1
code review, or is a behaviour-preserving refactor. Version 2 passes 41 automated tests
(Version 1 had 23); behaviour required by FR-01…FR-08, NFR-02/03 and AIR-01/02 is unchanged.

## A. Weaknesses fixed (from the code review)

| # | Weakness in V1 | Fix in V2 | Files | Requirement / design link |
|---|---|---|---|---|
| 1 | `AuthService.create_user()` took no token and checked nothing, so any code holding an `AuthService` could create an admin. ADMIN and CLINICIAN had identical permissions. | Added `Permission.USER_MANAGE` (ADMIN only; CLINICIAN is now `set(Permission) - {USER_MANAGE}`). `create_user(token, ...)` calls `authorize()` first. New `bootstrap_admin()` creates the first account only while no users exist (needed so seeding still works). New `has_users()`. Duplicate/blank usernames now raise `ValidationError` instead of a raw `sqlite3.IntegrityError`. | `models.py`, `auth.py`, `main.py` | BR-01, NFR-02, HIPAA rule |
| 2a | `VisitService` repeated the "description must be non-empty" check in `request_suggestion` and `add_visit_note`. | One helper `_require_text()` used by both (also returns the stripped text). The date checks moved into `_validate_visit_date()`. | `visit_service.py` | FR-06 |
| 2b | `CategorizationService.suggest()` caught `FutureTimeout` and `Exception` separately, but a timeout is already an `Exception`. It also had four near-identical `SuggestionResult(...)` constructions. | Single `except Exception` (comment notes it covers timeouts). Added `_result()` helper, `_call_classifier()` for the thread/timeout logic, and a message constant `MSG_SUGGESTED`. Confidence check simplified to `not threshold <= confidence <= 1.0`. | `ai_integration.py` | NFR-03, AIR-01 |
| 3a | `Category.from_label()` raised plain `ValueError`, which the CLI's `except PRSError` did not catch, so typing a bad category in the history filter crashed the program. | Now raises `ValidationError` (a `PRSError`). Because `suggest()` used to catch `ValueError` for this, it now catches `(ValidationError, TypeError, ValueError)`; otherwise an invalid AI category would have started raising (a regression the new tests guard against). | `models.py`, `ai_integration.py` | NFR-03, AIR-01 |
| 3b | `CLI.run()` decided to log out by searching the message text (`"Session expired" in str(e)`), which breaks if a message is reworded. | `except AuthenticationError` (covers `SessionExpired` and not-logged-in) placed **before** `except PRSError`. | `cli.py` | NFR-02 |
| 4 | An injectable clock existed in `AuthService`, but `VisitService` used `date.today()` and the repositories used their own `datetime.now()`, so time could not be controlled consistently. | `Clock` type alias in `models.py`. `PRSApplication` passes one clock to `AuthService`, both repositories (timestamps) and `VisitService` (future-date check). Added `PRSApplication.today()` so the CLI's default visit date uses it too. | `models.py`, `persistence.py`, `visit_service.py`, `app.py`, `cli.py` | FR-04, FR-06, NFR-02 |
| 5 | A note saved while the AI was down looked identical to one where staff rejected a suggestion, so AI quality could not be audited. | New columns `suggested_category` and `suggestion_status` on `visit_notes`, new enum `SuggestionOutcome` (accepted / rejected / no_suggestion / unavailable / not_requested). `add_visit_note()` takes the shown `suggestion` and an explicit `suggestion_accepted` flag. `_resolve_outcome()` also enforces AIR-02: an accepted category must equal the AI's suggestion, and you cannot "accept" when nothing was suggested. Existing V1 databases are upgraded in place (`Database._migrate()`), old notes read as `not_requested`. History screen shows an "AI outcome" column. | `models.py`, `persistence.py`, `visit_service.py`, `cli.py` | AIR-02, UR-03, Part 4 evaluation/monitoring |

## B. Refactoring and OOP improvements (behaviour unchanged)

| Area | Change |
|---|---|
| Encapsulation | `Database.conn` is now private (`_conn`); repositories use `fetch_one()`, `fetch_all()`, `transaction()`. Service and repository attributes are private (`_repo`, `_auth`, `_notes` …). `PRSApplication` no longer exposes the user repository; `main.py` uses `auth.has_users()`. |
| Class responsibilities | `AuthService.authorize()` split into `_live_session()` (existence + idle timeout) and the permission check; `login()` uses `_password_matches()`. `CategorizationService` exposes `source_name` instead of callers reaching into the classifier. |
| Simplification | `PatientService.create_patient` no longer rebuilds a dict through `zip`; both create and update share `_clean_fields()`. `list_for_patient()` builds its WHERE clause from a conditions list. `CLI` split into `_choose_category()`, a menu constant, and an actions dict built once in `__init__`. Removed redundant code in the history filter and `change_log`. |
| Exception order | `models.py` now defines exceptions first so the file reads top-down. |

## C. Optimization

| Change | Why |
|---|---|
| `update_patient` now does **1** patient lookup (V1: 3) and returns `dataclasses.replace(current, **cleaned)`; the repository receives the already-loaded patient instead of re-querying. | Removes two redundant queries and a duplicate `authorize()` per update. |
| Indexes `idx_notes_patient_date (patient_id, visit_date)` and `idx_changes_patient (patient_id)`. | History and change-log queries filter by patient and sort by date; helps at the 50,000-patient scale (NFR-01). |
| `main.py` reads the AI name from the running app instead of creating a second classifier object. | Removes an unnecessary object. |

## D. Programming standards
Consistent `Optional[...]` type hints, docstrings on every class and public method, named constant for
PBKDF2 iterations, `main()` function with `try/finally` so the database always closes, and PEP 8 naming
(private helpers prefixed `_`).

## E. API changes to be aware of
- `AuthService.create_user(token, username, password, role)` (token added); first account via `bootstrap_admin()`.
- `VisitService.add_visit_note(..., final_category=None, suggestion=None, suggestion_accepted=False)`.
- `PatientRepository.update(current_patient, changes, changed_by)` and repository constructors take a `clock`.
- `PRSApplication` no longer has a public `users` attribute.

## F. Not changed (intentionally)
Architecture and package layout, all FR/NFR/AIR behaviour and messages, the keyword fallback (still labelled
as not an AI model), CLI menu, database triggers. Known V1 limitations remain: CLI only, in-memory sessions,
no load test, no user-management screen.
