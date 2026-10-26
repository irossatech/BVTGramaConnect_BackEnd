# Login and authorization

How officials log in, how passwords and lockouts work, and how the server decides what each official may do. Owner decisions of 21 October 2026; see `authz-migration.md` for how the code got here.

Citizens never log in. Every citizen read endpoint is public.

Contents:
1. Login flow
2. Passwords
3. Lockout and unlock
4. New accounts and forgotten passwords
5. Blocking
6. Groups
7. Permission matrix
8. Area scoping
9. Checking permissions in code
10. How to add a permission
11. Running sync_role_permissions
12. Django admin
13. Audit events
14. Settings

---

## 1. Login flow

Officials log in with a **username and a password**. There is no OTP, no SMS and no email login. The username is unique, ignores case and surrounding spaces, and is stored in lower case. Email and mobile number are optional contact details only and never work as a login name.

Both clients go through the same check (`apps/accounts/credentials.py`, `authenticate_official`): django-axes lockout first, then the password, then blocked accounts. Every success and failure is audited.

### Mobile app: JWT

| Step | Call | Result |
|---|---|---|
| Log in | `POST /api/v1/auth/login/` `{"username", "password", "client": "app"}` | `200 {"access", "refresh", "user": Me}` |
| Call the API | `Authorization: Bearer <access>` | Access tokens last 15 minutes |
| Refresh | `POST /api/v1/auth/refresh/` `{"refresh"}` | New pair. The old refresh token is blacklisted. Refresh tokens last 30 days. |
| Log out | `POST /api/v1/auth/logout/` `{"refresh", "all_devices"?}` | `204`. The refresh token is blacklisted. `all_devices` also ends every other session. |

Every token carries the user's `token_version` as the claim `tv`. The server loads the user on every request and refuses a token whose `tv` no longer matches, with `401 session_revoked`. Blocking, a password change and an admin reset all bump `token_version`, so they end every session at once.

### Web console: Django session

The console does not use tokens. It uses a Django session cookie with CSRF protection.

| Step | Call | Result |
|---|---|---|
| Get a CSRF cookie | `GET /api/v1/auth/csrf/` | Sets `csrftoken`, which page scripts may read |
| Log in | `POST /api/v1/auth/session/login/` `{"username", "password"}` with the header `X-CSRFToken` | `200 {"user": Me}` and the `sessionid` cookie |
| Call the API | Same-origin cookies; `X-CSRFToken` on every POST, PATCH, PUT and DELETE | |
| Log out | `POST /api/v1/auth/session/logout/` with `X-CSRFToken` | `204`, session flushed |

- Only users holding `web_admin.access_web_console` get a session. Today that is the Super Admin group. Anyone else gets `403 not_console_user` and no session.
- The `sessionid` cookie is HttpOnly and SameSite=Lax. It is Secure on the server; `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` are off only in `dev.py`.
- The session ends after `CONSOLE_SESSION_IDLE_MINUTES` (30) without a request. Each request restarts the clock. After that, calls answer `401 not_authenticated`.
- The session remembers the user's `token_version`. A block, a reset or a password change in another session ends it with `401 session_revoked`.

### Login errors

| HTTP | `code` | When |
|---|---|---|
| 400 | `invalid_credentials` | Wrong username or password. An unknown username gets the same answer. |
| 403 | `account_blocked` | The account is blocked |
| 403 | `not_console_user` | Console login without `web_admin.access_web_console` |
| 403 | `permission_denied` | The account has no active role |
| 429 | `locked_out` | Too many failures; `retry_after_s` says how long is left |

### Forced password change

New accounts and reset accounts carry `must_change_password = true`. Until the official chooses a new password, every endpoint answers `403 password_change_required`, except these:

- `GET /api/v1/me/`
- `POST /api/v1/auth/password/change/`
- `POST /api/v1/auth/session/logout/`, `/auth/logout/` and `/auth/refresh/`

The same rule applies to sessions and to tokens. Clients send the official straight to the change-password screen when `/me` says `must_change_password`.

