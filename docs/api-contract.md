# BVT Grama Connect API contract, v1

Status: draft, 2026-10-21. Built from the three prototypes; see `prototype-inventory.md` in this folder.

This contract covers everything the mobile app (all roles), the public website and the Super Admin console need. Where a prototype disagrees with CLAUDE.md, this contract follows CLAUDE.md. A line marked **Pending Qn** depends on open question n in `prototype-inventory.md`, section 8. The contract is written so either answer fits without renaming endpoints.

Contents:
1. Conventions
2. Shared objects
3. accounts: auth, me, users
4. geography
5. posts: categories, posts, media, review, stats, settings
6. feed (public)
7. notifications: devices, inbox, templates, push
8. audit: audit log, dashboard, reports
9. web_public: site config, contact, showcase
10. Post state machine
11. Permission matrix
12. Screen to endpoint map
13. Limits and throttles
14. contacts: important numbers

---

## 1. Conventions

### Base URL and format
- Base path `/api/v1/`. Django serves it behind Nginx on the same origin as the website (`/`) and the console (`/console/`), so the web parts need no CORS. The mobile app calls the absolute URL.
- OpenAPI schema: `GET /api/schema/`. Swagger UI: `/api/docs/`.
- JSON in UTF-8 both ways. Telugu is sent as raw UTF-8, not `\u` escapes; a Telugu letter costs 3 bytes instead of 6, which matters on 2G. Nginx compresses JSON with gzip.
- Field names are `snake_case`. Enum values are `UPPER_CASE`, except category keys and language codes.

### Identifiers
- Users, role assignments, posts, media, review actions, notifications, devices, audit entries: UUID strings.
- States, districts, mandals, villages, categories: integers. Villages may also carry the government `lgd_code` used for master-data import.
- An area is always addressed as a pair: `level` plus `id`.

### Time
- Returned in India Standard Time (Asia/Kolkata), ISO 8601 with the offset, for example `2026-10-14T15:00:00+05:30`. Every endpoint does this, whether the time comes from a serializer or straight from a view (`grama/renderers.py`). The database stores UTC (`USE_TZ`). Clients show the time as sent; times they send may carry any offset.
- Calendar dates such as event days are `YYYY-MM-DD`.

### Language
- `Accept-Language: te` or `en`. Telugu is the default when the header is missing.
- It picks the language of server-made text: category names, area names, error messages, notification text.
- Area and category objects return `name` in the requested language. Admin endpoints also return `name_te` and `name_en` so both can be edited.
- A post is written in one language, Telugu or English, stored in `language` (owner decision Q3). `translation` is always `null`; it stays in the shape so a later version can fill it without renaming.

### Authentication
Officials log in with a **username and a password**. There is no OTP, no SMS and no email in any login or password flow. The username is unique and ignores case. Email and mobile are optional contact details and never work as a login name. Citizens never log in; every citizen read endpoint is public. `docs/authorization.md` describes the whole flow.

**Mobile app: JWT** from `djangorestframework-simplejwt`.
- **Access token:** lifetime 15 minutes. Sent as `Authorization: Bearer <access>`.
- **Refresh token:** lifetime 30 days, rotated on every use, old one blacklisted. Kept in secure storage (Android Keystore, iOS Keychain). The app calls `POST /auth/refresh/` when it gets `401 token_expired`.

**Web console: Django session** with CSRF.
- `GET /auth/csrf/` sets the readable `csrftoken` cookie. `POST /auth/session/login/` creates the session; the cookie `sessionid` is HttpOnly, SameSite=Lax and Secure on the server.
- The console sends same-origin cookies and the header `X-CSRFToken` on every POST, PATCH, PUT and DELETE. No `Authorization` header, no tokens.
- The session ends after `CONSOLE_SESSION_IDLE_MINUTES` (30) without a request: `401 not_authenticated`.
- Only users holding `web_admin.access_web_console` get a session.

**Immediate revocation.** Each user has an integer `token_version`. Every token carries it as claim `tv`, and every console session stores it at login. The server loads the user on every request and refuses a token or session whose version differs, or whose user is blocked: `401 session_revoked`. Blocking, a password change, an admin reset and "log out everywhere" bump `token_version` and blacklist the refresh tokens in one transaction. This meets the CLAUDE.md rule that blocking revokes all sessions at once.

**Permissions are read fresh.** Role assignments and group permissions are loaded on every request, so a change takes effect on the next call.

### Permissions
Roles are Django groups, and actions are Django permissions such as `posts.publish_post` (`docs/authorization.md`, sections 6 to 8). Every write and every non-public read checks two things on the server, per CLAUDE.md:
1. One of the actor's active role assignments has a group that grants the permission.
2. The target sits inside that assignment's area. The target is a post's target area, a user's assignment area, or a geography node. A Super Admin assignment covers every area.

Lists are filtered to the actor's areas on the server. A client filter can narrow a list but never widen it. Objects outside the actor's areas return `404`, not `403`, so their existence is not revealed.

Objects that clients act on carry `allowed_actions`, computed on the server for the current actor. Clients show buttons from it instead of repeating the rules.

### Pagination
- **Page numbers** for admin lists: `?page=1&page_size=20`, maximum `page_size` 100. Response:
  ```json
  {"count": 134, "next": "https://…?page=2", "previous": null, "results": []}
  ```
  Some lists add sibling keys next to `results`, such as `status_counts`. Each endpoint says so.
- **Cursors** for the citizen feed and the notification inbox, so new posts do not shift pages on a slow connection: `?cursor=<opaque>&page_size=10`, maximum 20. Response: `{"next": "<url>|null", "previous": "<url>|null", "results": []}`.

### Caching
- Feed and public post endpoints send `ETag` and `Cache-Control: public, max-age=60`. Clients send `If-None-Match` and get `304 Not Modified` with an empty body when nothing changed.
- Geography lists send `ETag` and `Cache-Control: public, max-age=600` (`GEO_PUBLIC_MAX_AGE`), on `GET` only, so a new village shows within minutes. Category lists send `max-age=3600`.
- The app keeps the last feed page on the device and shows it at once on launch, per CLAUDE.md.

### Errors
All errors share one shape:

