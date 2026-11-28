# Authorization migration: survey

Step 1 of moving authorization to Django Groups and Permissions with area scoping. Sections 1 to 4 describe the code before the change. Section 5 records the owner's decisions; steps 2 to 7 are built, and `authorization.md` describes the result.

Date: 21 October 2026. Repos surveyed: BVTGramaConnect_BackEnd (`dev`), BVTGramaConnect (`dev`), BVTGramaConnect_Mobile (`feature/api-integration`, the API-connected app, and `dev`, the mobile developer's app). Line numbers are as of those commits.

## 1. Summary

- **Access today is role-string based.** `apps/accounts/roles.py` defines five role strings and three tables of what they may do. `apps/accounts/scope.py` turns a user's role assignments into area checks. Every view and service asks those two modules. Django Groups and Permissions are not used at all.
- **There are five permission classes**, four of them a duplicated "is Super Admin" check, plus about 40 role checks inside services and views.
- **The clients make no security decisions.** The console and the API-connected app only show or hide buttons from `capabilities` and `allowed_actions` in `/me` and post objects. The mobile developer's app has no backend connection and decides everything locally from role strings.
- **OTP and SMS login code is already gone from the backend.** What is left is historical migrations, a test proving the old endpoints are gone, internal names in the console and app, the mobile developer's mock OTP screens, and wording in docs and the Repo folder's CLAUDE.md.
- **Several parts of the requested design differ from decisions taken on 21 October** (email invites, role default passwords, the lockout mechanism, URL paths, the console session). They are listed in section 5 and need your answer before step 2.

## 2. Where access is decided today

### 2.1 Backend: the role model

| File:line | What it decides |
|---|---|
| `apps/accounts/roles.py:10-24` | The five role strings and their order (highest first). |
| `apps/accounts/roles.py:27-33` | `ROLE_LEVEL`: the area level each role is assigned at. |
| `apps/accounts/roles.py:36-42` | `MANAGEABLE_ROLES`: which roles each role may create, block, reset and reassign. |
| `apps/accounts/roles.py:44` | `REVIEW_ROLES`: roles that review posts. |
| `apps/accounts/roles.py:46` | `POSTING_ROLES`: roles that post (Super Admin added only with `SUPER_ADMIN_CAN_POST`). |
| `apps/accounts/models.py:161-205` | `RoleAssignment(user, role, area_level, area_id)`; `clean()` at 200 checks the level against `ROLE_LEVEL`. |
| `apps/accounts/models.py:107-158` | `status` field (ACTIVE or BLOCKED); `is_active` is a property derived from it. |
| `apps/accounts/scope.py:35-55` | `user_areas()`: the user's (role, area) pairs, cached per user object. |
| `apps/accounts/scope.py:62-71` | `is_within()`: area containment; a Super Admin assignment covers everything (line 32). |
| `apps/accounts/scope.py:74-79` | `has_role()`, `is_super_admin()`. |
| `apps/accounts/scope.py:82-94` | `manageable_roles()`, `can_manage()`. |
| `apps/accounts/scope.py:97-127` | `manageable_assignment_q()`, `manageable_users()`: SQL filters for user lists. |
| `apps/accounts/scope.py:129-130` | `role_matches_level()`. |
| `apps/accounts/scope.py:133-145` | `capabilities()`: the flags sent to clients in `/me`. |
| `apps/geography/areas.py:74, 87` | `area_is_within()`, `within_q()`: area containment in Python and SQL (stays as is). |

### 2.2 Backend: permission classes

| File:line | Class | Used by |
|---|---|---|
| `apps/accounts/views_auth.py:217` | `IsSuperAdmin` | role default passwords (225, 234) |
| `apps/accounts/views_users.py:54` | `ManagesUsers` (any manageable role) | users (102), role assignments (312) |
| `apps/posts/views.py:46` | `IsSuperAdmin` | admin categories (86), admin settings (373); imported by `apps/audit/views.py:23` (96, 131) and `apps/web_public/api.py:30` (206, 232) |
| `apps/notifications/views.py:132` | `IsSuperAdmin` | notification templates (140, 147) |
| `apps/web_admin/permissions.py:8` | `IsSuperAdmin` | dashboard and reports (`apps/web_admin/api.py:102`), geography writes, tree and import (`apps/geography/views_admin.py:86, 207, 244, 258`) |

All other authenticated views use only `IsAuthenticated` and check roles inside the view or service (2.3).

### 2.3 Backend: checks inside views and services

| File:line | Check |
|---|---|
| `apps/accounts/views_auth.py:87-94, 124` | Console login allowed only if `is_super_admin()`; otherwise `not_console_user`. |
| `apps/accounts/views_users.py:61-80` | `check_assignment_allowed()`: role matches level, area exists, role in `manageable_roles()`, `can_manage()` for the area. |
| `apps/accounts/views_users.py:108, 316` | Users and role-assignment lookups limited to `manageable_users()`. |
| `apps/accounts/views_users.py:134, 153` | List filters by area (`within_q`) and by role string. |
| `apps/accounts/serializers.py:123-124, 226` | `capabilities` and user `allowed_actions` sent to clients. |
| `apps/accounts/credentials.py:72-75` | `highest_role()` picks which role default password a reset uses. |
| `apps/accounts/tasks.py:56-61` | Invite email text chosen by role; Super Admin gets the console link. |
| `apps/posts/services.py:40-47` | Role rank table used to decide who may reverse a decision. |
| `apps/posts/services.py:120-138` | `posting_roles()`, `posting_assignment()`: who may post where; Panchayat members only to their village (132). |
| `apps/posts/services.py:140-169` | `reviewer_assignment()`, `review_scope_q()`, `visible_posts()`, review queue scope. |
| `apps/posts/services.py:171-180` | `can_reverse()`: strictly higher rank, Super Admin always. |
| `apps/posts/services.py:182-200` | Post `allowed_actions` for clients. |
| `apps/posts/services.py:245-257` | Auto-publish unless the poster is a Panchayat member. |
| `apps/posts/services.py:317` | Panchayat members cannot mark posts urgent. |
| `apps/posts/services.py:424-426, 516-518` | Review and urgent: out_of_area if the user reviews elsewhere, permission_denied otherwise. |
| `apps/posts/services.py:563-578` | `/posts/targets/`: visibility levels per role. |
| `apps/posts/views.py:301` | Review queue requires a review role. |
| `apps/posts/views.py:329-345` | `/stats/` area check; Super Admin sees the whole state. |
| `apps/web_admin/api.py:38-44, 148, 244` | Reports count admins by role string. |
| `apps/notifications/services.py:38-40, 66-68` | Who gets "pending review" and escalation alerts: chosen by role string and area. |
| `apps/audit/services.py:30` | Records the actor's role strings on every audit entry. |
| `apps/web_public/api.py:250` | Writes `actor_role="SUPER_ADMIN"` on contact-message audit entries. |
| `apps/accounts/management/commands/seed_sample_data.py:54-58, 97` | Sample officials by role string. |

### 2.4 Backend: Django admin

| File:line | Today |
|---|---|
| `apps/accounts/models.py:61-67, 120` | `is_staff` and `is_superuser` are developer flags for Django admin, separate from the SUPER_ADMIN role. |
| `apps/accounts/admin.py:36-44` | User admin shows `groups` and `user_permissions`, but nothing uses them. Any staff user with model permissions could edit Groups and RoleAssignments. |

### 2.5 Web console (BVTGramaConnect)

`console/src/index.html` is generated from `console/tools/logic.js`; edit the latter.

| File:line | What it does | Security decision? |
|---|---|---|
| `console/tools/logic.js:4` | `ROLE` colours and labels per role key | No, display |
| `console/tools/logic.js:7` | `RK`: API role string to display key | No, display |
| `console/tools/logic.js:8` | `RAPI`: form value to API role string (Add User, filters) | No, form values |
| `console/tools/logic.js:36` | On `password_change_required`, show the change-password screen | No, follows the server |
| `console/tools/logic.js:55-57` | Login and password change calls (`client:'web_admin'`) | No, the server refuses non-Super Admins |
| `console/tools/logic.js:67, 83, 95, 143` | Role labels on posts, history and audit rows | No, display |
| `console/tools/logic.js:185` | `RL`: role names for the Add role modal | No, display |
| `console/tools/logic.js` (posts and users) | Buttons follow `allowed_actions` from the server | No, follows the server |

### 2.6 Public website (BVTGramaConnect `src/`)

No access decisions. The admin login card's button opens `/console/` (`src/index.html`, `adminOtp` handler, which now just navigates).

### 2.7 Flutter app on `feature/api-integration`

| File:line | What it does |
|---|---|
| `lib/core/api/models.dart:254-299` | Reads `capabilities` (can_post, can_review, manageable_roles, is_super_admin) and `must_change_password` from `/me`. |
| `lib/core/api/models.dart:173-229` | Reads `allowed_actions` on posts and users. |
| `lib/features/onboarding/onboarding_screens.dart:83-90` | `homeFor()`: picks the role's home screen. |
| `lib/core/state/providers.dart:134` | Role chooser for users with several assignments. |
| `lib/features/official/official_screens.dart:212` | `session.role == 'PANCHAYAT_MEMBER'` changes the compose button text. |
| `lib/features/users/users_screens.dart:78, 219, 263` | Role tabs and area root from `manageable_roles`; Super Admin starts at state level. |
| `lib/features/superadmin/superadmin_screens.dart:157` | "New post" only when `can_post`. |
| `lib/core/l10n.dart:15-20`, `lib/core/theme/tokens.dart:75-81` | Role names and colours. Display only. |

None of these protect anything; the server checks every action.

### 2.8 Flutter app on `dev` (mobile developer's app)

Runs on built-in sample data and makes no backend calls, so every check below is local and not a security boundary.

| File:line | Check |
|---|---|
| `lib/features/grama/state/app_state.dart:606` | `role == 'super'` |
| `lib/features/grama/state/app_state.dart:619-627` | Own posts and `allowedRoles()` by role string |
| `lib/features/grama/state/app_state.dart:646, 868-869, 880, 944-950, 978` | Screens, areas and lists chosen by role string |
| `lib/features/grama/presentation/screens/panchayat_screens.dart:162-164` | Target picker and submit label by role |
| `lib/features/grama/presentation/screens/account_screens.dart:222` | Citizen check |

## 3. OTP and SMS leftovers

No SMS is sent for anything, so nothing SMS-related needs keeping.

| Where | What | Proposed action |
|---|---|---|
| `apps/accounts/migrations/0001_initial.py:157-199` | Creates the old OtpRequest table | Keep: applied migration history; `0002_password_login.py:95` already drops the table |
| `apps/notifications/migrations/0002_seed_templates.py:24-32` | Seeds the old SMS texts | Keep: history; `0003_remove_sms_templates.py` deletes them and `0004` limits channels to push |
| `apps/accounts/tests/test_login.py:111-112` | Proves `/auth/otp/request/` answers 404 | Keep |
| `apps/notifications/tests/test_api.py:109, 128` | Proves SMS templates are gone; uses `{otp}` as an invalid placeholder | Keep |
| `.pytest_cache/` | Stale test names from the OTP era | Cache, not code; cleared by any run with `--cache-clear` |
| `console/tools/logic.js:36, 55, 122` (and generated `index.html:34, 57`) | The change-password screen is still called `otp` internally | Rename to `change_password` |
| `console/tools/logic.js:173` | Channel labels include `sms` and `push_sms` | Remove (channels are push only) |
| `console/tools/port.py:57, 158-159, 176-178, 210, 222`; `console/tools/visual-check.mjs:64-67`; `console/tools/visual_diff.py:17` | Match the prototype's OTP screens so they can be replaced and screenshotted | Keep: they refer to the prototype, not to our login |
| Mobile `feature/api-integration`: `lib/l10n/app_en.arb:9-14, 307-308`, `app_te.arb` (9 keys) | Unused OTP strings and SMS channel labels | Remove |
| Mobile `feature/api-integration`: `lib/core/widgets/inputs.dart:63` | Six-box OTP input widget | Remove if unused |
| Mobile `dev`: `lib/features/grama/state/app_state.dart:157, 347-374`; `lib/core/i18n/strings.dart:12-17, 470, 504-509` | Mock OTP login (any 6 digits; role from the mobile's last digit) | Replace with username and password login (depends on decision 5.7) |
| Repo folder `CLAUDE.md:17` | Brief still says "SMS OTP: a gateway interface…" | Remove (requested) |
| Repo folder `CLAUDE.md:65` | Decision note "No SMS…" | Reword to the new login decision |
| `TEAM_HANDOFF.md:165, 173, 186, 382` | Stale notes about OTP wording on the website (already fixed) | Remove |
| `docs/prototype-inventory.md` (21 mentions) | Describes the prototype's OTP screens | Keep as prototype history; add a pointer to the new authorization doc |
| `docs/api-contract.md` (2 mentions) | Notes that OTP endpoints were removed | Keep |

## 4. How login works today

### Backend
- **Login:** `POST /api/v1/auth/login/` (`apps/accounts/views_auth.py:112`) calls `authenticate_login()` (`apps/accounts/credentials.py:146`). The username or the email is accepted, case-insensitively: usernames and emails are stored lowercase (`apps/accounts/models.py:46, 146-147`).
- **Brute-force protection:** a custom `LoginAttempt` table (`apps/accounts/models.py:230`; `apps/accounts/credentials.py:115-144`). Five failures per username in 15 minutes, or 30 per IP in an hour, give 429. It is not django-axes, and there is no unlock action.
- **Tokens:** simplejwt. The access token lasts 15 minutes and the refresh token 30 days, rotating, with a blacklist (`grama/settings/base.py:178-185`). Each token carries the user's `token_version` as claim `tv` (`apps/accounts/tokens.py:24-30`). Every request reloads the user and rejects a changed version or a blocked user (`apps/accounts/tokens.py:37-45`).
- **Forced change:** `must_change_password` blocks every endpoint except `/me`, password change, refresh and logout (`apps/accounts/authentication.py:12-44`).
- **Password change:** checks the old password, runs the validators, bumps `token_version`, blacklists other refresh tokens, issues new tokens (`apps/accounts/views_auth.py:131`).
- **Blocking:** `status=BLOCKED`, `token_version` bumped, all refresh tokens blacklisted, in one transaction (`apps/accounts/tokens.py:84-90`).
- **New users and resets:** the admin enters name, email, role and area. The password is the role's default password (`RoleDefaultPassword`, `apps/accounts/credentials.py:53-83`), which is emailed by a background job (`apps/accounts/tasks.py`). Resets use the same default and email it again. The Super Admin edits the role defaults at `/api/v1/admin/role-passwords/`.
- **Passwords:** Django's default PBKDF2 hasher; Argon2 is not installed. Validators: user-attribute similarity, minimum length (Django's default of 8), common password, numeric (`grama/settings/base.py:113-118`).
- **Audit:** login, password change, reset, block and unblock are audited. Failed logins and throttling are recorded only in `LoginAttempt`, not in the audit log.

### Web console
- **Login:** the card posts username and password to `/api/v1/auth/login/` with `client: "web_admin"` (`console/tools/logic.js:56`).
- **Tokens:** the access token is kept in JavaScript memory (`console/src/gc-api.js:111`). The refresh token is an HttpOnly, SameSite=Strict cookie limited to `/api/v1/auth/` (`apps/accounts/views_auth.py:50-64`).
- **Reload:** the console calls `/auth/refresh/` with the cookie (`console/src/gc-api.js:35`). The server checks the Origin header (`apps/accounts/views_auth.py:66-70`).
- **Not used:** there is no Django session, no CSRF token and no idle timeout beyond token lifetimes.
- **Who may enter:** non-Super Admins are refused at login (`not_console_user`).

### Flutter app on `feature/api-integration`
- **Login:** username and password through `/api/v1/auth/login/`.
- **Tokens:** kept in `flutter_secure_storage` (`lib/core/storage/local_store.dart:14`). On `token_expired` the app refreshes once and retries (`lib/core/api/api_client.dart:44`). On `session_revoked` it logs out (`api_client.dart:57`).
- **Forced change:** routes to the change-password screen (`lib/app/app.dart:21`, `lib/features/auth/auth_screens.dart:19, 113-168`).

### Flutter app on `dev`
- **Login:** a mock OTP screen. Any six digits pass, and the role comes from the mobile number's last digit (`lib/features/grama/state/app_state.dart:347-374`). There is no backend call.

## 5. Decisions (owner, 2026-10-21)

1. **New accounts and resets:** the admin sets a temporary password or has the server generate one, shown once. No email. Email invites, the role default passwords, `/admin/role-passwords/` and the console's "Default passwords by role" card are removed.
2. **Email:** optional contact information only, not a login name. The username is the only login name.
3. **Blocked flag:** `status` became a real `is_active` field. The API still returns `status`, derived from it, for older clients.
4. **Lockout:** django-axes replaces `LoginAttempt`, whose table is dropped. 5 failures per username and IP lock for 15 minutes. Lockouts and unlocks are audited. An admin with `accounts.unlock_user` ranked above the user can unlock; a password reset also clears a lockout.
5. **URL paths:** everything stays under `/api/v1/`.
6. **Console session:** a Django session with CSRF, an HttpOnly cookie and a 30-minute idle timeout. The app keeps JWT.
7. **Mobile app:** skipped for now. The API keeps role keys and `capabilities` so the app on `feature/api-integration` keeps working.
8. **Group levels:** a `RoleGroup` table (group, key, level, rank, platform-wide, manage permission) is filled from `role_matrix.py` by the migration and by `sync_role_permissions`. No code tests group names.
9. **`SUPER_ADMIN_CAN_POST`:** applied by `sync_role_permissions` and checked again by `authz.can()` on every request.
10. **Second roles:** any admin with the matching `manage_*` permission for that area may assign lower groups, in the console or the API.

## 6. What step 2 to 5 will touch

- **Backend:**
  - `apps/accounts`: models, migrations, roles, scope (replaced by `authz.py`), credentials, tokens, authentication, views, serializers, admin, tasks and tests.
  - Every permission class listed in 2.2, and every check in 2.3.
  - Settings: axes, Argon2, session and CSRF.
  - `seed_sample_data`.
  - Docs: `docs/authorization.md`, `docs/api-contract.md` sections 1, 3 and 11, the CLAUDE.md files and `TEAM_HANDOFF.md`.
- **Web console:** the login and session handling in `console/src/gc-api.js` and `console/tools/logic.js`, the unlock action, permission-based menus from `/me`, and the `otp` screen rename.
- **Flutter app** (per decision 7): `/me` permission codenames instead of `capabilities`, and removal of the unused OTP strings and widget.
