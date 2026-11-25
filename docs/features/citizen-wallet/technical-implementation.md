# Citizen wallet and referrals: technical implementation

| | |
|---|---|
| Status | Draft design, 2026-11-25. Nothing is built yet. v5: a sign-up form with email and password, and email codes; WhatsApp and SMS move to a later stage (section 3.13). v4: family phones, deletion on the website, pilot mandals. |
| Requirements | [requirements.md](requirements.md) (decisions D1–D17, rules in section 7) |
| Code read for this design | BackEnd `dev` `0b0dfad`, Web `dev` `241cf1b`, Mobile `dev` `b3b7eff` |
| Owners | Backend (Basam Bhargavi), Mobile and web (Mohit), QA (Anvesh Kolli) |

The design follows the backend's own rules (`.claude/conventions.md`): roles and permissions only in `apps/accounts/role_matrix.py`, every check through `HasPerm` and `authz`, portable ORM, audit only through `record()`, every user-facing message in `_()` with its Telugu, and the contract first.

## 1. Overview

```mermaid
sequenceDiagram
    participant App as Mobile app
    participant API as Backend
    participant MAIL as Email service (SMTP)
    App->>API: POST citizen/auth/signup/ {name, mobile, email, password, village_id, age_confirmed, consent_version, language, referral_code?, android_id?}
    API->>MAIL: a 6-digit code to the email
    API-->>App: {signup_token, expires_in, resend_after, email_hint}
    App->>API: POST citizen/auth/signup/verify/ {signup_token, code}
    API-->>App: 201 JWT pair + account (Citizen role in the village); a referral starts PENDING
    Note over App,API: Later: POST citizen/auth/login/ {login: email or mobile, password}
    Note over App,API: Forgotten password: password/forgot/ {login}, then password/reset/ {login, code, new_password}
    Note over API: Hourly job: PENDING referrals past their hold date are CONFIRMED or REJECTED
```

Three things are new:
- a **citizen account**: a Django user with the new Citizen role and a password;
- **email codes** for sign-up and for a forgotten password;
- a **wallet** with **referrals**.

WhatsApp and SMS codes are a later stage (section 3.13). Device posting tokens (`apps.citizens`, `X-Citizen-Token`) stay as they are: posting and service requests do not need the account (D2).

## 2. Data model

### 2.1 Extend `apps.citizens` (accounts)

`apps.citizens` already owns citizen identity (phones, posting tokens). The account lives next to it.

**CitizenAccount**

| Field | Type | Notes |
|---|---|---|
| `user` | OneToOne `accounts.User`, primary key | The Django user. The password is the user's own (`set_password`, Argon2 as for officials), checked with the citizen rule (3.3). |
| `village_id` | PositiveInteger | Also the area of the Citizen role assignment. |
| `age_confirmed_at` | DateTime | 18+ confirmation (D5). |
| `consent_version`, `consent_at` | Char(20), DateTime | Which consent notice was agreed, and when. |
| `email` | Email(254) | The address codes and notices go to, stored in lower case. Not `User.email`: officials' emails are contact only and not unique, and an official's own citizen account may use the same one. |
| `email_key` | Char(254), unique, null | The address's canonical form, one of the two login names. It is lower case; for `gmail.com` and `googlemail.com`, dots and anything after a `+` are removed before the @ and the domain becomes `gmail.com`. One person's Gmail variants therefore clash (FR-11). Null only after deletion. |
| `email_verified_at` | DateTime, null | Set by the sign-up code, a new email's code, or the first reset code after the Super Admin set the email (3.4b). |
| `mobile_hash` | Char(64), unique, null | Keyed hash of the 10-digit number, the other login name (same keying as `citizens.services._keyed_hash`). The full number is never stored. Null after deletion, or after the Super Admin removes it (D16). Not `User.mobile`: that column is unique across officials (`accounts/models.py:101-108`), so an official's number would clash. |
| `mobile_last4` | Char(4) | For display and for the Super Admin's search. |
| `mobile_verified_at` | DateTime, null | Null until the later stage confirms the number with a code (3.13). Bills & Recharges need it (D17). A number change clears it. |
| `last_active_at` | DateTime | The last day the app opened with the account (section 3.6), shown in the Super Admin's list. |
| `referral_code` | Char(8), unique | Generated, no look-alike characters (the alphabet of `accounts/credentials.py:32`). |
| `signup_android_id_hash` | Char(64), indexed | From the phone used at sign-up. Limits the welcome bonuses per phone and skips the referrer's reward when both accounts signed up on the same phone (D10). |
| `deleted_at` | DateTime, null | Set when the account is deleted (section 3.8). |
| `deleted_mobile_hash`, `deleted_email_key` | Char(64), Char(254), indexed | Kept after deletion to stop repeat welcome bonuses (requirements Q9, pending legal review). |
| `created_at` | DateTime | |

**PendingSignup** (one row per sign-up waiting for its email code; an unfinished form, not a record)

| Field | Notes |
|---|---|
| `id` | UUID |
| `name`, `village_id`, `language` | From the form |
| `mobile_hash`, `mobile_last4` | The number typed |
| `email`, `email_key` | The address typed |
| `password` | The password's hash (`make_password`); the password itself is never stored |
| `age_confirmed_at`, `consent_version` | |
| `referral_code` | As typed; checked again when the account is created |
| `android_id_hash` | May be empty |
| `created_at`, `expires_at` | Expires after 24 hours (FR-10). Daily housekeeping deletes expired rows; they are not records under the "data is kept permanently" decision. |

**VerificationCode** (one row per code sent; never holds the code itself)