```json
{
  "error": {
    "code": "validation_error",
    "message": "దయచేసి శీర్షిక నింపండి",
    "fields": {"title": ["ఈ ఫీల్డ్ తప్పనిసరి."]}
  }
}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `validation_error` | Bad or missing fields; `fields` lists them |
| 400 | `invalid_credentials` | Wrong username or password (same answer for an unknown username), or wrong current password on a password change |
| 401 | `not_authenticated` | No token or session, or the console session ended after 30 idle minutes |
| 401 | `token_expired` | Access token expired; refresh and retry |
| 401 | `session_revoked` | Token or session version changed (block, reset, password change elsewhere), or refresh token blacklisted |
| 403 | `account_blocked` | Blocked user tries to log in |
| 403 | `not_console_user` | Console login without `web_admin.access_web_console`; no session is created |
| 403 | `password_change_required` | The official must choose a new password first; only `GET /me/`, `POST /auth/password/change/`, `/auth/session/logout/`, `/auth/refresh/` and `/auth/logout/` work, for sessions and tokens alike |
| 403 | `permission_denied` | No group of the actor grants the permission; also a console request without a valid `X-CSRFToken` |
| 403 | `out_of_area` | Target is outside the actor's area, on writes |
| 404 | `not_found` | Missing, or outside the actor's area on reads |
| 409 | `invalid_transition` | Post state does not allow the action |
| 409 | `already_decided` | Another reviewer acted first (`REVIEW_FIRST_DECISION_WINS`); `post` included |
| 409 | `duplicate` | Username, email, mobile or name already used; `fields` says which |
| 413 | `file_too_large` | Upload over the limit |
| 415 | `unsupported_media` | File type not allowed |
| 429 | `locked_out` | 5 failed logins for this username from this IP address, or 50 failed logins from this IP address for any usernames within an hour; `retry_after_s` gives the seconds left |
| 429 | `throttled` | Rate limit; `Retry-After` header set |

### Enumerations

| Name | Values |
|---|---|
| `role` | A group's key: `SUPER_ADMIN`, `DISTRICT_ADMIN`, `MANDAL_ADMIN`, `VILLAGE_ADMIN`, `PANCHAYAT_MEMBER` (`docs/authorization.md`, section 6). Citizens have no account. `SYSTEM` appears only as an actor in history and audit. |
| `level` | `STATE`, `DISTRICT`, `MANDAL`, `VILLAGE` |
| `post.status` | `DRAFT`, `PENDING`, `PUBLISHED`, `RECHECK`, `DISCARDED`, `UNPUBLISHED`. The prototypes call `UNPUBLISHED` "Taken down". |
| `review_action.action` | `SUBMIT`, `RESUBMIT`, `PUBLISH`, `AUTO_PUBLISH`, `RECHECK`, `DISCARD`, `UNPUBLISH`, `ESCALATE`, `REVERSE` |
| `user.status` | `ACTIVE`, `BLOCKED`. Read-only, derived from `is_active`; kept for older clients. |
| `category.key` | `announce`, `schemes`, `news`, `updates`. The design-system CSS variables `--cat-<key>` depend on these keys. |
| `event.kind` | `deadline`, `camp`, `distribution`, `closure`, `meeting` (owner decision Q7) |
| `media.kind` | `image`, `video` (video posts in version 1, owner decision Q8) |
| `language` | `te`, `en` |

---

## 2. Shared objects

### Area
```json
{"level": "VILLAGE", "id": 1042, "name": "కొలకలూరు", "path": "కొలకలూరు · తెనాలి · గుంటూరు"}
```
`path` runs from the area up to the district and is in the requested language. Admin endpoints add `name_te` and `name_en`.

### Category
```json
{"key": "announce", "name": "గ్రామ చాటింపులు", "short_name": "చాటింపులు", "order": 1}
```
Admin endpoints add `id`, `name_te`, `name_en`, `short_name_te`, `short_name_en`, `is_active`, `post_count`.

### Media
```json
{"id": "6f1c…", "kind": "image", "url": "https://<site>/media/posts/2026/10/6f1c-1280.webp", "thumb_url": "https://<site>/media/posts/2026/10/6f1c-320.webp", "width": 1280, "height": 853, "bytes": 84213}
```
- Images: the server checks type and size, turns the photo upright, strips EXIF, converts to WebP and keeps three sizes: at most 1280 px, 640 px (the feed's `image_url`) and 320 px on the long side. The original is not kept. Media objects keep their shape: `url` is the 1280 px file and `thumb_url` the 320 px one. URLs are absolute.
- Video: `{"id", "kind": "video", "url", "poster_url", "thumb_url", "width", "height", "duration_s", "bytes"}`. `url` is the MP4, `poster_url` the poster frame as WebP (at most 1280 px), `thumb_url` its 320 px thumbnail, `width` and `height` the poster's size, `bytes` the video's size. Photo media carry `"poster_url": null` and `"duration_s": null`.
- `focus_y` (0 to 1) is the crop focus the prototypes use for cover images. Optional.

### RoleAssignment
```json
{"id": "a71e…", "role": "VILLAGE_ADMIN", "group": {"key": "VILLAGE_ADMIN", "name": "Village Admin"}, "area": {"level": "VILLAGE", "id": 1042, "name": "కొలకలూరు", "path": "…"}, "is_active": true, "assigned_at": "2026-10-01T10:30:00+05:30", "created_at": "2026-10-01T10:30:00+05:30"}
```
`role` repeats `group.key` for older clients.

### Me
Returned by `GET /me/`, inside the app login response and inside the console session login response.
```json
{
  "id": "0b8e…",
  "username": "village.kolakaluru",
  "name": "Suresh Babu",
  "name_te": "సురేష్ బాబు",
  "mobile": "9000000003",
  "email": null,
  "preferred_language": "te",
  "is_active": true,
  "status": "ACTIVE",
  "must_change_password": false,
  "groups": [{"key": "VILLAGE_ADMIN", "name": "Village Admin", "level": "VILLAGE", "rank": 3}],
  "assignments": [{"id": "a71e…", "group": {"key": "VILLAGE_ADMIN", "name": "Village Admin"}, "area": Area, "is_active": true}],
  "role_assignments": [RoleAssignment],
  "permissions": ["accounts.block_user", "posts.create_post", "posts.publish_post", "…"],
  "permissions_by_area": [{"area": Area, "permissions": ["posts.publish_post", "…"]}],
  "capabilities": {
    "can_post": true,
    "can_review": true,
    "can_manage_users": true,
    "manageable_roles": ["PANCHAYAT_MEMBER"],
    "is_super_admin": false
  },
  "rules": {
    "admin_posts_auto_publish": true,
    "super_admin_can_post": false,
    "max_photos_per_post": 5,
    "video_enabled": true
  }
}
```
- `permissions` are the effective `app_label.codename` strings the user holds anywhere. `permissions_by_area` says where. Clients decide which screens and buttons to show from them; the server still checks every request.
- `assignments` lists every assignment, active or not. `role_assignments` lists the active ones in the older shape.
- `capabilities` is **deprecated**. It is derived from `permissions` and kept so the current app keeps working.
- `mobile` and `email` may be `null`. When `must_change_password` is true, clients go straight to the change-password screen.

### User (admin view)
```json
{
  "id": "0b8e…",
  "username": "village.kolakaluru",
  "email": null,
  "name": "Suresh Babu",
  "name_te": "సురేష్ బాబు",
  "mobile": null,
  "is_active": true,
  "status": "ACTIVE",
  "preferred_language": "te",
  "must_change_password": true,
  "role_assignments": [RoleAssignment],
  "last_login_at": "2026-10-14T09:12:00+05:30",
  "created_at": "2026-10-01T10:30:00+05:30",
  "created_by": {"id": "…", "name": "…"},
  "locked_out": false,
  "allowed_actions": ["edit", "block", "reset_password", "reassign"]
}
```
`allowed_actions` values: `edit`, `block`, `unblock`, `reset_password`, `unlock` (only while `locked_out`), `add_assignment`, `reassign`, `remove_assignment`. Each one follows the permissions and the rank rule in section 11.

### Post (authoring and review view)
```json
{
  "id": "c2d4…",
  "status": "PENDING",
  "category": Category,
  "language": "te",
  "title": "గ్రామ సభ సమావేశం – అక్టోబర్ 20",
  "body": "…",
  "translation": null,
  "target_level": "VILLAGE",
  "target_area": Area,
  "event": {"kind": "meeting", "start_date": "2026-10-20", "end_date": null},
  "is_urgent": false,
  "media": [Media],
  "author": {"id": "…", "name": "Ravi Kumar", "role": "PANCHAYAT_MEMBER"},
  "posted_as": {"role": "PANCHAYAT_MEMBER", "area": Area},
  "created_at": "2026-10-14T09:48:00+05:30",
  "updated_at": "2026-10-14T09:48:00+05:30",
  "submitted_at": "2026-10-14T09:48:00+05:30",
  "published_at": null,
  "escalated": false,
  "escalated_at": null,
  "last_review": {"action": "RECHECK", "comment": "ఫోటో జోడించండి", "actor": {"name": "Suresh Babu", "role": "VILLAGE_ADMIN"}, "at": "…"},
  "allowed_actions": ["publish", "recheck", "discard"]
}
```
- `translation` is always `null`: a post is written in one language (owner decision Q3).
- `event`: `null` or `{"kind", "start_date", "end_date"}`. `kind` is one of `deadline`, `camp`, `distribution`, `closure`, `meeting`; dates are `YYYY-MM-DD`; `end_date` is `null` for one-day events and never before `start_date` (owner decision Q7).
- `is_urgent`: set by admins; urgent posts are pinned in the app and their push goes out at high priority (owner decision Q19).
- `posted_as` is the role assignment the author used, so admin posts show under the official role, as the website FAQ promises.
- `allowed_actions` values: `edit`, `submit`, `delete`, `add_media`, `publish`, `recheck`, `discard`, `unpublish`, `reverse`, `set_urgent`.

### FeedPost (public view)
Lean on purpose, for 2G.
```json
{
  "id": "c2d4…",
  "category": "announce",
  "language": "te",
  "title": "…",
  "body": "…",
  "translation": null,
  "area": Area,
  "published_at": "2026-10-14T10:32:00+05:30",
  "event": null,
  "is_urgent": false,
  "media": [Media],
  "source": {"role": "VILLAGE_ADMIN", "office": "కొలకలూరు గ్రామ పంచాయతీ"}
}
```
`category` is the key only; the app has the category list cached. There is no public post page: a shared post carries its text plus `website_url` from `GET /public/site-config/` (owner decision Q17). Section 6 has the exact feed shapes.

### ReviewAction (post history)
```json
{"id": "…", "action": "RECHECK", "from_status": "PENDING", "to_status": "RECHECK", "comment": "…", "actor": {"id": "…", "name": "Suresh Babu", "role": "VILLAGE_ADMIN"}, "at": "…"}
```
`actor` is `{"id": null, "name": "System", "role": "SYSTEM"}` for escalation.

### Notification (logged-in inbox)
```json
{"id": "…", "kind": "POST_RECHECK", "title": "…", "body": "…", "post_id": "c2d4…", "area": Area, "created_at": "…", "read_at": null}
```
`kind` values: `POST_PUBLISHED`, `POST_RECHECK`, `POST_DISCARDED`, `POST_UNPUBLISHED`, `REVIEW_PENDING`, `ESCALATION`, `ACCOUNT`.

### AuditEntry
```json
{"id": "…", "at": "…", "actor": {"id": "…", "name": "Suresh Babu", "role": "VILLAGE_ADMIN"}, "action": "post.published", "type": "posts", "target_label": "గ్రామ సభ సమావేశం – అక్టోబర్ 20", "target": {"type": "post", "id": "c2d4…"}, "area": Area, "via": "app"}
```
- `type` values: `posts`, `users`, `areas`, `logins`, `settings`, `system`.
- `via` values: `app`, `console`, `system`.
- `action` keys, localised by the client: `auth.login`, `post.submitted`, `post.resubmitted`, `post.published`, `post.auto_published`, `post.recheck`, `post.discarded`, `post.unpublished`, `post.reversed`, `post.escalated`, `post.created`, `post.updated`, `post.deleted`, `user.updated`, `user.created`, `user.blocked`, `user.unblocked`, `user.reassigned`, `user.assignment_added`, `user.assignment_removed`, `area.created`, `area.updated`, `area.deactivated`, `area.reactivated`, `category.created`, `category.updated`, `setting.changed`, `template.updated`, `report.exported`.

---

## 3. accounts

### Auth

| Method and path | Auth | Request | Response |
|---|---|---|---|
| `POST /auth/login/` | public | `{"username", "password", "client": "app"}` | `200 {"access", "refresh", "user": Me}`. Mobile app. |
| `POST /auth/refresh/` | refresh token | `{"refresh": "…"}` | `200 {"access", "refresh"}` |
| `POST /auth/logout/` | bearer | `{"refresh": "…", "all_devices"?: true}` | `204`. Blacklists the refresh token. `all_devices` also bumps `token_version`. |
| `GET /auth/csrf/` | public | — | `200 {"detail": "ok"}` and the `csrftoken` cookie. Console. |
| `POST /auth/session/login/` | public, `X-CSRFToken` | `{"username", "password"}` | `200 {"user": Me}` and the `sessionid` cookie. Console; needs `web_admin.access_web_console`. |
| `POST /auth/session/logout/` | session, `X-CSRFToken` | — | `204`. The session is flushed. |
| `POST /auth/password/change/` | bearer or session | `{"current_password", "new_password"}` | App: `200 {"access", "refresh"}`. Console: `200 {"user": Me}`, and the current session stays. Every other session and token ends. |
| `GET /me/` | bearer or session | — | `200 Me` |
| `PATCH /me/` | bearer or session | `{"name"?, "name_te"?, "preferred_language"?}` | `200 Me` |

Login rules:
- `username` matches the username only, ignoring case and surrounding spaces. Usernames are stored in lower case.
- A wrong password and an unknown username give the same `400 invalid_credentials`.
- **Lockout** (django-axes): 5 failures for one username from one IP address lock that pair for 15 minutes. Every try then answers `429 locked_out` with `retry_after_s`, even with the right password. A success clears the count. An admin can unlock early (`POST /users/{id}/unlock/`). Nginx adds 10 requests a minute per IP.
- **IP-wide cap:** 50 failed logins from one IP address, for any usernames, within 60 minutes block every login from that address with the same `429 locked_out` and `retry_after_s` (at most an hour). A success does not reset it and unlock does not lift it. Clients need no change: they already handle `locked_out`.
- After the password check: blocked → `403 account_blocked`; no active role → `403 permission_denied`; console login without `web_admin.access_web_console` → `403 not_console_user`, and no session.
- A successful login updates `last_login_at` and writes `auth.login` with `via` (`app` or `console`). Failures write `auth.login_failed`, refusals `auth.login_refused`, lockouts `auth.locked_out`, IP blocks `auth.ip_locked_out`.

Passwords:
- Hashed with Argon2. New passwords pass Django's validators: at least 8 characters, not common, not all digits, not like the username, and at least one capital letter, one small letter, one number and one special character (anything not a letter, number or space, such as `@ # $ % & * ! ?`). Generated temporary passwords follow the same rules. Existing passwords keep working until next changed. A new password must differ from the current one. Errors come as `400 validation_error` with `fields.new_password` (or `fields.password` for admin-set passwords).
- New and reset accounts carry `must_change_password`. Until they choose a new password, every endpoint except `GET /me/`, `POST /auth/password/change/`, the logouts and `/auth/refresh/` answers `403 password_change_required`.
- Changing a password ends every other session and token. Audited as `user.password_changed`.
- Nothing is emailed or texted. Temporary passwords are handed over by the admin in person.

