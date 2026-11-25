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

Officials log in with a **username and a password**. There is no OTP, no SMS and no email login. The username is unique, ignores case, surrounding spaces and extra spaces between words, and is stored in lower case; it may contain single spaces between words, such as `ravi kumar` (2026-11-17). An admin managing the user can change it (`PATCH /users/{id}/`, audited as `user.username_changed`). Email and mobile number are optional contact details only and never work as a login name.

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
- The `sessionid` cookie is HttpOnly and SameSite=Lax. It is Secure on the server; `SESSION_COOKIE_SECURE` and `CSRF_COOKIE_SECURE` are off only in development (`DEBUG` on in `grama/settings.py`); with `DEBUG=False` they are always on.
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
  - at least **12 characters for every official**, Panchayat members included (decision of 2026-11-19; from security gap G12 of 2026-11-03 until then, 12 for admins and 8 for Panchayat members). Each role's minimum is `password_min_length` in `apps/accounts/role_matrix.py` (`PASSWORD_MIN_LENGTH`), kept on `RoleGroup`; someone holding several roles needs the longest. Checked by `credentials.check_new_password`, also for the role an admin is giving a new account; Django's `MinimumLengthValidator` asks the same 12;
  - not a common password;
  - not all digits;
  - not too similar to the username, name or email;
  - at least **one capital letter, one small letter, one number and one special character** (anything that is not a letter, a number or a space, such as `@ # $ % & * ! ?`). `apps/accounts/validators.py`, `PasswordCompositionValidator`.
- A failing password gives `400 validation_error`, with the reasons under `fields.new_password` or `fields.password`. One message names everything missing, for example "The password needs at least a capital letter (A-Z), a number (0-9)."
- Generated temporary passwords follow the same rules: 12 characters with one special character from `@#$%&*!?` (on a phone's first symbol keyboard), and no look-alikes (0/O, 1/l/I).
- The rules apply when a password is set. Passwords people already have keep working until they next change them, with one exception: at login, a password shorter than the person's roles now need (set before G12, a Panchayat member's 8 to 11 characters from before 2026-11-19, or a higher role given later) turns on `must_change_password`, so they choose a long enough one before doing anything else. That is audited as `auth.password_too_short`, without the password.
- Passwords are never written to logs, the audit log or background jobs.

---

## 3. Lockout and unlock

django-axes counts failed logins per **username and IP address together**:

