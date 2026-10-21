# BVTGramaConnect_BackEnd

Django REST API, background jobs and server deployment for BVT Grama Connect. Owned by the Backend team.

@.claude/project.md
@.claude/architecture.md
@.claude/conventions.md
@.claude/api.md

Read when needed: `.claude/operations.md` (setup, commands, env, deploy) and `.claude/backlog.md` (open work).

## Always

- Check role AND area on the server for every action; use `apps/accounts/scope.py` and `apps/geography/areas.py`.
- Change a post's status only through `apps/posts/services.py`; write audit entries only through `apps.audit.services.record`.
- Portable ORM only; tests run on SQLite and must stay green.
- Contract first: update `docs/api-contract.md` with any API change and tell the client teams.
- Never log or audit passwords, tokens, or full emails and mobile numbers.
- Never edit a merged migration.

## Commands

```
.venv\Scripts\python -m pytest -q -p no:logging          # all tests
.venv\Scripts\python manage.py makemigrations --check --dry-run
.venv\Scripts\python manage.py runserver                 # http://127.0.0.1:8000
.venv\Scripts\python manage.py qcluster                  # background jobs
```

On Linux or macOS use `.venv/bin/python`.