### Users

| Method and path | Needs | Request | Response |
|---|---|---|---|
| `GET /users/` | any `accounts.manage_*` | Query: `role`, `status=ACTIVE\|BLOCKED`, `level` + `area_id`, `district`, `q` (name, Telugu name, username, email, mobile), `page` | Page of `User` inside the actor's areas, plus `"counts": {"total", "active", "blocked", "by_role": {"VILLAGE_ADMIN": 5}}` over the same filters except `role` and `status` |
| `POST /users/` | the group's manage permission over the area | `{"name", "name_te"?, "username", "mobile"?, "email"?, "preferred_language"?, "role", "area": {"level", "id"}, "password"?}` | `201 {"user": User, "temporary_password"?}`. Without `password`, the server generates one and returns it once as `temporary_password`. `must_change_password` is set. A taken username, email or mobile gives `409 duplicate` with the field. |
| `GET /users/{id}/` | managing the user | — | `User` |
| `PATCH /users/{id}/` | managing the user | `{"name"?, "name_te"?, "preferred_language"?, "email"?, "mobile"?}` | `User` |
| `POST /users/{id}/reset-password/` | `accounts.reset_password` | `{"password"?}` | `200 {"user": User, "temporary_password"?}`. Generated when `password` is left out, shown once. Sets `must_change_password`, bumps `token_version`. Audited as `user.password_reset`. |
| `POST /users/{id}/unlock/` | `accounts.unlock_user` | — | `200 User`. Clears the lockout for that username. Audited as `user.unlocked`. |
| `POST /users/{id}/block/` | `accounts.block_user` | `{"reason"?}` | `User`. `is_active` false and `token_version` bumped: every session ends at once. |
| `POST /users/{id}/unblock/` | `accounts.block_user` | — | `User` |
| `POST /users/{id}/role-assignments/` | the group's manage permission over the area | `{"role", "area": {"level", "id"}}` | `201 RoleAssignment`. This is how a second role is added. |
| `PATCH /users/{id}/role-assignments/{ra_id}/` | `accounts.reassign_user` | `{"area"?: {"level", "id"}, "is_active"?}` | `RoleAssignment`. "Reassign area". Pending reviews stay with the old area. |
| `DELETE /users/{id}/role-assignments/{ra_id}/` | managing the user | — | `204`. A user with no active assignment left cannot log in. |

Rules:
- An admin acts on a user only when the permission's assignment covers every one of that user's assignment areas, and every one of the user's groups ranks strictly below the admin's group. This applies to editing, blocking, resetting, unlocking, reassigning and removing.
- Giving a role needs that group's manage permission over the area (`accounts.manage_village_admin` and so on) and a group strictly above it. Nobody can give a group at or above their own rank.
- The role must be at the area's level: `DISTRICT_ADMIN` on a district, `MANDAL_ADMIN` on a mandal, `VILLAGE_ADMIN` and `PANCHAYAT_MEMBER` on a village.
- Super Admins are not created, reset or given through the API. The first one is made on the server with `python manage.py create_super_admin --username <name> --name "<full name>"`, which prints a temporary password once (see deploy/RUNBOOK.md).
- Inactive areas cannot receive new assignments. Admins cannot block themselves.

---

## 4. geography

| Method and path | Who | Request | Response |
|---|---|---|---|
| `GET /geo/states` | public | — | `[{"id", "name"}]` |
| `GET /geo/districts?state={id}` | public | — | `[{"id", "name", "state_id"}]` |
| `GET /geo/mandals?district={id}` | public | — | `[{"id", "name", "district_id"}]` |
| `GET /geo/villages?mandal={id}&q=` | public | `q` searches English and Telugu names | `[{"id", "name", "mandal_id", "pin_code"}]` |
| `GET /geo/villages/{id}` | public | — | `{"id", "name", "pin_code", "latitude": "16.238100", "longitude": "80.592400", "mandal": {"id", "name"}, "district": {"id", "name"}, "state": {"id", "name"}, "path"}` |
| `GET /geo/nearest?lat=&lng=` | public | Decimal degrees | `{"village": <village detail>, "distance_km": 1.8}`, or `404 not_found` when nothing lies within `NEAREST_VILLAGE_MAX_KM` (default 50). The search box starts at `NEAREST_VILLAGE_START_KM` (5) and doubles until a village is found. |

Public lists are unpaged and return active areas only, sorted by name in the requested language.

Reads for Super Admins, including inactive areas:

| Method and path | Response |
|---|---|
| `GET /geo/tree?villages=1` | `[State]`, each with `districts: [District]`, each with `mandals: [Mandal]`. Every row has `id`, `level`, `name_en`, `name_te`, `lgd_code`, `is_active`; districts add `mandal_count` and `village_count`, mandals add `village_count`. With `villages=1` each mandal also carries `villages: [Village]` (`latitude`, `longitude` as 6-decimal strings or null, `pin_code`). Leave it out for large data and load villages per mandal. |
| `GET /geo/admin/villages?mandal={id}` | `[Village]` of one mandal, same fields |

Writes, Super Admin only (`geography.manage_master_data`). `apps/geography/services.py` is the only writer; the CSV import uses it too. Each write answers with the area row above plus `"warnings"` (below) and writes an audit entry with the old and new values (`area.created`, `area.updated`, `area.deactivated`, `area.reactivated`; the import writes one `area.imported` with counts). An edit that changes nothing writes no entry. The district, mandal and village list URLs take both the public `GET` and the Super Admin `POST`; only the `GET` is cached.