- 5 failures (`LOGIN_MAX_FAILURES`) lock that username from that IP address for 15 minutes (`LOGIN_LOCKOUT_MINUTES`).
- Ceiling per username (WEB-SEC-019, lead's decision of 2026-11-25): 20 failures (`LOGIN_MAX_FAILURES_PER_USERNAME`; 0 turns it off) for one username within 60 minutes (`LOGIN_USERNAME_WINDOW_MINUTES`), from any addresses, lock that username from every address for 15 minutes after the last of them. They are counted from axes' failure log (`apps/accounts/lockout.py`).
- During either lockout every try answers `429 locked_out` with `retry_after_s`, even with the right password. Tries during a lockout are not counted and do not extend it.
- A successful login clears both counts; so do an unlock and a password reset.
- A lockout is audited as `auth.locked_out` (with `"scope": "username"` for the ceiling), each failure as `auth.login_failed`, and each try refused by the ceiling as `auth.login_failed` with `"locked": true`.
- axes' tables store a keyed hash of the username (`lockout.login_key`, HMAC-SHA256 with `SECRET_KEY`), never what was typed: a password typed into the username box must not stay there for 30 days. So axes' own `axes_reset_username` command needs the hash; use the shell command below. Changing `SECRET_KEY` only clears current lockouts.

**Cap per address** (removed on 2026-11-20, back before going live, BVTGC-105): 50 failed logins (`LOGIN_IP_MAX_FAILURES`; 0 turns it off) from one IP address within 60 minutes (`LOGIN_IP_WINDOW_MINUTES`), for any usernames, block every app and console login from that address until the oldest of them leaves the window (`429 locked_out` with `retry_after_s`). Tries while blocked are not counted; a successful login or an unlock does not clear it, since it is about an address, not a user. Audited once as `auth.ip_locked_out`, never with the IP. The address is the one `grama.http.client_ip` gives, which believes `X-Real-IP` only from the server's own network (BE-SEC-101). The login throttle of security gap G1 (30 logins an hour per IP) is not back: not decided (`docs/security-review.md`, open item 8).

**Unlocking early:** an admin with `accounts.unlock_user` over the user's area, ranked above them, calls `POST /api/v1/users/{id}/unlock/`. This clears every lockout for that username and is audited as `user.unlocked`. A password reset (section 4) clears it too. User objects carry `"locked_out": true` and `unlock` in `allowed_actions` while a lockout is active. In the console the button is in the user panel.

From a shell on the server:

```bash
python manage.py shell -c "from apps.accounts.lockout import unlock; from apps.accounts.models import User; print(unlock(User.objects.get(username='<username>')))"
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

**The first Super Admin** is made on the server. See `deploy/RUNBOOK.md`:

```bash
python manage.py create_super_admin --username sita.admin --name "Sita Mahalakshmi"
```

It prints a temporary password once, and the person changes it at their first console login. The same command gives the role to an existing account.

### More Super Admins (plan of 2026-11-06)

After the first one, **any Super Admin may make another Super Admin**, in the console or the app (Users → Add user, role Super Admin), or give the role to an existing official. It is the same `POST /api/v1/users/` or `POST /api/v1/users/{id}/role-assignments/`, at the state, plus the actor's own password. A Super Admin can do everything, so the rules are stricter (`apps/accounts/peers.py`):

| # | Rule | Answer |
|---|---|---|
| S1 | The actor confirms with their **own password**, `current_password`. Wrong tries count with those on "Change password" (section 1): too many end every session. | missing: `400 validation_error`; wrong: `403 password_confirmation_required` |
| S2 | **Never yourself:** no block, reset, unlock or role change on your own account. | the user is not in your list: `404` |
| S3 | **Never zero:** no block, role removal or switch-off that would leave no active Super Admin. | `409 last_super_admin` |
| S4 | **Limit:** at most `MAX_SUPER_ADMINS` active Super Admins (default 5, 0 for no limit). Blocked ones do not count; unblocking or switching the role on again counts. | `409 super_admin_limit` |
| S5 | **Strong password:** 12 characters, changed at first login, as for every admin. | section 2 |
| S6 | **Audit:** `user.super_admin_created`, `user.super_admin_role_added`, `user.super_admin_role_removed`. | section 13 |
| S7 | **The others are told:** every other active Super Admin gets an inbox item and a push (template `super_admin_added`, editable in the console). | |
| S8 | **Peers:** a Super Admin may block, unblock, unlock and reset another Super Admin, so a lost or leaving admin is handled without the server. | |

Only the Super Admin group has peers. It is the one platform-wide group, and `authz.outranks()` lets a platform-wide group manage its own rank; every other group still manages only groups strictly below it, so a District Admin never makes a District Admin.

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
| `SUPER_ADMIN` | Super Admin | సూపర్ అడ్మిన్ | State, platform-wide | 0 | `accounts.manage_super_admin`: another Super Admin, with their own password (section 4) |
| `DISTRICT_ADMIN` | District Admin | జిల్లా అడ్మిన్ | District | 1 | `accounts.manage_district_admin` |
| `MANDAL_ADMIN` | Mandal Admin | మండల అడ్మిన్ | Mandal | 2 | `accounts.manage_mandal_admin` |
| `VILLAGE_ADMIN` | Village Admin | గ్రామ అడ్మిన్ | Village | 3 | `accounts.manage_village_admin` |
| `PANCHAYAT_MEMBER` | Panchayat Member | పంచాయతీ సభ్యుడు | Village | 4 | `accounts.manage_panchayat_member` |
| `CITIZEN` | Citizen | పౌరుడు | Village | 5 | Nobody: given at sign-up to a citizen's wallet account (BVTGC-106) |

Rank 0 is the highest.

**Citizen wallet accounts (since BVTGC-121).** A citizen may open an optional account for the wallet of points and referrals (`docs/api-contract.md`, section 21). It holds the `CITIZEN` group for the village chosen in the app, with one permission, `wallet.use_wallet`. It is **never an official**:
- the officials' login (`auth/login/`, `auth/session/login/`) refuses it with `400 invalid_credentials`, the same answer as a wrong password, even when it is blocked;
- the officials' endpoints that need no particular permission (`GET /me/`, `POST /auth/password/change/`, the notification inbox, `/posts/`) answer it `403 permission_denied` (`IsOfficialAccount` in `apps/accounts/permissions.py`);
- "any official role" means holding `accounts.use_official_api`, which every official group has and `CITIZEN` has not: the Telugu name suggestions and the published posts an official sees above their area (`authz.official_grants`);
- nobody can give the group through `users/`, so citizen accounts never appear in an official's user list, and the dashboard counts officials only.

Code asks `authz.is_citizen_account(user)`, which looks for the account's `CitizenAccount` row, never the group's key. The groups, their levels, ranks and permissions are written in one place: `ROLE_GROUPS` in `apps/accounts/role_matrix.py`.

**Role assignments.** An official holds one or more `RoleAssignment` rows: `user`, `group`, `area_level`, `area_id`, `is_active`, `assigned_by`, `assigned_at`. Each gives the group's permissions in that one area and the areas below it. Adding an assignment adds the user to the group; removing or deactivating the last one for that group takes them out of it.

**Second roles.** Any admin with the group's manage permission over that area may add a second role, in the app or the console.

---

## 7. Permission matrix

"yes" means the group holds the permission inside its assignment's area. The Super Admin's area is the whole platform.

| Permission | Super Admin | District Admin | Mandal Admin | Village Admin | Panchayat Member |
|---|---|---|---|---|---|
| `accounts.use_official_api` | yes | yes | yes | yes | yes |
| `posts.create_post` | yes, if SUPER_ADMIN_CAN_POST | yes | yes | yes | yes |
| `posts.submit_post` | yes, if SUPER_ADMIN_CAN_POST | yes | yes | yes | yes |
| `posts.review_post` | yes | yes | yes | yes |  |
| `posts.publish_post` | yes | yes | yes | yes |  |
| `posts.recheck_post` | yes | yes | yes | yes |  |
| `posts.discard_post` | yes | yes | yes | yes |  |
| `posts.takedown_post` | yes | yes | yes | yes | yes |
| `posts.reverse_decision` | yes | yes | yes | yes |  |
| `posts.view_review_queue` | yes | yes | yes | yes |  |
| `posts.mark_urgent` | yes | yes | yes | yes |  |
| `posts.block_citizen` | yes |  |  |  |  |
| `posts.view_stats` | yes | yes | yes | yes | yes |
| `posts.manage_categories` | yes |  |  |  |  |
| `posts.manage_settings` | yes |  |  |  |  |
| `accounts.manage_super_admin` | yes |  |  |  |  |
| `accounts.manage_district_admin` | yes |  |  |  |  |
| `accounts.manage_mandal_admin` | yes | yes |  |  |  |
| `accounts.manage_village_admin` | yes | yes | yes |  |  |
| `accounts.manage_panchayat_member` | yes | yes | yes | yes |  |
| `accounts.block_user` | yes | yes | yes | yes |  |
| `accounts.reset_password` | yes | yes | yes | yes |  |
| `accounts.reassign_user` | yes | yes | yes | yes |  |
| `accounts.unlock_user` | yes | yes | yes | yes |  |
| `contacts.manage_contact` | yes | yes | yes | yes | yes |
| `service_requests.view_service_requests` | yes | yes | yes | yes | yes (only those assigned to them) |
| `service_requests.manage_service_requests` | yes | yes | yes | yes |  |
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
| `ads.manage_ad` | yes |  |  |  |  |
| `service_requests.manage_services` | yes |  |  |  |  |
| `bills.manage_bills` | yes |  |  |  |  |
| `accounts.manage_citizen_accounts` | yes |  |  |  |  |
| `wallet.view_wallets` | yes |  |  |  |  |
| `wallet.adjust_points` | yes |  |  |  |  |
| `wallet.review_referrals` | yes |  |  |  |  |

The `CITIZEN` group holds only `wallet.use_wallet`, which no official group holds (section 6).

Rules on top of the table:

- **Posting.** A Panchayat Member's post waits for review because they lack `posts.publish_post`. An admin's own post publishes at once when `ADMIN_POSTS_AUTO_PUBLISH` is on. The Super Admin posts only while `SUPER_ADMIN_CAN_POST` is true. The sync command and every request both check that setting.
- **Posting goes anywhere (owner decision of 2026-10-28).** `posts.create_post` and `posts.submit_post` are the one exception to area scoping: whoever holds them in any area may post to Everyone or to any districts, mandals and villages. The post goes out under the role that covers its first area, else the poster's highest role, and that role alone decides whether it publishes at once. Citizens do not choose: a citizen's post is always for the phone's village (owner's decision of 2026-11-19, contract section 15).
- **Reviewing a post for several areas.** Every other post permission (review, publish, recheck, discard, take down, reverse, urgent, block the citizen) is checked over each of the post's areas, and holding it over **any** one is enough. So the admins of every chosen area review it, and a post to Everyone only the Super Admin. The first decision counts for the whole post.
- **Take-down rank rules (owner decision of 2026-11-17).** Holding `posts.takedown_post` allows taking down published posts subject to author rank:
  | Who takes down | Citizen post | Panchayat member post | Village/Mandal/District Admin post | Super Admin post | Own post |
  |---|---|---|---|---|---|
  | **Super Admin** | yes | yes | yes | yes | yes |
  | **District / Mandal / Village Admin** (in their area) | yes | yes | no | no | yes |
  | **Panchayat member** (their village) | yes | no (other members) | no | no | yes |
  Officials may also take down their own published post anywhere (even if targeting an area outside their assignment).
- **Seeing published posts above your area (backend plan of 2026-11-20).** Every official, whatever their group, sees the published posts for any area that is or contains one of their assignments' areas (their village, mandal, district, Everyone), as the citizens there do (`apps.posts.services.covering_scope_q`). It is read only and needs no permission: every action on such a post keeps its own check above, so it adds no button. Only official assignments count (`authz.official_grants`): a citizen's wallet role never shows posts (BVTGC-121). Only published posts; pending and decided ones above the area stay hidden.
- **Reversing a decision** needs `posts.reverse_decision` and a group ranked strictly above the one that decided. A Super Admin may reverse any decision, another Super Admin's and their own included (owner's decision of 2026-11-21). Reversing a published post (moving it to Pending or Discarded) is also gated by the take-down rank rule.
- **First decision wins.** A second reviewer gets `409 already_decided`.
- **User actions** need the permission over the user's area and a group ranked strictly below the admin's own. This covers assigning, reassigning, removing, blocking, resetting and unlocking. An admin can never give a group at or above their own rank. The one exception: Super Admins act on other Super Admins and give the role, with the rules of section 4, "More Super Admins".
- **Console screens** follow the same permissions: Geography needs `geography.manage_master_data`, the Audit Log needs `audit.view_audit_log`, and so on. The console hides what the user lacks; the server still refuses it.
- **Service requests: only the assignee changes one.** Changing the status, writing a note and passing it on belong to the official it is assigned to, whatever their group (a Panchayat Member included); every other official who sees it only reads it. `service_requests.manage_service_requests` over the village only lets an admin assign a request **nobody holds**: never assigned (the village has no Village Admin), or its assignee was blocked or lost their role over the village.
- **Important numbers** need `contacts.manage_contact` over the number's area, so every official works inside their own area and below. Only a platform-wide assignment covers the STATE, so only the Super Admin edits the statewide helplines; everyone else reads them. The CSV import needs the permission over the whole platform.
- **Number types** (add, rename, switch off and delete an unused one) need `contacts.manage_contact_types` over the whole platform: only the Super Admin.

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
- On servers, `/django-admin/` is off (404) since 2026-11-11 (lead's decision): `DJANGO_ADMIN_ENABLED` is off whenever `DEBUG` is off. The points above apply on laptops, or on a server whose `.env` turns it on.

---

## 13. Audit events

| Action | When |
|---|---|
| `auth.login` | Successful login, with `via` app or console |
| `auth.login_failed` | Wrong password or unknown username |
| `auth.login_refused` | Right password, but blocked, no role, not a console user, or a citizen's wallet account at the officials' login (`"reason": "citizen_account"`, BVTGC-121) |
| `auth.locked_out` | A username and IP pair is locked, or (`"scope": "username"`) a username is locked from every address by the ceiling per username |
| `auth.ip_locked_out` | An IP address reached the cap per address (written again since BVTGC-105; not written from 2026-11-20 to 2026-11-25). It holds the limit and window, never the IP |
| `user.unlocked` | An admin unlocked a user |
| `citizen.account_created` | A citizen finished the wallet sign-up with the email code (BVTGC-123): the village and whether a referral code was used |
| `citizen.code_sent` | A wallet code went by email to a citizen (BVTGC-122): the purpose, the channel and the masked address, never the code or the full address |
| `user.password_changed` | An official changed their own password |
| `user.password_reset` | An admin reset someone's password |
| `user.created`, `user.updated` | Accounts made or edited |
| `user.blocked`, `user.unblocked` | Blocking |
| `user.assignment_added`, `user.assignment_removed` | Roles given, moved or taken away |
| `user.super_admin_created`, `user.super_admin_role_added`, `user.super_admin_role_removed` | A Super Admin made a new Super Admin, gave the role (or switched it on again), or removed or switched it off |
| `auth.password_change_failed` | A wrong current password on "Change password", or a wrong own password when giving the Super Admin role (`data.for`: `give_super_admin`) |
| `setting.group_permissions_changed` | Group permissions edited in Django admin |
| `contact.created`, `contact.updated`, `contact.removed` | An important number added, edited or verified, or removed; the phone is masked (`******0001`) |
| `contact_type.created`, `contact_type.updated` | Contact types managed by the Super Admin |
| `contacts.imported` | A CSV import of important numbers committed; counts only |
| `citizen.registered` | A citizen's phone got its posting token; platform, whether Play Integrity was checked and whether it is a reinstall; never the IP, the Android ID or the token |
| `citizen.blocked`, `citizen.unblocked` | The Super Admin blocked (reason, days, end) or unblocked the phone behind a citizen post, or a phone from the blocked citizens list |
| `citizen.block_ended` | A phone's block reached its end date (actor System; the scheduled job, 2026-11-21) |
| `citizen.posts_discarded` | The Super Admin discarded all pending posts of one phone; the reason and how many |
| `service.created`, `service.updated` | The Super Admin added a service (its key and whether it has a logo) or changed one (the changed field names, `logo` for a new or removed logo) |
| `service.shown`, `service.hidden` | The Super Admin showed or hid a service on the citizens' Home |
| `service.reordered` | The Super Admin put the services in a new order; the keys in order |
| `service_request.created` | A citizen's phone sent a service request; the service and the device id, never the name, phone, email or address |
| `service_request.status_changed` | An official changed a service request's status; from, to and the note |
| `service_request.assigned` | The request was assigned or unassigned; the new assignee, and `auto` (true for the automatic assignment to the Village Admin on create) |
| `ad.created`, `ad.updated`, `ad.deleted` | The Super Admin made, changed (the changed field names) or deleted a Home banner ad, with its area |

No audit entry holds a password. The console shows these under Audit Log, tab Logins and tab Users.

---

## 14. Settings

| Setting (env var) | Default | Meaning |
|---|---|---|
| `LOGIN_MAX_FAILURES` | 5 | Failures before a username and IP pair is locked |
| `LOGIN_LOCKOUT_MINUTES` | 15 | Lockout length (both lockouts) |
| `LOGIN_MAX_FAILURES_PER_USERNAME` | 20 | Failures for one username from any addresses before it is locked from every address; 0 turns the ceiling off |
| `LOGIN_USERNAME_WINDOW_MINUTES` | 60 | The window those failures are counted in |
| `CONSOLE_SESSION_IDLE_MINUTES` | 30 | Console idle timeout |
| `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` | on (off in dev) | Cookies only over HTTPS |
| `CSRF_TRUSTED_ORIGINS` | none | The server's https origin, in the server's `.env` |
| `SUPER_ADMIN_CAN_POST` | on (owner decision of 2026-11-04) | Whether the Super Admin may post |
| `MAX_SUPER_ADMINS` | 5 (until the owner decides) | Most active Super Admins at once; 0 means no limit (section 4) |
| `ADMIN_POSTS_AUTO_PUBLISH` | on | Admin posts publish without review |

JWT lifetimes are in `SIMPLE_JWT`: access 15 minutes, refresh 30 days, rotated and blacklisted.
