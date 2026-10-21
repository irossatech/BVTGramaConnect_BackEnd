# Backend conventions

## Permissions

- Every write and every non-public read checks two things on the server: one of the actor's active role assignments has a group that grants the permission, and the target sits inside that assignment's area. Use `HasPerm("app_label.codename")` on views and `authz.can(user, perm, area)` in code (`apps/accounts/permissions.py`, `apps/accounts/authz.py`); never trust a client filter.
- Never test a group's name or key: ask for a permission. Only `apps/accounts/role_matrix.py`, data migrations and the sync name groups. To add a permission, follow `docs/authorization.md`, section 10.
- User actions (give, move or remove a role, block, reset, unlock) also need a group ranked strictly below the actor's: `authz.can_act_on` and `authz.can_assign`.
- Lists filter to the actor's areas in SQL (`authz.area_q`, `authz.manageable_users`, `apps.posts.services.visible_posts`). A query parameter can narrow a list, never widen it.
- An object outside the actor's reach answers **404** (`not_found`), so its existence stays hidden. Creating something in an area outside the actor's reach answers **403** `out_of_area`. An actor without the permission answers **403** `permission_denied`.
- Super Admin reach is platform-wide whatever area the assignment names.

## Repo structure

- Every Django app lives in `apps/`. To add one: `mkdir apps/<name>` then `python manage.py startapp <name> apps/<name>`; in its `apps.py` set `name = "apps.<name>"` and `label = "<name>"`; add `"apps.<name>"` to `LOCAL_APPS` in `grama/settings/base.py`.
- Import apps as `apps.<name>` (`from apps.posts.services import review`). Dotted strings that name a module (django-q task paths, gateway settings, `include()`) also start with `apps.`. Model references keep the short label (`"accounts.User"`).
- Keep the top level minimal: `apps/`, `grama/`, `deploy/`, `docs/`. Do not add top-level folders without the team's agreement. Project-level tests go in `grama/tests/`.
- One `requirements.txt` for every environment. Add new packages there, pinned. Do not create other requirements files.

## Database

- Portable ORM only: no raw SQL, no vendor-specific fields, no spatial types, no `select_for_update` as the only guard. Tests run on SQLite; the server runs MySQL (utf8mb4) or PostgreSQL.
- Never edit a migration that has been merged. Add a new one. Before every pull request run `python manage.py makemigrations --check --dry-run`.
- Seed data that must exist everywhere (categories, notification templates) goes in data migrations.

## Telugu

- Store and return Telugu as UTF-8. DRF sends raw UTF-8 JSON (`UNICODE_JSON`), which halves the bytes on 2G.
- Area and category names come in the language of `Accept-Language` (Telugu by default). CSV exports start with a UTF-8 BOM so Excel shows Telugu.
- Test with Telugu strings, not only English.

## API responses

- Errors always use `{"error": {"code", "message", "fields"?, ...}}`, produced by `grama/exceptions.py`. Raise `ApiError(code, message, status=...)` for a contract code and `AuthError` for 401s. The codes are listed in `docs/api-contract.md`, section 1.
- Objects that clients act on carry `allowed_actions`, computed on the server.
- Admin lists use page numbers (`grama/pagination.py`, at most 100); the feed and the inbox use cursors.

## Privacy and secrets

- Never write passwords, tokens or full email addresses or mobile numbers to logs, audit `data`, or django-q task arguments. A temporary password appears once, in the API response that set it, and nowhere else.
- Personal data never goes in a URL path.

## Tests

- pytest on SQLite: `python -m pytest -q -p no:logging`. Every pull request keeps the whole suite green.
- Every permission rule gets a test, including the "outside the area" case. Follow the matrix style in `apps/accounts/tests/test_matrix.py` (every group × every permission × the same area, the parent, a sibling and another district) and `apps/posts/tests/test_access.py`: work out the expected answer in the test from the rules, not by calling the code under test.
- Shared fixtures are in the root `conftest.py`: a small map (2 districts, 4 mandals, 8 villages) and one official per role and area (`world`, `api`). Put app-only fixtures in that app's `tests/conftest.py`.
- Push is checked with the `push_outbox` fixture in `apps/notifications/tests/conftest.py`.

## API changes

Contract first: change `docs/api-contract.md`, the code and the tests in one pull request, and tell the web developer and the mobile team before merging. See `.claude/api.md`.