| Method and path | Request |
|---|---|
| `POST /geo/districts` | `{"state_id", "name_en", "name_te"?, "lgd_code"?}` |
| `PATCH /geo/districts/{id}` | `{"name_en"?, "name_te"?, "lgd_code"?, "is_active"?}` |
| `POST /geo/mandals` | `{"district_id", "name_en", "name_te"?, "lgd_code"?}` |
| `PATCH /geo/mandals/{id}` | same as districts |
| `POST /geo/villages` | `{"mandal_id", "name_en", "name_te"?, "latitude", "longitude", "pin_code"?, "lgd_code"?}` |
| `PATCH /geo/villages/{id}` | any of the above plus `is_active` |
| `POST /geo/villages/import` | multipart `file` (UTF-8 CSV, same columns as `manage.py import_geography`); `?dry_run=1` saves nothing. `200 {"ok", "dry_run", "rows", "errors", "warnings", "counts": {"state"\|"district"\|"mandal"\|"village": {"created", "updated", "unchanged"}}, "missing_coordinates": [first 200], "missing_coordinates_count", "missing_telugu"}`. Any error: `400 validation_error` with the same object in `error.report`, and nothing is saved. |

Every write answers, for example:
```json
{
  "id": 77, "level": "VILLAGE", "name_en": "Chebrolu", "name_te": "చేబ్రోలు", "lgd_code": null,
  "is_active": true, "latitude": "16.196000", "longitude": "80.525000", "pin_code": "110001", "mandal_id": 12,
  "warnings": [{"code": "pin_outside_state", "message": "Andhra Pradesh PIN codes start with 5."}]
}
```

| Status | `code` | When |
|---|---|---|
| `201` / `200` | — | Created / updated. Check `warnings`. |
| `400` | `validation_error` | Missing or bad field (`fields` lists them); unknown parent (`fields.state_id`, `district_id` or `mandal_id`) |
| `401` | `not_authenticated`, `token_expired` | No or expired login (writes only) |
| `403` | `permission_denied` | Not a Super Admin. A console session write without `X-CSRFToken` is refused too. |
| `404` | `not_found` | PATCH of an id that does not exist |
| `409` | `duplicate` | Same name under the same parent (`fields.name_en`), also when another admin added it a moment earlier, or the LGD code is already used (`fields.lgd_code`) |

Warnings never block a save: `parent_inactive` (the parent is switched off), `name_te_missing` (no Telugu name, or the same as the English one), `name_te_not_telugu`, `coordinates_swapped`, `outside_state` (outside the `GEO_BOUNDS` box of Andhra Pradesh), `pin_outside_state` (not starting with 5). The CSV import lists them in its `warnings`, except `name_te_missing`, which `missing_telugu` counts.

Rules:
- `name_en` is required and unique among siblings, ignoring case and extra spaces; `409 duplicate` otherwise. The database enforces it (unique constraints, migration `geography.0003`), so two admins adding the same name at once cannot both succeed. A missing `name_te` copies `name_en`.
- `lgd_code` is digits only, at most 20, and unique per level.
- `latitude` is between -90 and 90 and `longitude` between -180 and 180, each with at most 6 decimals. `pin_code` is 6 digits.
- Before migrating a server to `geography.0003`, run `python manage.py geo_duplicates` (read-only). It must print "No duplicates."; fix any it lists with the owner first.
- Areas are never deleted. Deactivating an area hides it from public lists and blocks new users and posts there and in every area below it. Existing posts stay visible.
- Nearest village: bounding-box filter in the ORM, then haversine distance in Python, per CLAUDE.md.

---

## 5. posts

### Categories

| Method and path | Who | Response |
|---|---|---|
| `GET /categories` | public | `[Category]`, active only, in display order |
| `GET /admin/categories` | Super Admin | `[Category with admin fields]` |
| `POST /admin/categories` | Super Admin | `{"key", "name_te", "name_en", "short_name_te"?, "short_name_en"?}` → `201` (Pending Q6) |
| `PATCH /admin/categories/{id}` | Super Admin | `{"name_te"?, "name_en"?, "is_active"?, "order"?}` (Pending Q6) |

A disabled category disappears from the compose form and from feed filters. Its existing posts stay visible.

### Posts

| Method and path | Who | Request | Response |
|---|---|---|---|
| `GET /posts` | officials | Query: `status` (comma list), `mine=1`, `author`, `category`, `district`, `level` + `area_id`, `target_level`, `escalated=1`, `q` (title and body), `page` | Page of `Post`, plus `"status_counts": {"ALL": 15, "PENDING": 5, …}` over the same filters except `status`. `ALL` excludes drafts. |
| `POST /posts` | posting roles | `{"category": "announce", "language": "te", "title", "body", "target": {"level", "id"}, "event"?: {"kind", "start_date", "end_date"?}, "is_urgent"?: false, "submit": true}` | `201 Post` |
| `GET /posts/{id}` | officials | — | `Post` |
| `PATCH /posts/{id}` | author | same fields as create, all optional | `Post`. Only in `DRAFT` or `RECHECK`. |
| `DELETE /posts/{id}` | author | — | `204`. Only in `DRAFT`. |
| `POST /posts/{id}/submit` | author | — | `Post`. `DRAFT` to `PENDING` (action `SUBMIT`), or `RECHECK` to `PENDING` (action `RESUBMIT`). |
| `GET /posts/{id}/history` | officials | — | `[ReviewAction]`, newest first |
| `GET /posts/targets/` | posting roles | — | The "Visible to" choices for this official (below) |
| `POST /posts/{id}/urgent/` | reviewers whose area contains the post | `{"is_urgent": true\|false}` | `Post`. Panchayat members: `403 permission_denied`; outside the area: `404`. Audited as `post.urgent_set` or `post.urgent_cleared`. |

What `POST /posts` does:
- `submit` defaults to `true`, so the prototype flow, which has no draft step, works unchanged. With `submit: false` the post stays `DRAFT` (Pending Q4).
- Panchayat members post only to their own village. The post goes to `PENDING`.
- District, Mandal and Village Admins post to their own area or any area inside it. With `ADMIN_POSTS_AUTO_PUBLISH=True` the post goes straight to `PUBLISHED` with action `AUTO_PUBLISH`. Otherwise it goes to `PENDING` and someone else must review it; nobody reviews their own post.
- Super Admins may post only when `SUPER_ADMIN_CAN_POST=True`; otherwise `403 permission_denied`.
- **Visibility (owner decision Q5):** the author picks "Visible to" (village, mandal, district or state) and one area at that level, up to their own level. Panchayat members and Village Admins: their village. Mandal Admins: their mandal or a village in it. District Admins: their district, a mandal or a village in it. State: Super Admins only, and only with `SUPER_ADMIN_CAN_POST`. Anything wider answers `403 out_of_area`. One target per post.
- `event` and `is_urgent` are optional on create and `PATCH`. Only admins may set `is_urgent: true`; from a Panchayat member it answers `403 permission_denied`.

`GET /posts/targets/` answers, for example for a District Admin of Guntur:
```json
{"levels": [
  {"level": "DISTRICT", "areas": [{"level": "DISTRICT", "id": 3, "name": "గుంటూరు", "path": "గుంటూరు"}]},
  {"level": "MANDAL", "areas": [{"level": "MANDAL", "id": 12, "name": "తెనాలి", "path": "తెనాలి · గుంటూరు"}]},
  {"level": "VILLAGE", "areas": [], "pick_via": "geo"}
]}
```
- Levels come highest first. Each level lists the areas the official may pick directly: their own area at their own level, and the areas one level below it.
- `"pick_via": "geo"` means the list would be too long: the app lists the official's mandals from the level above and loads each mandal's villages from `GET /geo/villages/?mandal=`.
- `STATE` appears only for Super Admins when `SUPER_ADMIN_CAN_POST` is on. Officials who cannot post get `{"levels": []}`.
- Every transition writes a `ReviewAction` and an `AuditLog` entry in the same transaction, per CLAUDE.md.

### Media

| Method and path | Who | Request | Response |
|---|---|---|---|
| `POST /posts/{id}/media` | author | multipart: `file`, `kind=image\|video`, `focus_y`? | `201 Media` |
| `DELETE /posts/{id}/media/{media_id}` | author | — | `204` |

- Allowed while the post is `DRAFT` or `RECHECK`. Because `POST /posts` submits by default, the app creates the post with `submit: false`, uploads media, then calls `/submit`.
- Images: JPEG, PNG, WebP or HEIC, at most `MAX_IMAGE_UPLOAD_MB` (default 10) and `MAX_PHOTOS_PER_POST` (default 5) per post. Stored as WebP at most 1280 px, with a 640 px copy for big cards and a 320 px thumbnail. Too large: `413 file_too_large`. Not a photo: `415 unsupported_media`.
- **Video (owner decision Q8):** multipart `file` (MP4 or MOV), `kind=video`, optional `duration_s`, optional `poster` image. At most `MAX_VIDEO_UPLOAD_MB` (default 50; larger: `413 file_too_large`) and `VIDEO_MAX_SECONDS` (60; longer: `400 video_too_long`). Other files: `415 unsupported_media`. One video per post, never together with photos: `409 media_conflict`.
  - Duration: measured with `ffprobe` when it is installed (the server has it). Without `ffprobe` the client's `duration_s` is required and still checked.
  - Poster: made with `ffmpeg` from the first second when it is installed. Otherwise `poster` (a photo) is required and processed like photos.
  - The video is stored as uploaded; the app compresses it before upload.

