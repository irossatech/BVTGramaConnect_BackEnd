# The API and who uses it

`docs/api-contract.md` in this repo is the single source of truth for every endpoint, object, error code and permission rule. Read the section you need; do not copy it elsewhere.

The live schema is at `/api/schema/` and Swagger UI at `/api/docs/` on any running server.

## Changing the API

1. Update `docs/api-contract.md` first: path, request, response, errors, permissions.
2. Change the code and add tests in the same pull request.
3. Tell the teams that use the endpoint (table below) before merging into `dev`. Additive changes (a new optional field) are safe; renaming or removing a field needs a date agreed with each client team.
4. Keep `/api/v1/` stable. A breaking change that clients cannot follow together goes under `/api/v2/`.

## Who calls what

| Client | Endpoints |
|---|---|
| Public website (BVTGramaConnect, `src/`) | `public/site-config/`, `public/showcase/` (live posts in the phone picture), `public/contact-messages/` |
| Super Admin console (BVTGramaConnect, `console/`) | `auth/login/`, `auth/refresh/`, `auth/logout/`, `auth/password/change/`, `me/`, `admin/dashboard/`, `admin/reports/*`, `geo/*` including writes, `geo/tree/` and `geo/villages/import/`, `users/*` including reset-password and role-assignments, `posts/*` and review, `categories/`, `admin/categories/`, `admin/settings/`, `admin/role-passwords/`, `admin/notification-templates/`, `admin/contact-messages/` (list and mark handled), `audit/`, `audit/export/` |
| Mobile app (BVTGramaConnect_Mobile) | `auth/*`, `me/`, `geo/states/`, `geo/districts/`, `geo/mandals/`, `geo/villages/`, `geo/nearest/`, `feed/` (with `q` and `has_video`), `feed/posts/*`, `feed/upcoming/`, `feed/urgent/`, `public/site-config/` (help lines and the website link for sharing), `categories/`, `posts/*` and media (photos or one video), `posts/targets/`, `posts/{id}/urgent/`, `review/queue/`, `posts/{id}/review/`, `stats/`, `users/*`, `devices/`, `notifications/*`; Super Admin screens also use `admin/dashboard/` and `audit/` |

All paths are under `/api/v1/`. The console is served from the same origin, so it needs no CORS; the mobile app calls the absolute URL.

## Authentication in one paragraph

`POST auth/login/` with username (or email) and password returns an access token (15 minutes) and a refresh token (30 days, rotated). The console passes `"client": "web_admin"`, only Super Admins get in, and its refresh token goes in the HttpOnly `gc_refresh` cookie. While `must_change_password` is true, every endpoint except `me/`, `auth/password/change/`, `auth/refresh/` and `auth/logout/` answers 403 `password_change_required`. Blocking a user or changing a password bumps the token version, so old tokens fail at once with 401 `session_revoked`.