| Field | Notes |
|---|---|
| `purpose` | `SIGNUP`, `RESET_PASSWORD`, `CHANGE_EMAIL` or `DELETE` (the website's deletion page). The later stage adds `VERIFY_MOBILE`. |
| `channel` | `EMAIL`. The later stage adds `WHATSAPP` and `SMS`. |
| `pending_signup` | FK PendingSignup, null; set for `SIGNUP` |
| `account` | FK CitizenAccount, null; set for the other purposes |
| `email_key` | Where the code went, for the limits per address |
| `new_email`, `new_email_key` | `CHANGE_EMAIL` only: the address being proven. Cleared once used or expired. |
| `android_id_hash` | The phone that asked, for the per-phone limit; may be empty |
| `code_hash` | HMAC of the code with the row id and `SECRET_KEY` |
| `expires_at` | Sent + 10 minutes |
| `attempts` | Wrong tries, at most 5 |
| `verified_at`, `consumed_at` | Each code is used once |
| `provider_message_id` | The email service's message id, for support |
| `created_at` | Counts the limits |

**CitizenActiveDay**: `account`, `day` (DateField, India date), unique together. One row per day the app opened with the account (section 3.6). Rows older than 60 days are deleted by housekeeping; they are not records under the "data is kept permanently" decision, only a counter.

### 2.2 New app `apps.wallet`

Create it as `.claude/conventions.md` says (`startapp wallet apps/wallet`, label `wallet`, add to `LOCAL_APPS`).

**Wallet**: `account` (OneToOne CitizenAccount, PK), `confirmed_points` (PositiveInteger), `held_points` (PositiveInteger), `updated_at`. The two numbers are a cache of the entries, kept in step in the same transaction (section 3.5).

**WalletEntry** (append-only)

| Field | Notes |
|---|---|
| `id` | UUID |
| `wallet` | FK Wallet |
| `points` | Integer, never 0 (CheckConstraint); negative only for adjustments |
| `kind` | `REFERRAL_REWARD`, `WELCOME_BONUS`, `ADJUSTMENT` |
| `status` | `HELD`, `CONFIRMED`, `CANCELLED` |
| `referral` | FK Referral, null |
| `reason` | Text shown in the history (an adjustment's reason is required) |
| `created_by` | FK User, null (the Super Admin for adjustments) |
| `idempotency_key` | Char(64); unique together with `wallet` (the database guard against double rewards) |
| `created_at`, `decided_at` | |

**Referral**

| Field | Notes |
|---|---|
| `id` | UUID |
| `referrer` | FK CitizenAccount |
| `referee` | OneToOne CitizenAccount (a citizen is referred at most once, FR-22) |
| `status` | `PENDING` (held), `CONFIRMED`, `REJECTED` |
| `reject_reason` | One of the keys in section 3.5 |
| `pending_at`, `confirm_after`, `decided_at` | |
| `decided_by` | FK User, null (Super Admin) |
| `created_at` | |

Indexes: `(status, confirm_after)` for the job; `(referrer, status, decided_at)` for the monthly limit.

## 3. Backend design

### 3.1 The Citizen role

Add a group to `ROLE_GROUPS` in `apps/accounts/role_matrix.py`:

```python
"CITIZEN": {
    "name": "Citizen",
    "name_te": "పౌరుడు",
    "level": "VILLAGE",
    "rank": 5,
    "platform_wide": False,
    "manage_permission": None,     # nobody gives it through users/; sign-up gives it
    "password_min_length": 0,      # the citizen rule is separate (3.3), never credentials.check_new_password
    "permissions": ["wallet.use_wallet"],
},
```

The key matches the existing `CITIZEN_ROLE = "CITIZEN"` used for device posts (`apps/posts/models.py:21`), so audit and post history read the same.

New permissions (model `Meta.permissions`, then `sync_role_permissions`; steps in `docs/authorization.md`, section 10):

| Permission | Held by |
|---|---|
| `wallet.use_wallet` | Citizen |
| `wallet.view_wallets`, `wallet.adjust_points`, `wallet.review_referrals`, `wallet.manage_wallet_settings` | Super Admin (add to `PLATFORM_ADMIN`) |
| `accounts.manage_citizen_accounts` | Super Admin (search, block, unblock, delete citizen accounts, and the help of 3.4b) |
| `accounts.use_official_api` | Every official group (section 3.2) |

Sign-up creates the RoleAssignment directly (`assigned_by` null) and audits it. A village change moves the assignment's area.

**Citizens stay out of the officials' user lists without extra code:** `authz.manageable_users` (`apps/accounts/authz.py:473-505`) keeps only users whose every assignment the actor may manage, and a group with `manage_permission: None` is managed by nobody. A test must lock this in.

### 3.2 Places that treat "has a role" as "is an official" (must change)

Today every user with a role is an official, and some code relies on that. A Citizen account, which now has a password, breaks the assumption here:

| Where | What happens with a Citizen account | Change |
|---|---|---|
| `apps/accounts/credentials.py:122` `authenticate_official` (`auth/login/`, `auth/session/login/`) | The account has a password, so with its generated username it could log in through the officials' login | Refuse a user without `accounts.use_official_api` with the same `400 invalid_credentials` as a wrong password (FR-5). The generated username is never shown, so this is a second guard. |
| `apps/accounts/views_auth.py` `PasswordChangeView` | It would check a citizen's new password against the officials' rule (12 characters, composition) | Refuse citizen accounts with 403 `permission_denied`; citizens use `citizen/account/password/` (3.4a) |
| `apps/web_admin/api.py:562` `HasAnyRole` (Telugu name suggestions) | A citizen could call `admin/transliterate/`, which calls Google | Use `HasPerm("accounts.use_official_api")` |
| `apps/posts/services.py:229` `covering_scope_q` ("whatever its permissions") | `GET posts/` would list the village's published posts to a citizen account | Count only grants holding `accounts.use_official_api`, or gate `PostViewSet` with that permission |
| `apps/accounts/serializers.py` `capabilities()` / `MeSerializer` | Works, but the app should not read officials' fields | The app uses `citizen/account/`; `me/` stays for officials |
| Dashboard and reports (`apps/web_admin/api.py`) | Counts such as blocked users may include citizen accounts | Count officials only (holders of `accounts.use_official_api`) |
| `users/{id}/unlock/` and the console's lockout list | Citizen lockouts would show among officials' | Officials only; a citizen's lock ends by itself or with a password reset |

Before merging, grep for `IsAuthenticated]` and `authz.grants(` and confirm each place. Every endpoint officials use must answer a citizen account with 403, with a test per endpoint.

### 3.3 Sign-up, login and the forgotten password

Public endpoints, throttled per internet address like the other public citizen endpoints.

**The citizen password rule** (D15, FR-13)
- A separate list, `CITIZEN_PASSWORD_VALIDATORS` in `grama/settings.py`:
  - `MinimumLengthValidator` (`CITIZEN_PASSWORD_MIN_LENGTH`, 8);
  - `CommonPasswordValidator`;
  - `UserAttributeSimilarityValidator` on the name and the email;
  - a small validator that refuses the mobile number itself.
- Not `NumericPasswordValidator` and not `PasswordCompositionValidator`: numbers-only passwords are allowed.
- Checked in a new `apps/citizens/passwords.py` with `validate_password(password, user, password_validators=get_password_validators(settings.CITIZEN_PASSWORD_VALIDATORS))`. Never through `credentials.check_new_password`, which applies the officials' rule.
- The global `AUTH_PASSWORD_VALIDATORS` stay as they are, so the officials' rule cannot get weaker by accident. A test locks that in.
- The messages are Django's, whose Telugu is already in the catalogue (`grama/i18n.py`).

**`POST citizen/auth/signup/`** `{name, mobile, email, password, village_id, age_confirmed, consent_version, language, referral_code?, android_id?}`
- Checks, in order:
  - the Wallet on setting;
  - the pilot: while `WALLET_PILOT_MANDALS` is not empty, the village must be in one of those mandals (403 `wallet_not_in_area`, D13);
  - `age_confirmed: true` (`age_required`) and the current `consent_version`;
  - an active village and a name of 1 to 100 characters;
  - the number with `mobile_validator` (`apps/accounts/models.py:14`) and the email with Django's `EmailValidator`, at most 254 characters;
  - the password rule;
  - the referral code (errors in 3.5).
- A number or an email that already belongs to an account answers 409 `mobile_in_use` or `email_in_use`, so the app can offer **Log in** and **Forgot password?** (FR-11). This tells a visitor that a number or an email is registered. That is accepted, as in most sign-up forms, and the per-address throttle limits it (R13).
- Creates a PendingSignup and a `SIGNUP` code, and sends the code by email (3.4).
- Answers `{signup_token, expires_in, resend_after, email_hint}`. `email_hint` is the address masked like `r***@gmail.com` (`apps/audit/masking.py` `mask_email`).

**`POST citizen/auth/signup/resend/`** `{signup_token}` sends a new code, within the same limits.

**`POST citizen/auth/signup/verify/`** `{signup_token, code}`
- A wrong code is a conditional UPDATE on `attempts < 5`, answered with `code_wrong` and `attempts_left`. An expired code gets `code_expired`.
- The right code does all of this in one transaction:
  - checks again that the number and the email are still free; another sign-up may have finished first (409 `mobile_in_use` or `email_in_use`), and the unique constraints are the real guard;
  - creates the user, with a generated username that passes `username_validator` (never shown, never used to log in), the name, `preferred_language` and the password hash from the PendingSignup;
  - creates the Citizen assignment;
  - creates the account (`email_verified_at` now), its referral code and the wallet;
  - creates the referral, if a code was given (3.5);
  - deletes the PendingSignup and consumes the code.
- Answers 201 `{access, refresh, account}`.

**`POST citizen/auth/login/`** `{login, password}`
- A `login` with an @ is an email, matched on `email_key`. 10 digits are a mobile number, matched on `mobile_hash`. Anything else is `400 invalid_credentials`.
- Then `authenticate(request, username=<the account's generated username>, password=…)`. django-axes therefore counts the email and the mobile number as one account (FR-8): 5 failures per account and internet address give 429 `locked_out` with `retry_after_s` (`AXES_*`, as for officials).
- An unknown login is counted under the typed value's key, and the password hasher still runs once (as `ModelBackend` does), so the answer takes the same time.
- A wrong password, an unknown login and a deleted account all get `400 invalid_credentials`. A blocked account gets 403 `account_blocked`.
- Success answers `{access, refresh, account}` through `accounts.tokens.issue_tokens(user)`. Audit `auth.login` with `via="citizen_app"`.

**`POST citizen/auth/password/forgot/`** `{login}`
- If the login matches an active account, creates a `RESET_PASSWORD` code and emails it to the account's address.
- Always answers `{expires_in, resend_after}`, the same for an unknown login or a blocked account, which get no code (FR-14).

**`POST citizen/auth/password/reset/`** `{login, code, new_password}`
- Checks the newest live `RESET_PASSWORD` code of that account. An unknown login, or one without a live code, gets `400 code_expired` like an expired code.
- The right code, in one transaction:
  - checks the new password with the citizen rule and sets it;
  - sets `email_verified_at` if it was empty (3.4b);
  - `revoke_all_sessions(user)`;
  - clears the account's lock (`lockout.unlock`, as an official's reset does);
  - consumes the code;
  - audits `citizen.password_reset`.
- Then sends the "password changed" notice and answers `{access, refresh, account}`.

**Tokens and sessions**
- `signup_token` is signed with `django.core.signing` (its own salt) and carries only the PendingSignup id. It lives as long as the row (24 hours); each code inside it lives 10 minutes. The row is deleted when the account is made, so the token works once (401 `signup_token_invalid` after that).
- Sessions are issued with `issue_tokens(user)`, so the token version, blocking and the 90-day limit (`JWT_ABSOLUTE_DAYS`) work as for officials.
- Refresh and logout use the existing `auth/refresh/` and `auth/logout/`.
- No full email, number, password or code is ever in a token, a log or a task argument.

### 3.4 Sending email

- **How:** Django's `EmailMultiAlternatives` through `EMAIL_BACKEND`, the SMTP endpoint of the client's email service (C7: Amazon SES SMTP, Zoho ZeptoMail, or the domain's own server). Settings: `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (secret), `EMAIL_USE_TLS`, `EMAIL_TIMEOUT` (10 seconds), `DEFAULT_FROM_EMAIL`. Tests already use the locmem backend (`grama/settings.py`, section 6). The backend sends no email today, so this is its first use; no new package is needed.
- **When:** in the request, not in a django-q job. The conventions forbid full emails in task arguments, and the code exists only in memory. If the service cannot be reached, the answer is 503 `email_unavailable`. A pending sign-up stays, so **Send again** works later. Nothing else in the app depends on email.
- **Texts:** one template per purpose and language (`te`, `en`), plain text with a simple HTML copy, in `apps/citizens/templates/citizens/email/`, UTF-8 for Telugu.
  - The subject carries the code, for example "Grama Connect code: 482913", so it shows in the phone's notification.
  - The body says the code is valid for 10 minutes and must not be shared.
  - Notices without a code: password changed, email changed (to the old address), account deleted.
- **Reaching the inbox:** SPF, DKIM and DMARC on the sender's domain (C7), or Gmail may file the codes as spam. The app's code screen tells the citizen to check the spam folder.

**Rules** (FR-12)
- 6 digits from `secrets.randbelow`, valid 10 minutes, 5 wrong tries.
- A new code after 60 seconds (429 `code_cooldown` with `retry_after_s`).
- At most 5 codes a day per account or per address (`email_key`), and 10 a day per phone (`android_id_hash`), on top of the per-address throttle (429 `code_limit`).
- Wrong tries go through a conditional UPDATE (`attempts < 5`), so parallel tries cannot exceed the limit on any database.

**Audit.** `citizen.code_sent` (purpose, channel, the masked email). Never the code or the full address.

### 3.4a Changing details in the app

All with the citizen's JWT. A wrong password counts with the wrong tries of a password change (`credentials.confirm_own_password`, `PASSWORD_CHANGE_MAX_FAILURES`), so guessing ends the account's sessions as for officials.

- **`POST citizen/account/password/`** `{current_password, new_password}`. A wrong current password gets `400 invalid_credentials`. A new password goes through the citizen rule. Other sessions end (token version bump) and this phone gets new tokens. A notice goes by email; audit `citizen.password_changed`.
- **`POST citizen/account/email/`** `{email, password}` checks the password and that the address is free (409 `email_in_use`), then sends a `CHANGE_EMAIL` code to the new address.
- **`POST citizen/account/email/verify/`** `{code}` sets `email`, `email_key` and `email_verified_at` in one UPDATE; the unique constraint answers a race with 409. A notice goes to the old address. Audit `citizen.email_changed` (both masked).
- **`POST citizen/account/mobile/`** `{mobile, password}` sets `mobile_hash` and `mobile_last4` and clears `mobile_verified_at` in one UPDATE, or answers 409 `mobile_in_use`. Audit `citizen.mobile_changed` with the old and the new last 4 digits. In the later stage, a code to the new number is added (3.13).

### 3.4b Super Admin help (D16, FR-16)

`accounts.manage_citizen_accounts` only. While the citizen is on the phone:
1. **`POST admin/citizen-accounts/find/`** `{mobile?, email?}` returns the matching rows. It is a POST so the number or the email never goes in a URL; it is audited (masked).
2. The Super Admin dials the number the caller gave and the server matched, and makes sure the person who answers is the caller. The console shows only the last 4 digits.
3. Then one of:
   - **`POST admin/citizen-accounts/{id}/email/`** `{email, reason, called_back: true}` sets the new address with `email_verified_at` empty. Codes already sent to the old address stop working. The citizen then uses **Forgot password**, and the code reaching the new address proves it. Audit `citizen.email_set_by_admin`.
   - **`POST admin/citizen-accounts/{id}/release-mobile/`** `{reason, called_back: true}` clears `mobile_hash`, `mobile_last4` and `mobile_verified_at` on the account that holds the number. Its next `GET citizen/account/` says `mobile_missing: true`, and the app asks for a new number (3.4a). Audit `citizen.mobile_released`.

`called_back` must be true (`validation_error` otherwise): the console asks "Did you call the number back and speak to the citizen?" before the button works. No endpoint sets, shows or resets a citizen's password.

### 3.5 Referrals and the wallet

**Giving a code** (only at sign-up; FR-18, D9)

The code is checked at `citizen/auth/signup/`, so the citizen can fix it on the form, and again at `signup/verify/`. The sign-up is refused with:

| Error code | When |
|---|---|
| `referral_code_invalid` | No such code |
| `referral_code_own` | The referrer is the same person: the same mobile hash or the same `email_key` |
| `referral_not_open` | The referrer's account is blocked or deleted |

Otherwise, in the transaction of `signup/verify/`, a Referral is created as PENDING with `confirm_after = now + REFERRAL_HOLD_DAYS`. Up to two HELD entries are created, with idempotency keys `ref:<referral id>:referrer` and `ref:<referral id>:welcome`:
- the referrer's reward, unless both accounts signed up on the same phone (equal `signup_android_id_hash`; D10, FR-20). The history then notes `same_phone`.
- the referee's welcome bonus. It is skipped when any of these is true, with a note in the history:
  - `WELCOME_BONUSES_PER_PHONE` accounts with the same `signup_android_id_hash` (deleted ones included) already got one;
  - a deleted account with the same mobile hash or `email_key` got one;
  - the sign-up sent no Android id.

`held_points` goes up through `F()` updates. After commit, the referrer gets a push.

**Confirmation job.** `apps.wallet.tasks.decide_referrals` runs every hour, registered by `ensure_schedules`. For each PENDING referral past `confirm_after`:

| Reject reason | When |
|---|---|
| `blocked` | The referee or referrer is blocked |
| `deleted` | Either account is deleted |
| `not_active` | The referee had fewer than `REFERRAL_ACTIVE_DAYS` active days between `pending_at` and `confirm_after` |
| `monthly_limit` | The referrer already has `REFERRALS_PER_MONTH` confirmed referrals in this calendar month (India time) |

Otherwise the referral is CONFIRMED.
- Each move is a conditional UPDATE on the referral (`status='PENDING'`), so two workers cannot both decide it.
- The entries move HELD → CONFIRMED (or CANCELLED) in the same transaction, and the wallet cache moves with them: `held_points = F('held_points') - n`, `confirmed_points = F('confirmed_points') + n`.
- Pushes go out after commit. Audit `referral.confirmed` or `referral.rejected`.

**Super Admin**
- Confirm or reject a PENDING referral early, with a comment.
- Adjust a wallet with a reason: an ADJUSTMENT entry; a negative one uses `UPDATE … WHERE confirmed_points >= n`, else 409 `not_enough_points` (FR-29).

**Consistency.** A daily job compares each wallet's cache with the sum of its entries and audits `wallet.mismatch` if they differ. The entries are the truth.

### 3.6 Active days

`GET citizen/account/`, which the app calls when it opens, inserts today's CitizenActiveDay row if it is missing (`get_or_create` on the unique pair). That is one cheap write per account per day, and it gives the active rule (requirements section 7) without tracking anything else. The same write sets `last_active_at` for the Super Admin's list; the day rows are deleted after 60 days.

### 3.7 Settings

Runtime settings, overridable from the console (`apps/posts/services.py` `RUNTIME_SETTINGS`, SettingOverride), with env defaults in `grama/settings.py`:

| Setting | Default |
|---|---|
| `WALLET_ENABLED` | False |
| `REFERRAL_REWARD_POINTS` | 50 |
| `WELCOME_BONUS_POINTS` | 20 |
| `REFERRAL_HOLD_DAYS` | 7 |
| `REFERRAL_ACTIVE_DAYS` | 2 |
| `REFERRALS_PER_MONTH` | 30 |
| `WELCOME_BONUSES_PER_PHONE` | 3 (D10) |
| `WALLET_PILOT_MANDALS` | Empty, meaning everywhere; a comma-separated list of mandal ids while the pilot runs (D13) |

Env only (secrets and fixed values):
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` (secret), `EMAIL_USE_TLS`, `EMAIL_TIMEOUT`, `DEFAULT_FROM_EMAIL` (C7)
- `CITIZEN_PASSWORD_MIN_LENGTH` (8), `CITIZEN_CONSENT_VERSION`
- Later stage (3.13): `WHATSAPP_*`, `SMS_*`

`GET app-config/` adds `wallet_enabled`, so the app and the website hide the feature while it is off, and `wallet_pilot_mandals`, so the app hides it outside the pilot.

### 3.8 Account deletion

Three ways in, all through one service function:
- `POST citizen/account/delete/` `{password}`: the citizen, in the app;
- the website's deletion page (D12):
  1. `POST citizen/auth/delete/code/` `{login}` sends a `DELETE` code to the account's email, with the same answer for every login;
  2. `POST citizen/auth/delete/` `{login, code}` deletes the account. Only someone who can open the account's email can delete it;
- `POST admin/citizen-accounts/{id}/delete/`: the Super Admin, with a reason, for fraud or a legal request.

In one transaction:
- `is_active` false and `revoke_all_sessions`;
- the name replaced with "Deleted citizen", the username scrambled and the password made unusable;
- `email` cleared, and `email_key` moved to `deleted_email_key`;
- `mobile_hash` moved to `deleted_mobile_hash`. Both fingerprints are kept for 1 year if legal agrees (requirements Q9), then cleared by housekeeping;
- push devices unlinked;
- the role assignment deactivated;
- HELD entries cancelled.

Confirmed entries and referrals stay, with no personal details, for the audit trail. Audit `citizen.account_deleted` with `via` (`app`, `website` or `admin`). The "account deleted" notice is sent before the address is cleared. This is the exception to "data is kept permanently" (requirements Q8, needs the owner's confirmation).

### 3.9 API (draft for `docs/api-contract.md`, new section 21)

All under `/api/v1/`. Citizen endpoints need a citizen JWT holding `wallet.use_wallet`. Every endpoint answers 404 `not_found` while `WALLET_ENABLED` is off.

| Method and path | Who | Request | Response |
|---|---|---|---|
| `POST citizen/auth/signup/` | public | `{name, mobile, email, password, village_id, age_confirmed, consent_version, language, referral_code?, android_id?}` | `{signup_token, expires_in, resend_after, email_hint}`; 409 `mobile_in_use` or `email_in_use` |
| `POST citizen/auth/signup/resend/` | public | `{signup_token}` | `{expires_in, resend_after}` |
| `POST citizen/auth/signup/verify/` | public | `{signup_token, code}` | 201 `{access, refresh, account}` |
| `POST citizen/auth/login/` | public | `{login, password}` (email or mobile) | `{access, refresh, account}` |
| `POST citizen/auth/password/forgot/` | public | `{login}` | `{expires_in, resend_after}`, the same for every login |
| `POST citizen/auth/password/reset/` | public | `{login, code, new_password}` | `{access, refresh, account}` |
| `POST citizen/auth/delete/code/` | public (the website) | `{login}` | `{expires_in, resend_after}`, the same for every login |
| `POST citizen/auth/delete/` | public (the website) | `{login, code}` (a `DELETE` code) | `{deleted: true}` |
| `GET citizen/account/` | citizen | — | `Account`: name, village, `email_hint`, `mobile_last4`, `mobile_missing`, `mobile_verified`, `referral_code`, `invite_url`, wallet summary, `referral` (own, if any). Records today's active day |
| `PATCH citizen/account/` | citizen | `{village_id?, language?}` | `Account` |
| `POST citizen/account/password/` | citizen | `{current_password, new_password}` | `{access, refresh}` |
| `POST citizen/account/email/` | citizen | `{email, password}` (the new email) | `{expires_in, resend_after, email_hint}`; 409 `email_in_use` |
| `POST citizen/account/email/verify/` | citizen | `{code}` | `Account` |
| `POST citizen/account/mobile/` | citizen | `{mobile, password}` (the new number) | `Account`; 409 `mobile_in_use` |
| `POST citizen/account/delete/` | citizen | `{password}` | 204 |
| `GET wallet/` | citizen | — | `{confirmed_points, held_points, next_release_at, entries}`; entries are a cursor list (`id, points, kind, status, reason, created_at, decided_at`) |
| `GET admin/citizen-accounts/` | `accounts.manage_citizen_accounts` | `q` (name or last 4 digits), `village_id`, `status`, page | rows: name, village, `mobile_last4`, masked email, email confirmed, mobile confirmed, balance, held, status, `created_at`, `last_active_at` |
| `POST admin/citizen-accounts/find/` | `accounts.manage_citizen_accounts` | `{mobile?, email?}` | rows (3.4b) |
| `POST admin/citizen-accounts/{id}/email/` | `accounts.manage_citizen_accounts` | `{email, reason, called_back}` | row; 409 `email_in_use` |
| `POST admin/citizen-accounts/{id}/release-mobile/` | `accounts.manage_citizen_accounts` | `{reason, called_back}` | row |
| `POST admin/citizen-accounts/{id}/block/`, `/unblock/`, `/delete/` | `accounts.manage_citizen_accounts` | `{reason}` | row |
| `GET admin/referrals/` | `wallet.review_referrals` | `status`, `q`, page | rows with both people (name, last 4 digits), dates, reason, `allowed_actions` |
| `POST admin/referrals/{id}/confirm/`, `/reject/` | `wallet.review_referrals` | `{comment}` | row; 409 `already_decided` if the job got there first |
| `POST admin/wallets/{account_id}/adjust/` | `wallet.adjust_points` | `{points, reason}` | wallet; 409 `not_enough_points` |
| `GET admin/wallet/export/?type=referrals\|entries&format=csv` | `wallet.view_wallets` | filters | CSV with BOM, last 4 digits and masked emails only |
| `GET`/`PATCH admin/settings/` | `posts.manage_settings` (existing) | adds the eight runtime settings above | settings |
| `GET app-config/` | public | — | adds `wallet_enabled` and `wallet_pilot_mandals` |

Existing error codes reused: `invalid_credentials`, `locked_out`, `account_blocked`, `session_revoked`, `validation_error` (the password rule, with `fields`), `permission_denied`.

New error codes for section 1 of the contract:
- `age_required`, `signup_token_invalid` (expired or already used), `wallet_not_in_area`
- `email_unavailable` (503), `code_cooldown`, `code_limit`, `code_wrong` (with `attempts_left`), `code_expired`
- `mobile_invalid`, `mobile_in_use`, `email_in_use`
- `referral_code_invalid`, `referral_code_own`, `referral_not_open`
- `not_enough_points`

### 3.10 Notifications

- New `NotificationKind.WALLET`.
- Templates (data migration, editable in the console like the others): `referral_joined` (to the referrer), `points_confirmed`, `points_not_confirmed`, in Telugu and English.
- The app registers its device with the citizen's JWT (`POST devices/` already links a device to the signed-in user, `apps/notifications/views.py`). Pushes go through `send_push_to_users`, so retries and audit work as today.
- The data carries only ids (`type`, `referral_id`), never names.
- Emails (3.4) are separate from pushes and are not editable in the console in this version.

### 3.11 Audit actions

| Group | Actions |
|---|---|
| Accounts | `citizen.account_created`, `citizen.code_sent` (purpose, channel), `citizen.password_reset`, `citizen.password_changed`, `citizen.email_changed`, `citizen.mobile_changed`, `citizen.village_changed`, `citizen.account_blocked`, `citizen.account_unblocked`, `citizen.account_deleted` |
| Super Admin help | `citizen.account_found`, `citizen.email_set_by_admin`, `citizen.mobile_released` (each with the reason) |
| Referrals | `referral.created`, `referral.confirmed`, `referral.rejected` |
| Wallet | `wallet.adjusted`, `wallet.mismatch` |
| Settings | `setting.changed` (existing) for the wallet settings |

Logins use the existing `auth.login` and the failed-login and lockout entries, with `via="citizen_app"`. Audit `data` holds the last 4 digits of a mobile and a masked email at most. Add every key to the console's and the app's audit labels.

### 3.12 Tests (all on SQLite, each new file in `grama/tests/suite.json`)

- **Permissions:**
  - extend `apps/accounts/tests/test_matrix.py` with the Citizen group;
  - a citizen account gets 403 on every officials' endpoint (one test per endpoint) and never appears in `users/` for any official;
  - the officials' `auth/login/`, the console login and `auth/password/change/` refuse a citizen account;
  - only the Super Admin can use the help endpoints (3.4b).
- **Sign-up:**
  - the email code (right, wrong, expired, resend);
  - a pending sign-up forgotten after 24 hours;
  - a number or email already registered (409), and Gmail variants counted as one;
  - two sign-ups racing for one number or email;
  - the token works once.
- **Login:**
  - by email and by mobile;
  - the lock counts both as one account and ends with a reset;
  - an unknown login gets the same answer;
  - blocked account; 90-day limit.
- **Password:**
  - 8 characters and numbers-only accepted;
  - common passwords and the citizen's own number refused;
  - the officials' rule unchanged: an official still needs 12 characters with the composition.
- **Forgotten password:** the same answer for unknown logins; a reset ends sessions; the notice is sent.
- **Codes:**
  - expiry, attempts, cooldown;
  - the limits per account, per address, per phone and per internet address;
  - the email service down answers 503 and keeps the pending sign-up;
  - no code, password, full email or full number appears in the audit log, logs or task arguments.
- **Profile changes:** password, email with its code and the notice to the old address, mobile with `mobile_in_use`; a wrong password counted.
- **Super Admin help:**
  - find by POST;
  - `called_back` required;
  - a new email needs the reset code to become confirmed;
  - a released number gives `mobile_missing`;
  - every action audited.
- **Referrals:**
  - each reject reason;
  - self-referral by mobile or by email;
  - a same-phone referral pays only the welcome bonus;
  - the welcome limit per phone (deleted accounts included) and by a deleted account's email or number;
  - no welcome bonus without an Android id;
  - the monthly limit;
  - two parallel confirmations (one wins);
  - the idempotency key against double rewards.
- **Wallet:** the cache equals the sum of entries after every operation; a negative adjustment below zero is refused.
- **Deletion:** personal fields cleared and entries kept; re-sign-up gets no second welcome bonus; the website's deletion needs that account's `DELETE` code, and a `RESET_PASSWORD` code does not work for it.
- **Pilot:** sign-up outside the pilot mandals is refused; an existing account whose village moves outside keeps working; an empty list means everywhere.
- **Telugu:** every new message and email template has its Telugu (`grama/tests/test_i18n.py`).

### 3.13 Later stage: WhatsApp and SMS (D17, before Bills & Recharges)

It reuses the v3 and v4 design of this file (git history, section 3.4 there), adapted to accounts that already have a password:
- **Gateways**, pluggable like `apps/notifications/push.py`:
  - `WHATSAPP_GATEWAY`: the Meta Cloud API with the approved authentication template and its copy-code button (C1, C2);
  - `SMS_GATEWAY`: the client's Indian SMS provider, with the DLT-registered template and sender name (C3).
  - Each is off without its secret. `GET app-config/` lists the channels that are on (`citizen_code_channels`).
- **Sending:** codes go in the request, never in a job, because full numbers must not be in task arguments. The number is typed or confirmed by the citizen each time and never stored.
- **Confirming the number:**
  - `VerificationCode` gains the channels `WHATSAPP` and `SMS` and the purpose `VERIFY_MOBILE`;
  - `POST citizen/account/mobile/code/` `{channel}` and `POST citizen/account/mobile/verify/` `{code}` set `mobile_verified_at`;
  - when the stage goes live, the app asks every account without a confirmed number to confirm it once;
  - after that, changing the number needs a code sent to the new number.
- **Forgotten password:** the code can also go by WhatsApp or SMS to a confirmed number.
- **Android:** the SMS User Consent API fills an SMS code in after one tap on **Allow**, with no app hash in the message, so Telugu SMS works. WhatsApp's one-tap autofill needs the signing-key handshake and comes later; the copy-code button comes first.
- **Bills & Recharges** require `mobile_verified_at` (403 `mobile_not_verified`, bills design section 3.1).
- **Login by code instead of the password:** requirements Q12; not planned.
- **Cost:**
  - WhatsApp authentication messages for an Indian business to Indian numbers are reported at about ₹0.115 each.
  - SMS codes cost about ₹0.10 to ₹0.45 each, Telugu at the higher end.
  - These are third-party 2026 figures; the rate cards decide.
  - Each citizen confirms once, so 10,000 confirmations cost roughly ₹1,500 to ₹3,000 with tax if a fifth go by SMS.
- **Effort:** backend about 5 days, mobile 3, web 0.5, QA 2, plus Meta's and DLT's review time. Start C1 to C3 early, well before Bills & Recharges.

## 4. Mobile app (Flutter)

- **Packages:** one for Android's Phone Number Hint, such as `smart_auth`, pinned in `pubspec.yaml`, to fill the mobile field from the phone's SIMs. Check its current version when implementing. The SMS User Consent part comes with the later stage.
- **Autofill:** the forms sit in an `AutofillGroup`, with `autofillHints` (`email`, `telephoneNumber`, `newPassword` and `password`, `oneTimeCode`). Android's autofill can then offer the phone's Google account email and save the password, which helps citizens who forget it.
- **iPhone later:** there is no third-party login, so Sign in with Apple is not needed (App Store rule 4.8 covers third-party logins).
- **One account at a time:** an official or a citizen account, never both. The existing `ApiClient` token handling (secure storage, refresh, `session_revoked`) is reused unchanged. Citizen mode without an account stays the default.
- **Screens** (Telugu and English, existing widgets and colours):
  - **Sign in to earn points:** **Log in** and **Create account**.
  - **Create account:**
    - the name, the mobile number (offered from the phone's SIMs), the email, and the password with a confirm field;
    - the app checks that the two passwords match, with show and hide;
    - the 18+ tick, the consent notice with links, and the referral code field (pre-filled from an invite link);
    - server errors show on their fields; `mobile_in_use` and `email_in_use` offer **Log in** and **Forgot password?**.
  - **Check your email:** the masked address, the code field, the resend timer, "Check your spam folder", and **Change email**, which goes back to the form.
  - **Log in:** email or mobile number, the password with show and hide, **Forgot password?**, and the lock message with its minutes.
  - **Forgot password:** email or mobile number, then the code and the new password twice.
  - **Wallet:**
    - the confirmed balance, the points on hold with the expected date, and the history with each status and reason;
    - "Points have no cash value";
    - **Questions about points?** opens the existing help-line numbers screen, where the Super Admin lists the support number as a statewide help line (D11, C6), so nothing new is needed on the server.
  - **Invite friends:** the code, Copy, and Share (WhatsApp first, through the existing `share_plus`).
  - **Profile:** Wallet, Invite, Change password, Change email (with the code screen), Change mobile number, Log out, Delete my account (the password and a confirmation). When `GET citizen/account/` says `mobile_missing`, the app asks for a new number first.
- **Invite links:** add `android:pathPrefix="/app/"` next to the existing `/p/` App Link (`android/app/src/main/AndroidManifest.xml:44`). The server's `/.well-known/assetlinks.json` covers the domain, so no backend change is needed. The app stores `ref` until sign-up. Installs from the website get the code from the link or by typing it. Once the app is on Google Play, the Play Install Referrer can fill it too (it does not work for APKs installed outside Play).
- **On open:** `GET citizen/account/` (records the active day), and `POST devices/` with the citizen's JWT so pushes reach the account.
- **Feature switch:** everything hides while `app-config/` says `wallet_enabled: false`, and also while the village chosen in the app is outside `wallet_pilot_mandals` (when the list is not empty). There, an invite link opens the app with "The wallet is coming to your area soon". A signed-in account keeps its wallet wherever its village moves.
- **Tests:** flows against the local fake server (as `test/app_flow_test.dart`):
  - sign-up with the email code;
  - login by email and by mobile, and the lock message;
  - forgot password;
  - each profile change;
  - wallet, and invite link pre-fill;
  - log out, delete.

## 5. Web (BVTGramaConnect)

**Console.** A new page, **Citizens & wallet**, for the Super Admin, shown through `NAV_PERM` with the new permissions:
- citizen accounts:
  - search by name or last 4 digits, and **Find by mobile or email** for a caller;
  - block, unblock, delete;
  - **Set new email** and **Remove mobile number**, each with the call-back question and a reason (3.4b);
- referrals (status tabs, confirm, reject, reason);
- wallet adjustment with a reason;
- CSV export.

The Settings page gains the eight wallet settings, with a mandal picker for the pilot that reuses the console's existing area picker. The console is a port with "zero visual change" (`.claude/conventions.md` in the web repo), so a new page needs the owner's sign-off and must reuse existing styles (table, tabs, panels).

**Website**
- `/app/?ref=<code>` already redirects to the website's download section and keeps the query (`apps/share/views.py:138-144`). The website reads `ref` (8 characters from the code alphabet, anything else ignored) and shows "Your friend's referral code: **CODE**" with a **Copy** button in the download section (`src/website/sections/home/Download.tsx`).
- New texts go in `locales/te.json` and `en.json`.
- The privacy policy and terms (`legal` in the locale files) gain the account, the data (the email included), the 18+ rule, "points have no cash value", and how to delete the account.
- A **Delete my wallet account** page:
  - the email or the mobile number, **Send code** (to the account's email), the code, then a confirmation;
  - it calls `citizen/auth/delete/code/` and `citizen/auth/delete/` on the same domain, so it needs no CORS;
  - the privacy policy links to it, and its address is the deletion URL given to Google Play;
  - Telugu and English, in the website's existing styles.

## 6. Setup and operations

| Step | Who | Notes |
|---|---|---|
| An email service account and a sender address on the client's domain, for example `no-reply@gramaconnect.com` | Client (C7) | Amazon SES must be asked to leave its test mode first, usually within a day |
| SPF, DKIM and DMARC records for the sender's domain | The client's domain admin with DevOps | Without them Gmail may treat the codes as spam |
| SMTP user and password | Client and DevOps | A secret: production `.env` only, never in git |
| Production `.env` | DevOps | `EMAIL_*`, `DEFAULT_FROM_EMAIL`, `CITIZEN_CONSENT_VERSION`; `WALLET_ENABLED` stays off |
| Per-IP limits on sign-up, login and codes | Backend | Depend on real visitor IPs (BVTGC-105, BVTGC-39) |
| Later stage (3.13): Meta Business verification, WhatsApp number and template, DLT registration, SMS provider | Client (C1–C3) | Days to weeks each; start early, well before Bills & Recharges |

**Cost.** Email is cheap: Amazon SES, for example, charges about US$0.10 per 1,000 emails (check the AWS price page), so 10,000 codes and notices a month cost well under ₹100. Other services have free tiers that may cover the pilot. WhatsApp and SMS costs are in 3.13.

## 7. Rollout

1. Backend merged and deployed with `WALLET_ENABLED` off. Nothing visible changes.
2. App build with the new screens, hidden while the switch is off. Released through the website as usual.
3. On qa: switch on, run the UAT checklist (new section in `docs/uat.md`), with a real email service so the codes are tested in Gmail's inbox.
4. Production: set the pilot mandals, then switch on in the console. Widen the list after the pilot (requirements Q11).
5. Before switching on in production:
   - the client's amounts (C4), the legal texts (C5) and the support number (C6);
   - email working (C7): a test code reaches a Gmail inbox, not spam;
   - the owner's answers to Q8 and Q9.
6. The later stage (3.13) goes live before Bills & Recharges.

## 8. Documents to update with the code

- `docs/api-contract.md`: new section 21, the error codes, the screen map and the `app-config` fields.
- `docs/authorization.md`: the Citizen group and the new permissions.
- `.claude/project.md` in all three repos:
  - the roles table (Citizen: an optional account for the wallet);
  - owner decision entries for D1 to D17 of 2026-11-25.
  - They include two exceptions for citizens, and the rules stay true for officials:
    - citizens get email codes for sign-up and a forgotten password (later WhatsApp and SMS), an exception to the 2026-10-21 rule "nothing is sent by text message or email for login or passwords";
    - citizens must give an email, an exception to "email is optional contact information only".
- `docs/security-review.md`: the new data (emails, citizen passwords with the shorter rule), the SMTP secret, the fraud controls.
- `docs/uat.md`: a wallet and referrals section.

## 9. Work and effort

Rough estimates in working days for one developer each, for the wallet without the later stage.

| Team | Work | Days |
|---|---|---|
| Backend | Models and migrations (2.1, 2.2) | 1.5 |
| Backend | Citizen role, permissions, the section 3.2 fixes (the officials' login refuses citizens) | 1.5 |
| Backend | Sign-up with the email code, login and lock, forgotten password, the citizen password rule | 3 |
| Backend | Email sending, templates in Telugu and English, code rules | 1.5 |
| Backend | Profile changes and Super Admin help (3.4a, 3.4b) | 1.5 |
| Backend | Referrals (with the family rule), wallet, jobs, deletion (with the website's code) | 4 |
| Backend | Admin endpoints, settings (with the pilot mandals), CSV | 2.5 |
| Backend | Notifications, audit labels, contract and documents, Telugu | 1.5 |
| Backend | Tests (section 3.12) | 3 |
| **Backend total** | | **20** |
| Mobile | Account state, Phone Number Hint, autofill | 1 |
| Mobile | Create account, email code, log in, forgot password | 3.5 |
| Mobile | Wallet, invite, profile changes (password, email, mobile), delete | 3.5 |
| Mobile | Invite links, pushes, feature switch with the pilot | 2 |
| Mobile | Tests | 2 |
| **Mobile total** | | **12** |
| Web | Console page with the help actions, settings (with the pilot mandal picker) | 5 |
| Web | Website code display, legal texts, the deletion page with an email code | 2 |
| **Web total** | | **7** |
| QA | Test plan and UAT on qa | 5 |
| DevOps | Email service, DNS records, `.env` | 1, plus the email service's approval (about a day) |

With backend, app and web in parallel after the contract (section 3.9) is agreed: about **5 weeks**. Meta's and DLT's reviews are no longer on the path to launch; the email setup takes days, not weeks. The later stage (3.13) adds about 2 weeks plus the review time, and must finish before Bills & Recharges.

**Suggested Jira breakdown** (one epic, "Citizen wallet and referrals"):
- Backend:
  - models and the Citizen role;
  - the "role means official" fixes;
  - sign-up, login and forgotten password with email codes;
  - profile changes and Super Admin help;
  - referrals and wallet;
  - admin API and settings;
  - deletion;
  - tests and contract.
- Mobile: sign-up, login and forgotten password; profile; wallet and invite; invite links.
- Web: console page; website, deletion page and legal texts.
- Client inputs: C4 to C7, and the pilot mandals (requirements Q11).
- Later stage (WhatsApp and SMS): its own tickets when it is planned, before BVTGC-107 starts, with C1 to C3.

## 10. Risks and open technical questions

| # | Risk or question | Mitigation or default |
|---|---|---|
| R1 | Fake referrals. Emails are free and the mobile number is not checked until the later stage; Play Integrity is off | The email code; Gmail variants counted as one; at most 3 welcome bonuses and no referrer reward on one phone; the holding period and the active-days rule; the monthly limit; Super Admin review; the pilot. Require Play Integrity at sign-up once on Google Play. Numbers are confirmed before points can be spent (D17) |
| R2 | Codes landing in spam, or not arriving | SPF, DKIM and DMARC (C7), a code in the subject, "check your spam folder" in the app, **Send again**, the help line (D16) |
| R3 | Recycled numbers: someone given an old number cannot open the previous owner's account (the password protects it), but cannot sign up with the number either | The Super Admin frees it after a call-back (D16). The "Is this your account?" check of v3 and v4 is gone |
| R4 | Per-IP limits on sign-up, login and codes depend on real visitor IPs | BVTGC-105 and the production team's confirmation (BVTGC-39) |
| R5 | Account deletion conflicts with "data is kept permanently" | Owner's decision (requirements Q8) before production |
| R6 | Officials' code paths assume "any role = official" (section 3.2), and a citizen now has a password | Fix with the new permission, the officials' login included; tests per endpoint |
| R7 | iPhone: no Android id, so the per-phone rules do not work there (a sign-up without an Android id gets no welcome bonus) | iPhone is not in Phase 1; revisit with App Attest (BVTGC-27) |
| R8 | Points gain a rupee value when Bills & Recharges go live (D4) | Keep `WalletEntry` append-only and idempotent from day one; the bills design adds a SPENT kind and refunds ([../bills-recharges/technical-implementation.md](../bills-recharges/technical-implementation.md), section 3.6) |
| R9 | Bots asking for many codes | Limits per account, per address, per phone and per internet address (3.4), and a daily sending cap at the email service. More important in the later stage, where SMS costs money |
| R10 | The pilot depends on the village chosen in the app, which anyone can change | Accepted: the pilot limits who sees the wallet, not who may join; every fraud rule applies everywhere |
| R11 | Many villagers do not use email, so they cannot sign up, or cannot reset the password, by themselves | The app explains that the phone's Google account is already a Gmail address, and autofill offers it; the help line (D16); in the later stage, a reset by WhatsApp or SMS |
| R12 | 8-character passwords are easier to guess than officials' 12 | The lock after 5 wrong passwords, the per-address throttle, the common-password list, and points of small value until Bills & Recharges. Review again before Bills & Recharges |
| R13 | Sign-up tells whether a number or an email is registered (409) | Accepted, so citizens know to log in or reset (FR-11); throttled per internet address |
| Q-T1 | Store the fingerprints of deleted accounts for 1 year? | Pending legal (requirements Q9) |
| Q-T2 | Should a citizen account also post (instead of the device token) later? | Out of scope (D2); the account and the device stay separate |

## References

- Django password validation: https://docs.djangoproject.com/en/5.2/topics/auth/passwords/#password-validation
- Django sending email: https://docs.djangoproject.com/en/5.2/topics/email/
- Amazon SES pricing: https://aws.amazon.com/ses/pricing/
- Android Phone Number Hint: https://developer.android.com/identity/phone-number-hint
- Flutter autofill (`AutofillGroup`, `autofillHints`): https://api.flutter.dev/flutter/widgets/AutofillGroup-class.html
- Google Play Install Referrer: https://developer.android.com/google/play/installreferrer
- Firebase Dynamic Links (shut down in 2025, so not an option for invite links): https://firebase.google.com/support/dynamic-links-faq
- Google Play account deletion requirements: https://support.google.com/googleplay/android-developer/answer/13327111
- Later stage: Android SMS Retriever and SMS User Consent APIs: https://developer.android.com/identity/sms-retriever
- Later stage: India's SMS rules and DLT registration (third-party guide): https://www.telerivet.com/blog/india-sms-compliance-trai-dlt-registration-and-tcccpr-guide
- Later stage: WhatsApp authentication templates: https://developers.facebook.com/docs/whatsapp/business-management-api/authentication-templates
- Later stage: WhatsApp Business Platform pricing: https://developers.facebook.com/docs/whatsapp/pricing