### Review

| Method and path | Who | Request | Response |
|---|---|---|---|
| `GET /review/queue` | reviewers | Query: `level` + `area_id`, `category`, `escalated=1`, `page` | Page of `PENDING` `Post` inside the actor's areas, oldest first, excluding the actor's own posts |
| `POST /posts/{id}/review` | reviewers | `{"action": "publish"\|"recheck"\|"discard"\|"unpublish", "comment": "…"}` | `Post` |
| `POST /posts/{id}/review` (reverse) | a strictly higher level than whoever decided | `{"action": "reverse", "to_status": "PUBLISHED"\|"PENDING"\|"RECHECK"\|"DISCARDED", "comment"}` | `Post` |

- `comment` is required for `recheck`, `discard`, `unpublish` and `reverse`. It is shown to the author.
- `reverse` is allowed only when the actor's role is strictly higher than the role that made the current decision (Super Admin highest). With `REVIEW_FIRST_DECISION_WINS=False`, any reviewer in the area may reverse.
- With `REVIEW_FIRST_DECISION_WINS=True`, the first decision takes the post out of every queue. A later reviewer gets `409 already_decided`: `{"error": {"code": "already_decided", "message", "decided_by": {"id", "name", "role"}, "action": "PUBLISH", "at": "…", "post": Post}}`. The check is one conditional UPDATE (`WHERE id = … AND status = 'PENDING'`), so it holds on SQLite, MySQL and PostgreSQL alike.
- `publish` sends the push alert for the post's area (section 7).

### Area stats for the app dashboards