### Changing your own password

`POST /api/v1/auth/password/change/` `{"current_password", "new_password"}`.

- A wrong current password gives `400 invalid_credentials`.
- The new password must pass the rules in section 2 and differ from the current one.
- Every other session and token ends. For the console, the current session stays open. For the app, the response carries a fresh token pair.

---

## 2. Passwords

- Hashed with **Argon2** (`argon2-cffi`). Older PBKDF2 hashes still work and are upgraded at the next login. Tests use MD5 for speed only.
- Every password an official or an admin chooses passes Django's validators:
  - at least 8 characters;
  - not a common password;
  - not all digits;
  - not too similar to the username, name or email;
  - at least **one capital letter, one small letter, one number and one special character** (anything that is not a letter, a number or a space, such as `@ # $ % & * ! ?`). `apps/accounts/validators.py`, `PasswordCompositionValidator`.
- A failing password gives `400 validation_error`, with the reasons under `fields.new_password` or `fields.password`. One message names everything missing, for example "The password needs at least a capital letter (A-Z), a number (0-9)."
- Generated temporary passwords follow the same rules: 12 characters with one special character from `@#$%&*!?` (on a phone's first symbol keyboard), and no look-alikes (0/O, 1/l/I).
- The rules apply when a password is set. Passwords people already have keep working until they next change them.
- Passwords are never written to logs, the audit log or background jobs.

---

## 3. Lockout and unlock

django-axes counts failed logins per **username and IP address together**:

- 5 failures (`LOGIN_MAX_FAILURES`) lock that username from that IP address for 15 minutes (`LOGIN_LOCKOUT_MINUTES`).
- During the lockout every try answers `429 locked_out` with `retry_after_s`, even with the right password. Tries during the lockout do not extend it.
- A successful login clears the count.
- A lockout is audited as `auth.locked_out`, and each failure as `auth.login_failed`.

**Unlocking early:** an admin with `accounts.unlock_user` over the user's area, ranked above them, calls `POST /api/v1/users/{id}/unlock/`. This clears every lockout for that username and is audited as `user.unlocked`. A password reset (section 4) clears it too. User objects carry `"locked_out": true` and `unlock` in `allowed_actions` while a lockout is active. In the console the button is in the user panel.

From a shell on the server:

```bash
python manage.py axes_reset_username <username>
```

---

## 4. New accounts and forgotten passwords

There is no email or SMS in either flow. The admin hands the temporary password to the person directly.

### New account

`POST /api/v1/users/` `{"name", "name_te"?, "username", "mobile"?, "email"?, "preferred_language"?, "role", "area": {"level", "id"}, "password"?}`

- With `password`, that is the temporary password. It must pass the rules in section 2.
- Without it, the server generates one and returns it **once** as `temporary_password` in the `201` response. It cannot be read again later.
- The account starts with `must_change_password = true`.
- The admin needs the group's manage permission over the area (section 8). Audited as `user.created`, without the password.

### Forgotten password

The official asks a higher admin. An admin with `accounts.reset_password` over the official's area, ranked above them, calls:

`POST /api/v1/users/{id}/reset-password/` `{"password"?}`

- Without `password`, a generated one comes back once as `temporary_password`.
- `must_change_password` is set and `token_version` is bumped, so every session ends at once.
- Any lockout on that username is cleared, so the person can log in with the new password at once.
- Audited as `user.password_reset`, without the password.

**The first Super Admin** is made on the server. Nobody can give the Super Admin group through the API. See `deploy/RUNBOOK.md`:

```bash
python manage.py create_super_admin --username sita.admin --name "Sita Mahalakshmi"
```

It prints a temporary password once, and the person changes it at their first console login. The same command gives the role to an existing account.

---

## 5. Blocking

`POST /api/v1/users/{id}/block/` `{"reason"?}` needs `accounts.block_user` over the user's area and a higher rank.

- It sets `is_active = false` and bumps `token_version`. Every token and session fails on its next request with `401 session_revoked`.
- Logging in again gives `403 account_blocked`.
- `POST /api/v1/users/{id}/unblock/` reverses it.
- Audited as `user.blocked` and `user.unblocked`. Admins cannot block themselves.

---

## 6. Groups

Roles are Django groups (`django.contrib.auth.models.Group`). A small table, `RoleGroup`, stores what the code needs about each group, so no code tests a group's name.

| Key (sent to clients as `role`) | Group | Telugu | Assigned at | Rank | Given by holders of |
|---|---|---|---|---|---|
| `SUPER_ADMIN` | Super Admin | సూపర్ అడ్మిన్ | State, platform-wide | 0 | Nobody through the API |
| `DISTRICT_ADMIN` | District Admin | జిల్లా అడ్మిన్ | District | 1 | `accounts.manage_district_admin` |
| `MANDAL_ADMIN` | Mandal Admin | మండల అడ్మిన్ | Mandal | 2 | `accounts.manage_mandal_admin` |
| `VILLAGE_ADMIN` | Village Admin | గ్రామ అడ్మిన్ | Village | 3 | `accounts.manage_village_admin` |
| `PANCHAYAT_MEMBER` | Panchayat Member | పంచాయతీ సభ్యుడు | Village | 4 | `accounts.manage_panchayat_member` |

Rank 0 is the highest. The groups, their levels, ranks and permissions are written in one place: `ROLE_GROUPS` in `apps/accounts/role_matrix.py`.

**Role assignments.** An official holds one or more `RoleAssignment` rows: `user`, `group`, `area_level`, `area_id`, `is_active`, `assigned_by`, `assigned_at`. Each gives the group's permissions in that one area and the areas below it. Adding an assignment adds the user to the group; removing or deactivating the last one for that group takes them out of it.

**Second roles.** Any admin with the group's manage permission over that area may add a second role, in the app or the console.

---

## 7. Permission matrix

"yes" means the group holds the permission inside its assignment's area. The Super Admin's area is the whole platform.

| Permission | Super Admin | District Admin | Mandal Admin | Village Admin | Panchayat Member |
|---|---|---|---|---|---|
| `posts.create_post` | yes, if SUPER_ADMIN_CAN_POST | yes | yes | yes | yes |
| `posts.submit_post` | yes, if SUPER_ADMIN_CAN_POST | yes | yes | yes | yes |
| `posts.review_post` | yes | yes | yes | yes |  |
| `posts.publish_post` | yes | yes | yes | yes |  |
| `posts.recheck_post` | yes | yes | yes | yes |  |
| `posts.discard_post` | yes | yes | yes | yes |  |
| `posts.takedown_post` | yes | yes | yes | yes |  |
| `posts.reverse_decision` | yes | yes | yes | yes |  |
| `posts.view_review_queue` | yes | yes | yes | yes |  |
| `posts.mark_urgent` | yes | yes | yes | yes |  |
| `posts.view_stats` | yes | yes | yes | yes | yes |
| `posts.manage_categories` | yes |  |  |  |  |
| `posts.manage_settings` | yes |  |  |  |  |
| `accounts.manage_district_admin` | yes |  |  |  |  |
| `accounts.manage_mandal_admin` | yes | yes |  |  |  |
| `accounts.manage_village_admin` | yes | yes | yes |  |  |
| `accounts.manage_panchayat_member` | yes | yes | yes | yes |  |
| `accounts.block_user` | yes | yes | yes | yes |  |
| `accounts.reset_password` | yes | yes | yes | yes |  |
| `accounts.reassign_user` | yes | yes | yes | yes |  |
| `accounts.unlock_user` | yes | yes | yes | yes |  |
| `contacts.manage_contact` | yes | yes | yes | yes | yes |
| `accounts.manage_groups` | yes |  |  |  |  |
| `geography.manage_master_data` | yes |  |  |  |  |
| `audit.view_audit_log` | yes |  |  |  |  |
| `audit.export_audit_log` | yes |  |  |  |  |
| `web_admin.access_web_console` | yes |  |  |  |  |
| `web_admin.view_dashboard` | yes |  |  |  |  |
| `web_admin.view_reports` | yes |  |  |  |  |
| `notifications.manage_templates` | yes |  |  |  |  |
| `web_public.manage_contact_messages` | yes |  |  |  |  |
| `contacts.manage_contact_types` | yes |  |  |  |  |

Rules on top of the table:

- **Posting.** A Panchayat Member's post waits for review because they lack `posts.publish_post`. An admin's own post publishes at once when `ADMIN_POSTS_AUTO_PUBLISH` is on. The Super Admin posts only while `SUPER_ADMIN_CAN_POST` is true. The sync command and every request both check that setting.
- **Reversing a decision** needs `posts.reverse_decision` and a group ranked strictly above the one that decided.
- **First decision wins.** A second reviewer gets `409 already_decided`.
- **User actions** need the permission over the user's area and a group ranked strictly below the admin's own. This covers assigning, reassigning, removing, blocking, resetting and unlocking. An admin can never give a group at or above their own rank.
- **Console screens** follow the same permissions: Geography needs `geography.manage_master_data`, the Audit Log needs `audit.view_audit_log`, and so on. The console hides what the user lacks; the server still refuses it.
- **Important numbers** need `contacts.manage_contact` over the number's area, so every official works inside their own area and below. Only a platform-wide assignment covers the STATE, so only the Super Admin edits the statewide helplines; everyone else reads them. The CSV import needs the permission over the whole platform.

---

## 8. Area scoping

Areas nest: State > District > Mandal > Village. An assignment covers its own area and everything below it. A Super Admin assignment is platform-wide and covers everything.

`authz.can(user, perm, target)` is true only when all of these hold:

1. The user is logged in and active.
2. No forced password change is pending.
3. At least one **active** assignment's group grants `perm`, and that assignment covers `target`.

Targets:

| Target | Meaning |
|---|---|
| An area, `AreaRef(level, id)` | The assignment's area is that area or one of its ancestors |
| `authz.PLATFORM` | Only a platform-wide assignment covers it. Used for Super Admin screens. |
| `authz.ANYWHERE` | The permission is held in any area. Used for "may this user open this screen at all". |

Examples for a Mandal Admin of Tenali:

| Target | `posts.publish_post` |
|---|---|
| Tenali mandal | yes |
| A village in Tenali | yes |
| Guntur district, the parent | no |
| A sibling mandal | no |
| Any area in another district | no |

**Lists.** `authz.areas_for(user, perm)` returns the areas where the user holds the permission, or `authz.ALL_AREAS`. `authz.area_q(user, perm, level_field, id_field)` turns that into a `Q` for a queryset. Lists are filtered on the server; a client filter can narrow a list, never widen it. Objects outside the user's areas answer `404`, so their existence is not revealed.

**Two assignments.** An official with assignments in two areas gets each group's permissions only in its own area. A Village Admin of one village and a Panchayat Member of another can publish only in the first.

**Speed.** Two queries load a user's assignments and permissions once per request; the result is cached on the user object. Area ancestry is loaded in batches, so a list makes no query per row. Tests assert the query counts.

---

## 9. Checking permissions in code

- **Views:** `HasPerm("app_label.codename", target)` from `apps/accounts/permissions.py`. `has_permission` asks `authz.can(user, perm, target)`; `has_object_permission` checks the object's area.

  ```python
  permission_classes = [IsAuthenticated, HasPerm("audit.view_audit_log", PLATFORM)]
  permission_classes = [IsAuthenticated, HasPerm("posts.view_review_queue")]   # held in any area
  ```

- **Services:** `authz.can(request.user, "posts.publish_post", post.target)`.
- **Querysets:** `queryset.filter(authz.area_q(user, "posts.review_post", "target_level", "target_area_id"))`.
- **User management:** `authz.can_act_on(actor, perm, target_user)` and `authz.can_assign(actor, role, area)` apply the rank rule.
- **Clients:** objects carry `allowed_actions`, and `GET /api/v1/me/` returns `permissions` and `permissions_by_area`. Clients show buttons from them and never repeat the rules.

Never test a group's name or key in code. Only `role_matrix.py`, the data migrations and the sync command may. Ask for a permission instead.

---

## 10. How to add a permission

1. Add it to the right model's `Meta.permissions`, for example `("approve_media", "Can approve media")` on `Post`.
2. Run `python manage.py makemigrations <app>` and commit the migration.
3. Add `"<app_label>.approve_media"` to the groups that need it in `ROLE_GROUPS`, `apps/accounts/role_matrix.py`.
4. Run `python manage.py migrate`. It ends with the same sync as step 5 below.
5. Ask for it in code with `HasPerm` or `authz.can`.
6. Add the permission to the matrix test and to the table in section 7. The matrix test fails until both agree.

A permission named in `ROLE_GROUPS` that no model defines stops the sync with an error, so a typo cannot pass silently.

---

## 11. Running sync_role_permissions

```bash
python manage.py sync_role_permissions
```

- It makes the database match `ROLE_GROUPS`: it creates missing groups and `RoleGroup` rows, sets each group's level, rank and Telugu name, and sets each group's permissions to exactly the list in the dict.
- It applies `SUPER_ADMIN_CAN_POST`: when the setting is off, the Super Admin group does not get the posting permissions.
- It is idempotent. A second run changes nothing and says so.
- `migrate` runs the same sync at the end, so every deploy applies the dict.
- Run it by hand after changing `SUPER_ADMIN_CAN_POST`, or after someone edited group permissions in Django admin and you want the dict back.

---

## 12. Django admin

- Only users holding `accounts.manage_groups` on the platform, the Super Admins, can see or edit Groups, `RoleGroup` rows and role assignments in `/django-admin/`.
- Every change to a group's permissions is audited as `setting.group_permissions_changed`, with what was added and removed.
- An edit there lasts until the next `migrate` or `sync_role_permissions`, which restores the dict. Change `role_matrix.py` for a lasting change.
- On the server, `/django-admin/` is reachable only from `OFFICE_IPS`.

---

## 13. Audit events

| Action | When |
|---|---|
| `auth.login` | Successful login, with `via` app or console |
| `auth.login_failed` | Wrong password or unknown username |
| `auth.login_refused` | Right password, but blocked, no role, or not a console user |
| `auth.locked_out` | A username and IP pair is locked |
| `user.unlocked` | An admin unlocked a user |
| `user.password_changed` | An official changed their own password |
| `user.password_reset` | An admin reset someone's password |
| `user.created`, `user.updated` | Accounts made or edited |
| `user.blocked`, `user.unblocked` | Blocking |
| `user.assignment_added`, `user.assignment_removed` | Roles given, moved or taken away |
| `setting.group_permissions_changed` | Group permissions edited in Django admin |
| `contact.created`, `contact.updated`, `contact.removed` | An important number added, edited or verified, or removed; the phone is masked (`******0001`) |
| `contact_type.created`, `contact_type.updated` | Contact types managed by the Super Admin |
| `contacts.imported` | A CSV import of important numbers committed; counts only |

No audit entry holds a password. The console shows these under Audit Log, tab Logins and tab Users.

---

## 14. Settings

| Setting (env var) | Default | Meaning |
|---|---|---|
| `LOGIN_MAX_FAILURES` | 5 | Failures before a username and IP pair is locked |
| `LOGIN_LOCKOUT_MINUTES` | 15 | Lockout length |
| `CONSOLE_SESSION_IDLE_MINUTES` | 30 | Console idle timeout |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | on (off in dev) | Cookies only over HTTPS |
| `CSRF_TRUSTED_ORIGINS` | none | The server's https origin, in `prod.py` |
| `SUPER_ADMIN_CAN_POST` | off | Whether the Super Admin may post |
| `ADMIN_POSTS_AUTO_PUBLISH` | on | Admin posts publish without review |

JWT lifetimes are in `SIMPLE_JWT`: access 15 minutes, refresh 30 days, rotated and blacklisted.
