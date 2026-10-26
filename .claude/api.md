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
| Super Admin console (BVTGramaConnect, `console/`) | `auth/csrf/`, `auth/session/login/`, `auth/session/logout/`, `auth/password/change/`, `me/`, `admin/dashboard/`, `admin/reports/*`, `geo/*` including writes, `geo/tree/` and `geo/villages/import/`, `users/*` including reset-password, unlock and role-assignments, `posts/*` and review, `categories/`, `admin/categories/`, `admin/settings/`, `admin/notification-templates/`, `admin/contact-messages/` (list and mark handled), `admin/contacts/*` (important numbers, import), `admin/contact-types/`, `audit/`, `audit/export/` |
| Mobile app (BVTGramaConnect_Mobile) | `auth/login/`, `auth/refresh/`, `auth/logout/`, `auth/password/change/`, `me/`, `geo/states/`, `geo/districts/`, `geo/mandals/`, `geo/villages/`, `geo/nearest/`, `feed/` (with `q` and `has_video`), `feed/posts/*`, `feed/upcoming/`, `feed/urgent/`, `public/site-config/` (help lines and the website link for sharing), `categories/`, `posts/*` and media (photos or one video), `posts/targets/`, `posts/{id}/urgent/`, `review/queue/`, `posts/{id}/review/`, `stats/`, `users/*`, `devices/`, `notifications/*`, `contacts/` and `contact-types/` (the citizen Contacts tab), `admin/contacts/*` (every official's important numbers, and `targets/`); Super Admin screens also use `admin/dashboard/` and `audit/` |

All paths are under `/api/v1/`. The console is served from the same origin, so it needs no CORS; the mobile app calls the absolute URL.

## Authentication in one paragraph

Officials log in with a username and a password; there is no OTP, SMS or email. The app calls `POST auth/login/` and gets an access token (15 minutes) and a refresh token (30 days, rotated and blacklisted). The console calls `GET auth/csrf/`, then `POST auth/session/login/`, and from then on sends the session cookie plus `X-CSRFToken`; only users with `web_admin.access_web_console` get a session, which ends after 30 idle minutes. Five failed logins for one username from one IP address lock it for 15 minutes (429 `locked_out`). While `must_change_password` is true, every endpoint except `me/`, `auth/password/change/`, the logouts and `auth/refresh/` answers 403 `password_change_required`. Blocking, a reset or a password change bumps the user's token version, so old tokens and sessions fail at once with 401 `session_revoked`. Permissions are Django permissions such as `posts.publish_post`: `GET me/` lists the user's codenames, and `allowed_actions` on objects drives the buttons. Details: `docs/authorization.md`.