`GET /stats?level=&area_id=` (officials; defaults to the actor's first area):
```json
{"pending_review": 5, "published_today": 3, "active_users": 11, "blocked_users": 2, "escalated": 1}
```

### Runtime settings (Pending Q6)

`GET /admin/settings`, `PATCH /admin/settings` (Super Admin):
```json
{
  "review_escalation_hours": 24,
  "languages": {"te": true, "en": true},
  "admin_posts_auto_publish": true,
  "review_first_decision_wins": true,
  "super_admin_can_post": false
}
```
- Values start from Django settings and environment variables, as CLAUDE.md defines. A value saved here overrides the default and is audited as `setting.changed`.
- Only `review_escalation_hours` (6 to 168) and `languages.en` are editable, matching the prototypes. Telugu cannot be switched off. The other three are read-only here.

---

## 6. feed (public, no login)

No login. These views ignore any `Authorization` header, so a stale token on a citizen's phone never breaks the feed.

| Method and path | Request | Response |
|---|---|---|
| `GET /feed/?village_id=` | `village_id` (required), `category` (comma list of keys, e.g. `news,updates`), `q` (search, 2+ characters, title and body, any case), `has_video=1`, `cursor` | Cursor page of `FeedItem`, newest first, 20 per page: `{"next": "<url>\|null", "previous": "<url>\|null", "results": [FeedItem]}`. `400` without `village_id` or with a 1-character `q`; `404` for an unknown or inactive village. |
| `GET /feed/upcoming/?village_id=` | `village_id` | `[FeedItem]`, at most 10 published posts from the same feed whose event starts today or later, soonest first. For "Don't miss". |
| `GET /feed/urgent/?village_id=` | `village_id` | `[FeedItem]`, at most 5 published urgent posts from the same feed, published in the last 7 days, newest first. For the red alert at the top of Home and News. |
| `GET /feed/posts/{id}/` | — | `FeedPost`; `404 not_found` unless `PUBLISHED` |
| `GET /feed/posts/?ids=a,b,c` | 1 to 50 post ids | `[FeedItem]` for the ones still published, in the order asked. Used for posts saved on the phone. |

`FeedItem`, the slim list shape:
```json
{
  "id": "c2d4…",
  "category": "announce",
  "language": "te",
  "title": "గ్రామ సభ సమావేశం – అక్టోబర్ 20",
  "body": "…",
  "area": {"level": "VILLAGE", "id": 1042, "name": "కొలకలూరు"},
  "published_at": "2026-10-14T10:32:00+05:30",
  "thumbnail_url": "/media/posts/2026/10/…-320.webp",
  "image_url": "/media/posts/2026/10/…-640.webp",
  "media_count": 3,
  "source_role": "VILLAGE_ADMIN",
  "event": {"kind": "meeting", "start_date": "2026-10-20", "end_date": null},
  "is_urgent": false,
  "video": null
}
```
- `event` and `is_urgent` as on Post. `video`: `null`, or for a video post `{"url", "poster_url", "duration_s"}`, so the reels screen needs no extra call; `thumbnail_url` is then the poster's thumbnail.
- The list carries the full `body`, not an excerpt. The app's news deck shows the body on the card, and one larger page costs less on 2G than a request per card.
- `thumbnail_url` is the first photo's 320 px WebP, or `null`. URLs are relative to the server.
- `image_url` is the first photo sized for big cards: its 640 px WebP (`POST_MEDIUM_PX`, about 30–50 KB), or the 1280 px one for photos from before that size existed. `null` for a post without a photo and for a video post (use `video.poster_url`). Use it for large cards and `thumbnail_url` for small rows. Clients fall back to `thumbnail_url` when the field is missing.
- `category` is the key only; the app caches `GET /categories`.

`FeedPost` (detail) is `FeedItem` plus `"path": "కొలకలూరు · తెనాలి · గుంటూరు"` and `"media": [{"id", "kind", "url", "thumb_url", "poster_url", "width", "height", "duration_s"}]`.

Caching: every feed response sends `Cache-Control: public, max-age=60` and an `ETag`; a repeat request with `If-None-Match` gets `304` with an empty body.

Feed rule: published posts whose target is the village, its mandal, its district or its state. The mobile prototype also shows other districts' district-level posts; that is a prototype bug and is not followed. Ordering is `published_at` then `id`, both descending; the composite index `(status, target_level, target_area_id, published_at)` on posts serves it.

---

## 7. notifications

### Devices

| Method and path | Who | Request | Response |
|---|---|---|---|
| `POST /devices/` | public or bearer | `{"fcm_token", "platform": "android"\|"ios"\|"web", "language": "te"\|"en", "village_id"?, "app_version"?}` | `201` when new, `200` when the token was known: `{"id", "fcm_token", "platform", "language", "village_id", "app_version"}`. Upserts by `fcm_token`. With a bearer token the device is linked to that official; without one it is unlinked. `400` for an unknown or inactive village. |
| `PATCH /devices/{id}/` | the app that registered it | `{"village_id"?, "language"?, "fcm_token"?}` | `200`, same shape. A device linked to an official answers `404` to anyone else. |
| `DELETE /devices/{id}/` | same | — | `204` (logout or opt-out) |

### Inbox (officials)

| Method and path | Response |
|---|---|
| `GET /notifications/?unread=1&cursor=` | Cursor page of `Notification`, 20 per page, newest first, plus `"unread_count"` |
| `POST /notifications/{id}/read/` | `204`; `404` for someone else's |
| `POST /notifications/read-all/` | `204` |

`Notification`: `{"id", "kind", "title", "body", "post_id", "area": {"level", "id"}|null, "created_at", "read_at"}`. `title` is the rendered text in the official's preferred language; `body` is the post title.

What creates inbox items (each also queues a push to the official's devices):

| Event | Recipients | `kind` | Template |
|---|---|---|---|
| A post enters `PENDING` | Active Village, Mandal and District Admins whose area contains the target, except the author. Super Admins are not pinged per post; they watch the queue. | `REVIEW_PENDING` | `review_pending` |
| A reviewer publishes, sends back, discards or takes down a post | The author, unless the author acted | `POST_PUBLISHED`, `POST_RECHECK`, `POST_DISCARDED`, `POST_UNPUBLISHED` | `post_approved`, `post_recheck`, `post_discarded`, `post_unpublished` |
| Escalation | Admins at the level the post escalated to whose area contains it; Super Admins when it reaches state level | `ESCALATION` | `escalation` |

Citizens have no account. Their alert list is built on the device from the feed, and read state stays on the device, as in the prototype.

### Push (Firebase Cloud Messaging)
- **Topics, without a language suffix:** `village_{id}`, `mandal_{id}`, `district_{id}`, `state_{id}`. A citizen's app subscribes to all four for its village and moves the subscriptions when the village changes.
- **Publishing** sends one message to the topic of the post's target level. A district post reaches every village in the district with one message.
- **Language:** a post exists only in the language it was written in, so the topic push is in the post's language: title from template `post_published` (e.g. "కొలకలూరు: కొత్త గ్రామ చాటింపులు"), body = post title. Pushes to officials use each official's preferred language.
- **Payload:** notification `{"title", "body"}` plus data `{"type": "POST_PUBLISHED"|"REVIEW_PENDING"|…, "post_id", "area_level", "area_id", "urgent": "1"|"0"}`, all strings. The app opens the post on tap.
- **Priority:** topic pushes for ordinary posts go out at normal priority; an urgent post's push goes out at high priority (Android `priority: high`, APNs `apns-priority: 10`). Pushes to officials are high priority.
- **Delivery:** django-q2 jobs. Each send retries with backoff: `DELIVERY_RETRY_ATTEMPTS` tries (default 3), waiting `DELIVERY_RETRY_BASE_SECONDS` × 1, × 4 (1 s, 4 s). After the last failure an audit entry `push.failed` is written (`via: system`, target = topic or device id, no personal data). An unregistered token deletes the device without retrying.
- **No credentials, no push:** without `FCM_CREDENTIALS_FILE` the gateway logs "FCM disabled: no credentials" once and skips sends, so development needs no Firebase project.

Invite and reset emails use the same retries (section 3).

### Notification templates

| Method and path | Who | Request or response |
|---|---|---|
| `GET /admin/notification-templates/` | Super Admin | `[{"key", "name", "name_te", "name_en", "channel": "push", "recipient": "author"\|"citizens"\|"reviewers"\|"higher_admin", "text_te", "text_en", "variables": ["title", "comment"], "editable": true, "updated_at"}]` |
| `PATCH /admin/notification-templates/{key}/` | Super Admin | `{"text_te", "text_en"}`, both required; only the listed `variables` may appear. Audited as `template.updated`. |

Keys, seeded by a migration: `post_published`, `post_approved`, `post_recheck`, `post_discarded`, `post_unpublished`, `review_pending`, `escalation`, all push and editable. There are no SMS texts.

---

## 8. audit

### Audit log (Super Admin)

Needs `audit.view_audit_log`; the export needs `audit.export_audit_log`.

| Method and path | Request | Response |
|---|---|---|
| `GET /audit` | `q` (actor, action, target, area), `type`, `actor`, `action`, `from`, `to` (dates), `page` | Page of `AuditEntry`, plus `"counts_by_type"` |
| `GET /audit/export?format=csv&…` | same filters | CSV file. UTF-8 with BOM so Excel shows Telugu. Writes `report.exported`. |

Audit entries are append-only. No endpoint edits or deletes them.

### Dashboard (Super Admin)

Needs `web_admin.view_dashboard`.

`GET /admin/dashboard`:
```json
{
  "geo": {"districts": 13, "mandals": 679, "villages": 17000},
  "kpis": {
    "total_posts": 1520, "pending": 42, "pending_districts": 9, "oldest_pending_hours": 26.0,
    "escalated": 3, "published_today": 57, "active_admins": 410,
    "active_users": 980, "blocked_users": 12, "live_villages": 16950, "inactive_villages": 50
  },
  "posts_per_day": [{"date": "2026-10-01", "submitted": 6, "published": 5}],
  "pending_by_district": [{"district": Area, "pending": 12, "oldest_pending_hours": 26.0}],
  "recent_activity": [AuditEntry]
}
```
Built (web_admin app). Also returns `"escalation_hours"` (the effective setting). Drafts are not counted. `posts_per_day` covers the last 14 days, bucketed by Asia/Kolkata date in Python, so no database time-zone functions are needed. Each `pending_by_district` row also has `name_te`; `oldest_pending_hours` is null when nothing is pending. `active_admins` counts active users holding a District, Mandal or Village Admin role. `recent_activity` holds 6 entries; the app shows 5.

### Reports (Super Admin)

Needs `web_admin.view_reports`.

Built (web_admin app). `days` is 1 to 366, default 30, and counts posts created in that window (drafts excluded).

| Method and path | Response |
|---|---|
| `GET /admin/reports/summary?days=30` | `{"total", "published", "pending", "rejected", "days"}`. `rejected` counts `DISCARDED` and `UNPUBLISHED`. |
| `GET /admin/reports/posts-by-area?days=30` | `[{"district": Area, "total", "published", "pending", "rejected", "mandals": [{"mandal": Area, "total", "published", "pending", "rejected"}]}]`. Mandals with no posts are left out. |
| `GET /admin/reports/review-time?days=30` | `[{"level": "VILLAGE"\|"MANDAL"\|"DISTRICT"\|"STATE", "avg_hours": 3.4 or null, "reviews": 42, "escalation_hours": 24}]`. Hours from the latest (re)submission to the reviewer's decision. |
| `GET /admin/reports/admin-activity?days=30&page=` | Page of `{"user": {"id", "name", "role"}, "area": Area, "reviewed", "published", "recheck", "discarded", "avg_review_hours", "last_active_at"}`, most decisions first. `avg_review_hours` is the admin's average, over their publish, recheck and discard decisions in the window, of the hours from the post's latest (re)submission to the decision, the same measure as `review-time`; one decimal, `null` when they made no such decision. |
| `GET /admin/reports/{report}/export/?format=csv&days=` | `report` is one of the four above. CSV with a UTF-8 BOM so Excel shows Telugu; area names follow `Accept-Language`. The admin-activity CSV has an "Average review hours" column, empty when `avg_review_hours` is `null`. Errors still come back as JSON. `format=pdf` answers 404: exports are CSV only (owner decision Q14). Writes `report.exported`. |

---

## 9. web_public

| Method and path | Auth | Request | Response |
|---|---|---|---|
| `GET /public/site-config/` | public | — | `{"office_address_te", "office_address_en", "phone", "email", "hours_te", "hours_en", "play_store_url", "app_store_url", "website_url", "helplines": [{"number": "112", "label_te": "అత్యవసరం", "label_en": "Emergency"}, …]}`. Addresses use `\n` between the two lines the page shows. Empty store URLs keep the website's "coming soon" toast. Cacheable for 5 minutes. |
| `GET /public/showcase/` | public | — | `{"location": Area\|null, "featured": FeedItem\|null, "items": [FeedItem, FeedItem]}` for the home page's phone picture. Cacheable for 60 s. |
| `GET /admin/contact-messages/` | Super Admin | Query: `status=new\|handled`, `q` (name, mobile, village, message), `page` | Page of `{"id", "name", "mobile", "village_text", "message", "language", "created_at", "handled", "handled_at", "handled_by": {"id", "name"}\|null}`, newest first |
| `PATCH /admin/contact-messages/{id}/` | Super Admin | `{"handled": true\|false}` | The message. Sets or clears `handled_at` and `handled_by`; audited as `contact.handled` or `contact.reopened`. |
| `POST /public/contact-messages/` | public | `{"name", "mobile", "village_text"?, "message", "language", "website"?}` | `201 {"id", "created_at"}` |

- Built. Values come from settings, each overridable by env: `SITE_OFFICE_ADDRESS_TE`, `SITE_OFFICE_ADDRESS_EN`, `SITE_PHONE`, `SITE_EMAIL`, `SITE_HOURS_TE`, `SITE_HOURS_EN`, `PLAY_STORE_URL`, `APP_STORE_URL`. Defaults are the prototype's values.
- Contact form validation matches the website: name and message required after trimming, mobile 10 digits (`+91` and spaces are accepted and stripped). `website` is a honeypot: when filled, the server answers `201` with `id` 0 and stores nothing. At most `CONTACT_MESSAGES_PER_IP_PER_HOUR` (default 5) per IP, then `429 throttled` with `Retry-After`.
- `website_url` (`SITE_WEBSITE_URL`) is the link the app adds to shared posts and invites (owner decision Q17).
- `helplines` (owner decision Q19): the state's helplines from the important numbers (section 14), in their `sort_order`. While none are loaded it falls back to `SITE_HELPLINES` (a JSON list in the env), by default 112 Emergency, 108 Ambulance, 1930 Cyber fraud, 181 Women helpline, 1098 Child helpline, 1912 Electricity, with the prototype's Telugu labels. `seed_helplines` loads that same list into the table, so the output does not change.
- **Showcase (owner decision Q16):** `location` is the village in `SITE_SHOWCASE_VILLAGE_ID` and the posts come from its feed: `featured` is the newest post with a photo (or the newest post), `items` the next two. When the setting is empty or that feed has no posts, the newest published posts anywhere are used and `location` is the featured post's area.
- **Contact messages (owner decision Q16)** are read and marked handled in the console. Others get `403 permission_denied`.

---

## 10. Post state machine

| From | To | Action | Who | Comment |
|---|---|---|---|---|
| (new) | `DRAFT` | — | any posting role, `submit: false` | — |
| (new) or `DRAFT` | `PENDING` | `SUBMIT` | Panchayat member, or an admin when auto-publish is off | — |
| (new) or `DRAFT` | `PUBLISHED` | `AUTO_PUBLISH` | District, Mandal or Village Admin when `ADMIN_POSTS_AUTO_PUBLISH=True`; Super Admin only when also `SUPER_ADMIN_CAN_POST=True` | — |
| `PENDING` | `PUBLISHED` | `PUBLISH` | reviewer whose area contains the target, not the author | optional |
| `PENDING` | `RECHECK` | `RECHECK` | same | required |
| `PENDING` | `DISCARDED` | `DISCARD` | same | required |
| `RECHECK` | `PENDING` | `RESUBMIT` | author | — |
| `PUBLISHED` | `UNPUBLISHED` | `UNPUBLISH` | reviewer whose area contains the target, including the author's superiors (Pending Q18) | required |
| `PENDING` | `PENDING` | `ESCALATE` | System, after `REVIEW_ESCALATION_HOURS` with no decision; flags the post and alerts the next level up | — |
| `PUBLISHED`, `RECHECK`, `DISCARDED`, `UNPUBLISHED` | `PUBLISHED`, `PENDING`, `RECHECK`, `DISCARDED` | `REVERSE` | a reviewer whose role is strictly higher than the one who decided | required |

Any other move returns `409 invalid_transition`. Each row writes one `ReviewAction` and one `AuditLog` entry in the same database transaction as the status change.

Escalation runs as a django-q2 scheduled job every `ESCALATION_CHECK_MINUTES` (15); `manage.py ensure_schedules` registers it. It does not hide the post from lower reviewers. It marks `escalated`, writes `ESCALATE`, and notifies the admins one level above the target area. There is one escalation time for every level, `REVIEW_ESCALATION_HOURS` (24 by default); the console's per-level limits are not used (owner decision Q1).

---

## 11. Permission matrix

Roles are Django groups and actions are Django permissions. The full group × permission table is in `docs/authorization.md`, section 7, and in code in `apps/accounts/role_matrix.py`. "Area" below means the area of one of the actor's active role assignments; "inside" includes the area itself. A Super Admin assignment covers every area.

| Action | Permission | Super Admin | District Admin | Mandal Admin | Village Admin | Panchayat member | Citizen |
|---|---|---|---|---|---|---|---|
| Read feed, categories, geography | public | yes | yes | yes | yes | yes | yes |
| Create and submit a post | `posts.create_post`, `posts.submit_post` | only if `SUPER_ADMIN_CAN_POST`; the only group that may target the state | district, a mandal or a village inside it | mandal, or a village inside it | own village | own village, goes to review | no |
| Review queue | `posts.view_review_queue`, `posts.review_post` | any post | posts targeted inside the district | inside the mandal | inside the village | no | no |
| Publish, recheck, discard | `posts.publish_post`, `posts.recheck_post`, `posts.discard_post` | any | inside the district | inside the mandal | inside the village | no | no |
| Take down a published post | `posts.takedown_post` | any | inside the district | inside the mandal | inside the village | no | no |
| Reverse a decision | `posts.reverse_decision` | when the decider ranked lower | same | same | same | no | no |
| Mark as urgent | `posts.mark_urgent` | any | inside the district | inside the mandal | inside the village | no | no |
| Area stats | `posts.view_stats` | any area | inside the district | inside the mandal | the village | the village | no |
| Create users, add roles | `accounts.manage_<group>` | District, Mandal, Village Admins, Panchayat members | Mandal, Village Admins, Panchayat members inside the district | Village Admins, Panchayat members inside the mandal | Panchayat members in the village | no | no |
| Block, reset password, reassign, unlock | `accounts.block_user`, `accounts.reset_password`, `accounts.reassign_user`, `accounts.unlock_user` | anyone ranked below | lower groups inside the district | lower groups inside the mandal | Panchayat members in the village | no | no |
| Geography writes | `geography.manage_master_data` | yes | no (owner decision Q13) | no | no | no | no |
| Audit log, export | `audit.view_audit_log`, `audit.export_audit_log` | yes | no | no | no | no | no |
| Dashboard, reports | `web_admin.view_dashboard`, `web_admin.view_reports` | yes | no | no | no | no | no |
| Settings, categories | `posts.manage_settings`, `posts.manage_categories` | yes | no | no | no | no | no |
| Notification templates | `notifications.manage_templates` | yes | no | no | no | no | no |
| Contact messages | `web_public.manage_contact_messages` | yes | no | no | no | no | no |
| Important numbers (section 14) | `contacts.manage_contact` | any area, and the statewide helplines | inside the district | inside the mandal | the village | the village (decision C1) | read only, public |
| Contact types | `contacts.manage_contact_types` | yes | no | no | no | no | no |
| Web console login | `web_admin.access_web_console` | yes | no | no | no | no | no |
| Groups and role assignments in Django admin | `accounts.manage_groups` | yes | no | no | no | no | no |

Every row has tests against SQLite, per CLAUDE.md: each group × each permission × the same area, the parent, a sibling and another district.

---

## 12. Screen to endpoint map

### Mobile app

| Screen | Endpoints |
|---|---|
| Splash, language, welcome | none; `GET /categories` and geography warm the cache |
| Location step (to be designed, see inventory) | `GET /geo/villages/nearest`, then the picker if needed |
| Change village | `GET /geo/states`, `/geo/districts`, `/geo/mandals`, `/geo/villages`; `PATCH /devices/{id}` |
| Citizen home | `GET /feed/summary` or the first page of `GET /feed` |
| News deck, videos | `GET /feed` with `category` or `has_video=1` |
| Post detail | `GET /feed/posts/{id}` |
| Saved posts | `GET /feed/posts?ids=` |
| Citizen alerts | built on the device from `GET /feed` |
| Login | `POST /auth/login/` |
| Change password (first login, profile) | `POST /auth/password/change` |
| Profile | `GET /me`, `PATCH /me`, `POST /auth/logout` |
| Panchayat "My posts" | `GET /posts?mine=1` |
| Compose, edit and resubmit | `POST /posts`, `POST /posts/{id}/media`, `DELETE …/media/{id}`, `PATCH /posts/{id}`, `POST /posts/{id}/submit` |
| Admin dashboard | `GET /stats`, `GET /review/queue?page_size=3` |
| Review queue | `GET /review/queue` |
| Review | `GET /posts/{id}`, `GET /posts/{id}/history`, `POST /posts/{id}/review` |
| Published posts | `GET /posts?status=PUBLISHED` |
| Manage users, add user | `GET /users`, `POST /users`, `POST /users/{id}/block`, `POST /users/{id}/unblock` |
| Official notifications | `GET /notifications`, `POST /notifications/{id}/read`, `POST /notifications/read-all` |
| Super Admin: home | `GET /admin/dashboard` |
| Super Admin: all posts | `GET /posts` with `status_counts`; `POST /posts` only if allowed |
| Super Admin: areas | `GET /geo/*?include_inactive=1&with_counts=1`, geography writes |
| Super Admin: reports, audit, settings | `GET /admin/reports/*`, `GET /audit`, `/admin/settings`, `/admin/categories`, `/admin/notification-templates` |
| App start | `POST /devices` |
| Contacts tab (citizens) | `GET /contacts?village_id=`; `GET /contact-types` for chips and icons |
| Important numbers (every official) | `GET /admin/contacts` (`section=AREA\|HELPLINE`), `POST /admin/contacts`, `PATCH /admin/contacts/{id}`, `DELETE /admin/contacts/{id}`, `GET /admin/contacts/targets` |

### Super Admin console

| Page | Endpoints |
|---|---|
| Login | `GET /auth/csrf/`, `POST /auth/session/login/`; then `POST /auth/password/change/` when `must_change_password` |
| Shell (header, profile, change password, logout) | `GET /me/`, `POST /auth/password/change/`, `POST /auth/session/logout/`; menus follow `permissions`; Posts badge from `GET /posts?status=PENDING&page_size=1` |
| Dashboard | `GET /admin/dashboard` |
| Geography | `GET /geo/tree`, geography writes |
| Users | `GET /users`, `GET /users/{id}`, `GET /posts?author={id}&page_size=5`, `POST /users`, block, unblock, reset password, unlock, role assignments |
| Posts and moderation | `GET /posts` with `status_counts`, `GET /posts/{id}`, `GET /posts/{id}/history`, `POST /posts/{id}/review`, `GET /categories` for the filter |
| Reports | `GET /admin/reports/*` and their exports |
| Audit log | `GET /audit`, `GET /audit/export` |
| Settings | `/admin/settings`, `/admin/categories`, `/admin/notification-templates` |
| Contact messages | `GET /admin/contact-messages`, `PATCH /admin/contact-messages/{id}` |
| Important numbers | `GET /admin/contacts` (area and helpline tabs), `POST /admin/contacts`, `PATCH /admin/contacts/{id}`, `DELETE /admin/contacts/{id}`, `GET /admin/contacts/targets`, `POST /admin/contacts/import`; types: `/admin/contact-types` |

### Public website

| Page | Endpoints |
|---|---|
| All pages | `GET /public/site-config` once |
| Home phone mockup | `GET /public/showcase` |
| Contact | `POST /public/contact-messages` |

---

## 13. Limits and throttles

| Item | Limit |
|---|---|
| Failed logins | 5 per username and IP address, then locked for 15 minutes (django-axes; a success resets the count); Nginx 10 requests a minute per IP |
| Contact form | 5 per hour per IP |
| Public feed | 120 requests per minute per IP |
| Post title | 150 characters |
| Post body | 5,000 characters |
| Review comment | 500 characters |
| Photos per post | `MAX_PHOTOS_PER_POST`, default 5 |
| Image upload | `MAX_IMAGE_UPLOAD_MB`, default 10 |
| Video | one per post, `VIDEO_MAX_SECONDS` 60, `MAX_VIDEO_UPLOAD_MB` default 50 |
| Page size | feed 20, admin lists 100 |
| Important numbers | 200 active per area (`CONTACTS_MAX_PER_AREA`); name 150, custom type 40 characters; CSV import 2 MB and 5,000 rows |

---

## 14. contacts: important numbers

Built (`apps.contacts`). The app's Contacts tab: phone numbers officials publish for their area (VRO, Mee Seva, Sachivalayam, Tahsildar office …) and the statewide government helplines (108, 1098 …). Each number has one area, stored like a post's target. A citizen sees the numbers of their village, its mandal, its district and its state. Open decisions C1 to C5 and their defaults are in `prototype-inventory.md`, section 8.

**Sections.** A type is either `AREA` (numbers at district, mandal or village level, shown under "For you") or `HELPLINE` (numbers at state level, shown under "Government helplines"). A number's level must agree with its type's section: otherwise `400 validation_error` on `area`.

### Public (no login)

| Method and path | Request | Response |
|---|---|---|
| `GET /contacts/?village_id=` | `village_id` (required) | `CitizenContacts` (below). `400` without `village_id`; `404` for an unknown or inactive village. `ETag` and `Cache-Control: public, max-age=300` (`CONTACTS_PUBLIC_MAX_AGE`); `If-None-Match` gets `304`. Ignores any `Authorization` header, like the feed. Rate-limited by Nginx like the rest of the API. |
| `GET /contact-types/` | — | `[{"key", "section", "name", "is_other", "sort_order"}]`, active types only. `max-age=3600`. |

```jsonc
// CitizenContacts: one call fills the whole Contacts tab
{
  "village": {"level": "VILLAGE", "id": 1042, "name": "కొలకలూరు", "path": "కొలకలూరు · తెనాలి · గుంటూరు"},
  "for_you": [
    {
      "type": "vro",
      "label": "వీఆర్వో",            // the typed custom_type for "Other" numbers
      "contacts": [
        {"id": "6f1c…", "name": "కె. శ్రీనివాసరావు", "phone": "9000010001", "phone_kind": "MOBILE",
         "email": null, "area": {"level": "VILLAGE", "id": 1042, "name": "కొలకలూరు"}}
      ]
    }
  ],
  "helplines": [
    {"type": "health", "label": "ఆరోగ్యం", "contacts": [
      {"id": "…", "name": "అంబులెన్స్", "phone": "108", "phone_kind": "SHORT_CODE", "email": null,
       "area": {"level": "STATE", "id": 1, "name": "ఆంధ్రప్రదేశ్"}}
    ]}
  ],
  "updated_at": "2026-10-26T15:00:00+05:30"   // newest change among the rows; null when there are none
}
```
- Order: groups in the types' `sort_order`; one group per type, and one per typed name for "Other" numbers (ignoring case). Inside a group the most local number first (village, mandal, district), then its `sort_order`, then its name. Empty groups are left out.
- `label`, `name` and the area's name follow `Accept-Language` (Telugu by default). A number without a Telugu name shows its English one.
- Not paged: at most `CONTACTS_MAX_PER_AREA` numbers per area and four areas. Phone formatting ("90000 10001") is the app's job; the server sends digits.

### Officials

All need `contacts.manage_contact`, which every official holds in their own area: District, Mandal and Village Admins, Panchayat members (decision C1) and the Super Admin. Only a platform-wide assignment covers the STATE, so only the Super Admin changes helplines.

| Method and path | Who | Request | Response |
|---|---|---|---|
| `GET /admin/contacts/` | `manage_contact` anywhere | `section=AREA\|HELPLINE` (default `AREA`), `level` + `area_id` (that area and below), `type` (key), `q` (name, or the phone's digits), `page` | Page of `AdminContact`. `AREA`: only numbers inside the actor's areas. `HELPLINE`: every active statewide number; `allowed_actions` is empty unless the actor may edit it. Most local first, then type, `sort_order`, name. |
| `POST /admin/contacts/` | `manage_contact` over `area` | `{"type", "custom_type"?, "name", "name_te"?, "phone", "email"?, "area": {"level", "id"}, "sort_order"?, "consent_recorded"?}` | `201 AdminContact`. `400 validation_error`; `403 out_of_area`; `409 duplicate` (`fields.phone`). |
| `GET /admin/contacts/{id}/` | `manage_contact` over its area; any official for a helpline | — | `AdminContact`, otherwise `404` |
| `PATCH /admin/contacts/{id}/` | over the old **and** the new area | any POST field, plus `"verified": true` to stamp `verified_at` | `AdminContact`. `404` if the number is outside reach; `403 out_of_area` if the new area is. |
| `DELETE /admin/contacts/{id}/` | over its area | — | `204`. Soft delete: hidden from citizens, kept for the audit trail. A second delete answers `404`. |
| `GET /admin/contacts/targets/` | `manage_contact` anywhere | — | "Who sees it" choices, in the shape of `GET /posts/targets/` (`levels`, `areas`, `pick_via: "geo"`), never `STATE` |
| `POST /admin/contacts/import/` | Super Admin (`manage_contact` over the platform) | multipart `file` (UTF-8 CSV, BOM allowed, at most `CONTACTS_IMPORT_MAX_BYTES` 2 MB and `CONTACTS_IMPORT_MAX_ROWS` 5,000 rows); `?dry_run=1` checks only | `200 {"dry_run", "created", "updated", "skipped", "errors": []}`. Any bad row: `400 validation_error` with `report` holding the same object and `errors: [{"row", "field", "message"}]`, and nothing is written. Too large: `413 file_too_large`. |
| `GET /admin/contact-types/` | Super Admin (`contacts.manage_contact_types`) | — | `[{"id", "key", "section", "name_en", "name_te", "is_other", "sort_order", "is_active", "contact_count"}]` |
| `POST /admin/contact-types/` | same | `{"key", "section", "name_en", "name_te", "is_other"?, "sort_order"?, "is_active"?}` | `201`; a used key: `409 duplicate` |
| `PATCH /admin/contact-types/{id}/` | same | `name_en`, `name_te`, `is_other`, `sort_order`, `is_active` | The type. `key` and `section` never change. A type is switched off, never deleted. |

`AdminContact`:
```jsonc
{
  "id": "6f1c…",
  "type": {"key": "other", "section": "AREA", "name": "ఇతర"},
  "custom_type": "Anganwadi",
  "name": "Anganwadi Centre, Tenali", "name_te": "",
  "phone": "9848012345", "phone_kind": "MOBILE", "email": "",
  "area": {"level": "MANDAL", "id": 12, "name": "తెనాలి", "path": "తెనాలి · గుంటూరు"},
  "sort_order": 100,
  "consent_recorded": true,
  "verified_at": "2026-10-26T15:00:00+05:30",
  "needs_recheck": false,            // never verified, or not for CONTACTS_VERIFY_DAYS (180)
  "created_by": {"id": "…", "name": "Lakshmi Devi"},
  "created_at": "…", "updated_at": "…",
  "allowed_actions": ["edit", "remove", "verify"]
}
```

### Rules

| Field | Rule |
|---|---|
| `type` | An active type's key. Seeded `AREA` keys: `vro`, `meeseva`, `sachivalayam`, `revenue`, `official`, `other`. `HELPLINE` keys: `emergency`, `farmers`, `senior`, `student`, `women`, `health`, `police`, `railways`, `power`, `grievance`, `more`. They are the app's icon keys. |
| `custom_type` | Required, 1 to 40 characters, for "Other" types (`is_other`); must be empty otherwise. Changing to another type clears it. |
| `name`, `name_te` | `name` required, 1 to 150 characters after collapsing spaces. `name_te` optional, the Telugu name. |
| `phone` | Spaces, dashes and brackets are removed; a leading `+91`, or `91` when 12 digits remain, is dropped. Then one kind must match: `MOBILE` `[6-9]` + 9 digits, `LANDLINE` `0` + 9-10 digits, `TOLL_FREE` `1800` + 6-7 digits, `SHORT_CODE` 3-6 digits for helplines only. Anything else, such as `*#06#`, is refused. |
| `email` | Optional, a valid address, stored in lower case. |
| `area` | Must exist and be active, and agree with the type's section. |
| `consent_recorded` | Must be `true` for a `MOBILE` number while `CONTACTS_REQUIRE_MOBILE_CONSENT` is on (decision C2). The form says "This person agreed to have this number shown to the public". |
| `sort_order` | 0 to 1000, default 100. |
| Duplicates | No two active numbers with the same type, phone and area: `409 duplicate`. Checked in the transaction; a removed number may be added again. |
| Capacity | At most `CONTACTS_MAX_PER_AREA` (200) active numbers per area: `400 validation_error` on `area`. |

- Audited, with the phone masked (`******0001`): `contact.created`, `contact.updated` (changed field names, and the masked old and new phone when it changed), `contact.removed`, `contact_type.created`, `contact_type.updated`, `contacts.imported` (counts only).
- CSV columns: `type`, `name`, `phone` (required), `custom_type`, `name_te`, `email`, `sort_order`, `consent` (`yes`/`1`/`true`), and the area as `district_lgd`, `mandal_lgd`, `village_lgd` or the names `district`, `mandal`, `village` (LGD code first, else the name within the parent; the deepest one given sets the level). Helpline rows leave the area columns empty and go to the state. A row matching an active number's type, phone and area updates it instead of adding one. `python manage.py import_contacts file.csv [--dry-run]` does the same.
- `python manage.py seed_helplines` loads `apps/contacts/sample_data/helplines.csv`, the six numbers of `SITE_HELPLINES`, until the owner signs off a longer list (decision C3).
- `GET /public/site-config/` reads `helplines` from the statewide numbers (`label_en` = `name`, `label_te` = `name_te` or the type's Telugu name, in `sort_order`), and falls back to `SITE_HELPLINES` while none are loaded.
