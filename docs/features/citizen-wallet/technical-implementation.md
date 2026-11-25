# Citizen wallet and referrals: technical implementation

| | |
|---|---|
| Status | Draft design, 2026-11-25 (v4: family phones, deletion on the website, pilot mandals; v3: login by mobile number and a one-time code replaces Google sign-in). Nothing is built yet. |
| Requirements | [requirements.md](requirements.md) (decisions D1–D13, rules in section 7) |
| Code read for this design | BackEnd `dev` `70b0f19`, Web `dev` `241cf1b`, Mobile `dev` `b3b7eff` |
| Owners | Backend (Basam Bhargavi), Mobile and web (Mohit), QA (Anvesh Kolli) |

The design follows the backend's own rules (`.claude/conventions.md`): roles and permissions only in `apps/accounts/role_matrix.py`, every check through `HasPerm` and `authz`, portable ORM, audit only through `record()`, every user-facing message in `_()` with its Telugu, and the contract first.

## 1. Overview

```mermaid
sequenceDiagram
    participant App as Mobile app
    participant API as Backend
    participant MSG as WhatsApp Cloud API or SMS provider
    App->>API: POST citizen/auth/code/ {mobile, channel, android_id?}
    API->>MSG: a 6-digit code (WhatsApp template, or DLT-registered SMS)
    MSG-->>App: the code on WhatsApp or by SMS
    App->>API: POST citizen/auth/verify/ {mobile, code}
    alt the number has an account
        API-->>App: JWT pair + account (or "Is this your account?" when unused for long)
    else a new number
        API-->>App: signup_token (15 minutes, used once)
        App->>API: POST citizen/auth/signup/ {signup_token, name, village_id, age_confirmed, consent_version, language, referral_code?, android_id?}
        API-->>App: JWT pair + account (Citizen role in the village); a referral starts PENDING
    end
    Note over API: Hourly job: PENDING referrals past their hold date are CONFIRMED or REJECTED
```

Three things are new: a **citizen account** (a Django user with the new Citizen role), **sign-in by mobile number** with a one-time code (WhatsApp, or SMS as the backup), and a **wallet** with **referrals**. Device posting tokens (`apps.citizens`, `X-Citizen-Token`) stay as they are: posting and service requests do not need the account (D2).

## 2. Data model

### 2.1 Extend `apps.citizens` (accounts)

`apps.citizens` already owns citizen identity (phones, posting tokens). The account lives next to it.

**CitizenAccount**

| Field | Type | Notes |
|---|---|---|
| `user` | OneToOne `accounts.User`, primary key | The Django user. `set_unusable_password()`: a citizen never logs in with a password. |
| `village_id` | PositiveInteger | Also the area of the Citizen role assignment. |
| `age_confirmed_at` | DateTime | 18+ confirmation (D5). |
| `consent_version`, `consent_at` | Char(20), DateTime | Which consent notice was agreed, and when. |
| `mobile_hash` | Char(64), unique, null | Keyed hash of the account's 10-digit number, which is the login (same keying as `citizens.services._keyed_hash`). Null only after deletion. Not `User.mobile`: that column is unique across officials (`accounts/models.py:101-108`), so an official's number would clash. |
| `mobile_last4` | Char(4) | For display and for the Super Admin's search. |
| `mobile_verified_at` | DateTime | When the current number was last proven with a code (sign-up or number change). |
| `last_active_at` | DateTime | The last day the app opened with the account (section 3.6). The unused account check reads it (FR-12a). |
| `referral_code` | Char(8), unique | Generated, no look-alike characters (the alphabet of `accounts/credentials.py:32`). |
| `signup_android_id_hash` | Char(64), indexed | From the phone used at sign-up. Limits the welcome bonuses per phone and skips the referrer's reward when both accounts signed up on the same phone (D10). |
| `deleted_at` | DateTime, null | Set when the citizen deletes the account (section 3.8). |
| `deleted_mobile_hash` | Char(64), indexed | Kept after deletion to stop repeat welcome bonuses (requirements Q9, pending legal review). |
| `created_at` | DateTime | |

**MobileCode** (one row per code sent; never holds the full number)

| Field | Notes |
|---|---|
| `account` | FK CitizenAccount, null: a sign-in code for a number without an account has none |
| `purpose` | `LOGIN`, `CHANGE_NUMBER` or `DELETE` (the website's deletion page) |
| `channel` | `WHATSAPP` or `SMS` |
| `mobile_hash`, `mobile_last4` | The number the code was sent to |
| `android_id_hash` | The phone that asked, for the per-phone limit; may be empty |
| `code_hash` | HMAC of the code with the row id and `SECRET_KEY`; the code itself is never stored or logged |
| `expires_at` | Sent + 10 minutes |
| `attempts` | Wrong tries, at most 5 |
| `verified_at` | Null until the right code is entered |
| `consumed_at` | Set when a sign-up, an "Is this your account?" answer or a number change uses the code; each code is used once |
| `provider_message_id` | WhatsApp or SMS provider's message id, for support |
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
| `referee` | OneToOne CitizenAccount (a citizen is referred at most once, FR-16) |
| `status` | `PENDING` (held), `CONFIRMED`, `REJECTED`. Version 2 also had `WAITING` for accounts without a verified number; since v3 every account has one from sign-up. |
| `reject_reason` | One of the keys in section 3.4 |
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
    "password_min_length": 0,      # no password
    "permissions": ["wallet.use_wallet"],
},
```

The key matches the existing `CITIZEN_ROLE = "CITIZEN"` used for device posts (`apps/posts/models.py:21`), so audit and post history read the same.

New permissions (model `Meta.permissions`, then `sync_role_permissions`; steps in `docs/authorization.md`, section 10):

| Permission | Held by |
|---|---|
| `wallet.use_wallet` | Citizen |
| `wallet.view_wallets`, `wallet.adjust_points`, `wallet.review_referrals`, `wallet.manage_wallet_settings` | Super Admin (add to `PLATFORM_ADMIN`) |
| `accounts.manage_citizen_accounts` | Super Admin (search, block, unblock, delete citizen accounts) |
| `accounts.use_official_api` | Every official group (section 3.2) |

Sign-up creates the RoleAssignment directly (`assigned_by` null) and audits it. A village change moves the assignment's area.

**Citizens stay out of the officials' user lists without extra code:** `authz.manageable_users` (`apps/accounts/authz.py:473-505`) keeps only users whose every assignment the actor may manage, and a group with `manage_permission: None` is managed by nobody. A test must lock this in.

### 3.2 Places that treat "has a role" as "is an official" (must change)

Today every user with a role is an official, and some code relies on that. A Citizen account breaks the assumption here:

| Where | What happens with a Citizen account | Change |
|---|---|---|
| `apps/web_admin/api.py:562` `HasAnyRole` (Telugu name suggestions) | A citizen could call `admin/transliterate/`, which calls Google | Use `HasPerm("accounts.use_official_api")` |
| `apps/posts/services.py:229` `covering_scope_q` ("whatever its permissions") | `GET posts/` would list the village's published posts to a citizen account | Count only grants holding `accounts.use_official_api`, or gate `PostViewSet` with that permission |
| `apps/accounts/views_auth.py` `PasswordChangeView` | A citizen has no password: each try counts as a wrong password and ends sessions after 5 | Refuse citizen accounts with 403 `permission_denied` |
| `apps/accounts/serializers.py` `capabilities()` / `MeSerializer` | Works, but the app should not read officials' fields | The app uses `citizen/account/`; `me/` stays for officials |
| Dashboard and reports (`apps/web_admin/api.py`) | Counts such as blocked users may include citizen accounts | Count officials only (holders of `accounts.use_official_api`) |

Before merging, grep for `IsAuthenticated]` and `authz.grants(` and confirm each place. Every endpoint officials use must answer a citizen account with 403, with a test per endpoint.

### 3.3 Sign-in with a one-time code

Four public endpoints, throttled per internet address like the other public citizen endpoints. No email and no Google account are asked for or stored (D1).

**`POST citizen/auth/code/`** `{mobile, channel, purpose?, android_id?}`
- `purpose` is `login` (the default) or `delete` (the website's deletion page, 3.8).
- Checks the number with `mobile_validator` (`apps/accounts/models.py:14`), the channel against the gateways that are on, and the limits (3.4).
- Creates a MobileCode (`purpose` LOGIN, `account` set when the number already has one) and sends the code in the request (3.4).
- Answers the same `{expires_in, resend_after, channel}` whether or not the number has an account, so it never tells who is registered (FR-11).

**`POST citizen/auth/verify/`** `{mobile, code}`
- Finds the newest live LOGIN code for the number's hash. A wrong code is a conditional UPDATE on `attempts < 5` (`code_wrong` with `attempts_left`).
- The right code sets `verified_at`. Then:
  - an active account with this `mobile_hash`, used within `CITIZEN_STALE_DAYS`: `{status: "signed_in", access, refresh, account}`;
  - an active account unused for longer: `{status: "confirm", masked_name, confirm_token}` (FR-12a). `masked_name` keeps the first letter of each word of the name and replaces the rest with `***`;
  - a blocked account (`is_active` false): 403 `account_blocked`, the existing code;
  - no account: `{status: "new", signup_token}`.

**`POST citizen/auth/signup/`** `{signup_token, name, village_id, age_confirmed, consent_version, language, referral_code?, android_id?}`
- Needs `age_confirmed: true`, the current `consent_version`, an active village, a name of 1 to 100 characters, and the Wallet on setting. While `WALLET_PILOT_MANDALS` is not empty, the village must be in one of those mandals (403 `wallet_not_in_area`, D13).
- Creates, in one transaction:
  - the user, with a generated username that passes `username_validator` (never shown, never used to log in), the name, `preferred_language` and an unusable password;
  - the Citizen assignment;
  - the account (`mobile_hash`, `mobile_last4`, `mobile_verified_at`), its referral code and the wallet;
  - the referral, if a code was given (3.5).
- A bad referral code refuses the whole sign-up with its error code, so the app can let the citizen fix or clear it.
- Two sign-ups racing for one number: the unique `mobile_hash` lets one win, and the other gets 409 `mobile_in_use`.

**`POST citizen/auth/confirm/`** `{confirm_token, is_mine}`
- `true`: signs in, as above.
- `false`: closes the old account as in 3.8, audited as `citizen.account_released`, then answers `{status: "new", signup_token}`.

**Tokens and sessions**
- `signup_token` and `confirm_token` are signed with `django.core.signing` (a salt for each purpose). They are valid for 15 minutes and carry only the MobileCode id.
- Using a token sets the code's `consumed_at` with a conditional UPDATE (`consumed_at IS NULL`), so each token works once (401 `signup_token_invalid` after that). The full number is never in a token, a row, a log or a task argument.
- Sessions are issued with `accounts.tokens.issue_tokens(user)`, so the token version, blocking and the 90-day limit (`JWT_ABSOLUTE_DAYS`) work as for officials.
- Refresh and logout use the existing `auth/refresh/` and `auth/logout/`.

### 3.4 Sending codes: WhatsApp and SMS

**Gateways.** Pluggable, like `apps/notifications/push.py`.
- `WHATSAPP_GATEWAY` names `apps.citizens.whatsapp.MetaCloudGateway` (production) or `LocmemWhatsAppGateway` (tests). It is off without `WHATSAPP_ACCESS_TOKEN`.
- `SMS_GATEWAY` names a class for the Indian SMS provider the client picks (C3), or `LocmemSmsGateway` (tests). It is off without `SMS_API_KEY`.
- `GET app-config/` lists the channels that are on (`citizen_code_channels`), so the app shows only buttons that work. A channel that is off answers 503 `channel_unavailable`.

**WhatsApp.** `POST https://graph.facebook.com/<version>/<WHATSAPP_PHONE_NUMBER_ID>/messages` with the approved authentication template (`WHATSAPP_OTP_TEMPLATE`), in the citizen's language (`te` or `en`), the code as the body parameter and the copy-code button parameter. Start with the **copy-code** button. One-tap autofill needs the package name `com.bvt.app`, each signing key's hash and a native Android handshake in the app, so it comes later (Q-T2).

**SMS.**
- The provider sends the DLT-registered template for the language (`SMS_DLT_TEMPLATE_IDS`) from the sender name `SMS_SENDER_ID`, with `SMS_DLT_ENTITY_ID`. For example: "<code> is your Grama Connect code. Do not share it with anyone."
- The app reads it with Android's **SMS User Consent API**: when the SMS arrives, Android asks once "Allow Grama Connect to read this message?" and fills the code in. Unlike the SMS Retriever API, it needs no app hash in the message, so it works the same for every signing key, and the message can be in Telugu.
- A Telugu SMS is Unicode (70 characters per part) and costs more than English. Check the provider's price for the Telugu text.

**Sending in the request.** Both channels send **in the request**, with a 10-second timeout, not in a django-q job: the conventions forbid full mobile numbers in task arguments, and this way the number is never stored.

**Rules** (FR-10, FR-11)
- 6 digits from `secrets.randbelow`, valid 10 minutes, 5 wrong tries.
- A new code after 60 seconds (429 `code_cooldown` with `retry_after_s`).
- At most 5 codes a day per number over both channels, and 10 a day per phone (`android_id_hash`), on top of the per-address throttle (429 `code_limit`).
- Wrong tries go through a conditional UPDATE (`attempts < 5`), so parallel tries cannot exceed the limit on any database.

**Delivery.** WhatsApp may accept a message for a number without WhatsApp and report the failure only later, by webhook. So the app offers **Send by SMS instead** when the resend timer ends. A status webhook can come later if support needs it.

**Audit.** `citizen.code_sent` (channel, purpose, last 4 digits only). Never the code or the full number.

### 3.4a Changing the number

- `POST citizen/account/mobile/` `{mobile, channel}` (citizen JWT) sends a CHANGE_NUMBER code to the new number, with the same rules. It does not say whether another account holds the number.
- `POST citizen/account/mobile/verify/` `{code}`: the right code sets the account's `mobile_hash`, `mobile_last4` and `mobile_verified_at` in one UPDATE.
  - If another account holds the number, the unique constraint answers 409 `mobile_in_use`, told only to the person who proved that number.
  - The citizen's other sessions end (token version bump) and this phone gets new tokens.
  - Audit `citizen.mobile_changed` with the old and the new last 4 digits.
- No Super Admin endpoint moves an account to another number (D8).

### 3.5 Referrals and the wallet

**Giving a code** (only in `citizen/auth/signup/`; FR-14, D9)

The sign-up is refused with:

| Error code | When |
|---|---|
| `referral_code_invalid` | No such code |
| `referral_code_own` | The referrer is the same person: the same mobile hash |
| `referral_not_open` | The referrer's account is blocked or deleted |

Otherwise, in the sign-up's transaction, a Referral is created as PENDING with `confirm_after = now + REFERRAL_HOLD_DAYS`. Up to two HELD entries are created, with idempotency keys `ref:<referral id>:referrer` and `ref:<referral id>:welcome`:
- the referrer's reward, unless both accounts signed up on the same phone (equal `signup_android_id_hash`; D10, FR-15a). The history then notes `same_phone`;
- the referee's welcome bonus, unless `WELCOME_BONUSES_PER_PHONE` accounts with the same `signup_android_id_hash` (deleted ones included) already got one, or a deleted account with the same mobile hash did, or the sign-up sent no Android id. A skipped bonus gets a note in the history.

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
- Adjust a wallet with a reason: an ADJUSTMENT entry; a negative one uses `UPDATE … WHERE confirmed_points >= n`, else 409 `not_enough_points` (FR-23).

**Consistency.** A daily job compares each wallet's cache with the sum of its entries and audits `wallet.mismatch` if they differ. The entries are the truth.

### 3.6 Active days

`GET citizen/account/`, which the app calls when it opens, inserts today's CitizenActiveDay row if it is missing (`get_or_create` on the unique pair). That is one cheap write per account per day, and it gives the active rule (requirements section 7) without tracking anything else. The same write sets `last_active_at`, which the unused account check reads (3.3); the day rows are deleted after 60 days, so the check cannot use them.

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
| `CITIZEN_STALE_DAYS` | 180 (the unused account check, FR-12a; requirements Q10) |
| `WELCOME_BONUSES_PER_PHONE` | 3 (D10) |
| `WALLET_PILOT_MANDALS` | Empty, meaning everywhere; a comma-separated list of mandal ids while the pilot runs (D13) |

Env only (secrets and fixed values):
- `WHATSAPP_GATEWAY`, `WHATSAPP_ACCESS_TOKEN` (secret), `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_OTP_TEMPLATE`, `WHATSAPP_API_VERSION`
- `SMS_GATEWAY`, `SMS_API_KEY` (secret), `SMS_SENDER_ID`, `SMS_DLT_ENTITY_ID`, `SMS_DLT_TEMPLATE_IDS` (one per language)
- `CITIZEN_CONSENT_VERSION`

`GET app-config/` adds `wallet_enabled`, so the app and the website hide the feature while it is off, `wallet_pilot_mandals`, so the app hides it outside the pilot, and `citizen_code_channels` (3.4).

### 3.8 Account deletion

Three ways in, all through one service function:
- `POST citizen/account/delete/`: the citizen, in the app;
- `POST citizen/auth/delete/` `{mobile, code}`: the website's deletion page, after `citizen/auth/code/` with `purpose: delete` (D12). Only the owner of the number can delete;
- `POST admin/citizen-accounts/{id}/delete/`: the Super Admin, with a reason, for fraud or a legal request.

In one transaction:
- `is_active` false and `revoke_all_sessions`;
- the name replaced with "Deleted citizen";
- the username scrambled;
- push devices unlinked;
- `mobile_hash` moved to `deleted_mobile_hash` (kept for 1 year if legal agrees, requirements Q9, then cleared by housekeeping);
- the role assignment deactivated;
- HELD entries cancelled.

Confirmed entries and referrals stay, with no personal details, for the audit trail. Audit `citizen.account_deleted` with `via` (`app`, `website` or `admin`). A **No** to "Is this your account?" (3.3) closes the old account the same way, audited as `citizen.account_released`. This is the exception to "data is kept permanently" (requirements Q8, needs the owner's confirmation).

### 3.9 API (draft for `docs/api-contract.md`, new section 21)

All under `/api/v1/`. Citizen endpoints need a citizen JWT holding `wallet.use_wallet`. Every endpoint answers 404 `not_found` while `WALLET_ENABLED` is off.

| Method and path | Who | Request | Response |
|---|---|---|---|
| `POST citizen/auth/code/` | public | `{mobile, channel, purpose?, android_id?}` | `{expires_in, resend_after, channel}`, the same for every number |
| `POST citizen/auth/verify/` | public | `{mobile, code}` | `{status: "signed_in", access, refresh, account}`, `{status: "confirm", masked_name, confirm_token}` or `{status: "new", signup_token}` |
| `POST citizen/auth/signup/` | public | `{signup_token, name, village_id, age_confirmed, consent_version, language, referral_code?, android_id?}` | 201 `{access, refresh, account}` |
| `POST citizen/auth/confirm/` | public | `{confirm_token, is_mine}` | As `verify/`: `signed_in`, or `new` with a `signup_token` |
| `POST citizen/auth/delete/` | public (the website) | `{mobile, code}` (a `delete` code) | `{deleted: true}`, or `{deleted: false}` when the number has no account |
| `GET citizen/account/` | citizen | — | `Account`: name, village, `mobile_verified`, `mobile_last4`, `referral_code`, `invite_url`, wallet summary, `referral` (own, if any). Records today's active day |
| `PATCH citizen/account/` | citizen | `{village_id?, language?}` | `Account` |
| `POST citizen/account/mobile/` | citizen | `{mobile, channel}` (the new number) | `{expires_in, resend_after, channel}` |
| `POST citizen/account/mobile/verify/` | citizen | `{code}` | `{access, refresh, account}`; 409 `mobile_in_use` |
| `POST citizen/account/delete/` | citizen | `{confirm: true}` | 204 |
| `GET wallet/` | citizen | — | `{confirmed_points, held_points, next_release_at, entries}`; entries are a cursor list (`id, points, kind, status, reason, created_at, decided_at`) |
| `GET admin/citizen-accounts/` | `accounts.manage_citizen_accounts` | `q` (name or last 4 digits), `village_id`, `status`, page | rows: name, village, `mobile_last4`, verified, balance, held, status, `created_at` |
| `POST admin/citizen-accounts/{id}/block/`, `/unblock/`, `/delete/` | `accounts.manage_citizen_accounts` | `{reason}` | row |
| `GET admin/referrals/` | `wallet.review_referrals` | `status`, `q`, page | rows with both people (name, last 4 digits), dates, reason, `allowed_actions` |
| `POST admin/referrals/{id}/confirm/`, `/reject/` | `wallet.review_referrals` | `{comment}` | row; 409 `already_decided` if the job got there first |
| `POST admin/wallets/{account_id}/adjust/` | `wallet.adjust_points` | `{points, reason}` | wallet; 409 `not_enough_points` |
| `GET admin/wallet/export/?type=referrals\|entries&format=csv` | `wallet.view_wallets` | filters | CSV with BOM, last 4 digits only |
| `GET`/`PATCH admin/settings/` | `posts.manage_settings` (existing) | adds the nine runtime settings above | settings |
| `GET app-config/` | public | — | adds `wallet_enabled`, `wallet_pilot_mandals` and `citizen_code_channels` |

New error codes for section 1 of the contract:
- `age_required`, `signup_token_invalid` (expired or already used), `wallet_not_in_area`
- `channel_unavailable`, `code_cooldown`, `code_limit`, `code_wrong` (with `attempts_left`), `code_expired`, `mobile_invalid`, `mobile_in_use`
- `referral_code_invalid`, `referral_code_own`, `referral_not_open`
- `not_enough_points`

### 3.10 Notifications

- New `NotificationKind.WALLET`.
- Templates (data migration, editable in the console like the others): `referral_joined` (to the referrer), `points_confirmed`, `points_not_confirmed`, in Telugu and English.
- The app registers its device with the citizen's JWT (`POST devices/` already links a device to the signed-in user, `apps/notifications/views.py`). Pushes go through `send_push_to_users`, so retries and audit work as today.
- The data carries only ids (`type`, `referral_id`), never names.

### 3.11 Audit actions

| Group | Actions |
|---|---|
| Accounts | `citizen.account_created`, `citizen.code_sent` (channel, purpose), `citizen.mobile_changed`, `citizen.village_changed`, `citizen.account_blocked`, `citizen.account_unblocked`, `citizen.account_deleted`, `citizen.account_released` |
| Referrals | `referral.created`, `referral.confirmed`, `referral.rejected` |
| Wallet | `wallet.adjusted`, `wallet.mismatch` |
| Settings | `setting.changed` (existing) for the wallet settings |

Logins use the existing `auth.login` with `via="app"`. Audit `data` holds the last 4 digits of a mobile at most. Add every key to the console's and the app's audit labels.

### 3.12 Tests (all on SQLite, each new file in `grama/tests/suite.json`)

- **Permissions:** extend `apps/accounts/tests/test_matrix.py` with the Citizen group. Test that a citizen account gets 403 on every officials' endpoint (one test per endpoint) and never appears in `users/` for any official.
- **Auth:** sign-in for a new and a known number; the same answer from `auth/code/` for registered and unregistered numbers; sign-up and confirm tokens expire and work once; the unused account check (yes and no); two sign-ups racing for one number; blocked account; 90-day limit; password change refused.
- **Codes:** expiry, attempts, cooldown, the limits per number (both channels together), per phone and per address, a channel that is off, number change with `mobile_in_use`, and that no code or full number appears in the audit log, logs or task arguments.
- **Referrals:** each reject reason; self-referral by mobile; a same-phone referral pays only the welcome bonus; the welcome limit per phone (deleted accounts included); no welcome bonus without an Android id; the monthly limit; two parallel confirmations (one wins); the idempotency key against double rewards.
- **Wallet:** the cache equals the sum of entries after every operation; a negative adjustment below zero is refused.
- **Deletion:** personal fields cleared, entries kept, re-sign-up gets no second welcome bonus; the website's deletion needs that number's `delete` code, and a `login` code does not work for it.
- **Pilot:** sign-up outside the pilot mandals is refused; an existing account whose village moves outside keeps working; an empty list means everywhere.
- **Telugu:** every new message has its Telugu (`grama/tests/test_i18n.py`).

## 4. Mobile app (Flutter)

- **Package:** one for Android's Phone Number Hint and SMS User Consent APIs, such as `smart_auth`, pinned in `pubspec.yaml`; check its current version when implementing. Both APIs need Google Play services, but no Google account and no SMS permission. Nothing is registered per signing key, so the website's APK works as is.
- **iPhone later:** iOS offers an SMS code above the keyboard by itself. With no Google login, Sign in with Apple is not needed either (App Store rule 4.8 covers third-party logins).
- **One account at a time:** an official or a citizen account, never both. The existing `ApiClient` token handling (secure storage, refresh, `session_revoked`) is reused unchanged. Citizen mode without an account stays the default.
- **Screens** (Telugu and English, existing widgets and colours):
  - **Sign in to earn points:** the number (offered from the phone's SIMs), why it is needed with the privacy policy link, **Send code on WhatsApp** and **Send code by SMS** (only the channels `app-config/` lists).
  - **Enter the code:** the code field (an SMS code fills in after **Allow**), the resend timer, then **Send by SMS instead**.
  - **About you** (new numbers only): the name, the consent notice with links to the privacy policy and terms, the 18+ tick, the referral code field (pre-filled from an invite link).
  - **Is this your account?** (unused accounts): the masked name, **Yes**, and **No** with a warning that the old account's points are lost.
  - **Wallet:** confirmed balance, points on hold with the expected date, history with each status and reason, "Points have no cash value", and **Questions about points?**, which opens the existing help-line numbers screen. The Super Admin lists the support number there as a statewide help line (D11, C6), so nothing new is needed on the server.
  - **Invite friends:** the code, Copy, and Share (WhatsApp first, through the existing `share_plus`).
  - **Profile:** Wallet, Invite, Change my mobile number, Sign out, Delete my account (with a confirmation).
- **Invite links:** add `android:pathPrefix="/app/"` next to the existing `/p/` App Link (`android/app/src/main/AndroidManifest.xml:44`). The server's `/.well-known/assetlinks.json` covers the domain, so no backend change is needed. The app stores `ref` until sign-up. Installs from the website get the code from the link or by typing it. Once the app is on Google Play, the Play Install Referrer can fill it too (it does not work for APKs installed outside Play).
- **On open:** `GET citizen/account/` (records the active day), and `POST devices/` with the citizen's JWT so pushes reach the account.
- **Feature switch:** everything hides while `app-config/` says `wallet_enabled: false`, and also while the village chosen in the app is outside `wallet_pilot_mandals` (when the list is not empty). There, an invite link opens the app with "The wallet is coming to your area soon". A signed-in account keeps its wallet wherever its village moves.
- **Tests:** flows against the local fake server (as `test/app_flow_test.dart`): sign-up by WhatsApp and by SMS, signing in again, the unused account question, number change, wallet, invite link pre-fill, sign out, delete.

## 5. Web (BVTGramaConnect)

**Console.** A new page, **Citizens & wallet**, for the Super Admin, shown through `NAV_PERM` with the new permissions:
- citizen accounts (search by name or last 4 digits, block, unblock, delete);
- referrals (status tabs, confirm, reject, reason);
- wallet adjustment with a reason;
- CSV export.

The Settings page gains the nine wallet settings, with a mandal picker for the pilot that reuses the console's existing area picker. The console is a port with "zero visual change" (`.claude/conventions.md` in the web repo), so a new page needs the owner's sign-off and must reuse existing styles (table, tabs, panels).

**Website**
- `/app/?ref=<code>` already redirects to the website's download section and keeps the query (`apps/share/views.py:138-144`). The website reads `ref` (8 characters from the code alphabet, anything else ignored) and shows "Your friend's referral code: **CODE**" with a **Copy** button in the download section (`src/website/sections/home/Download.tsx`).
- New texts go in `locales/te.json` and `en.json`.
- The privacy policy and terms (`legal` in the locale files) gain the account, the data, the 18+ rule, "points have no cash value", and how to delete the account.
- A **Delete my wallet account** page: the mobile number, **Send code** (WhatsApp or SMS), the code, then a confirmation. It calls `citizen/auth/code/` (`purpose: delete`) and `citizen/auth/delete/` on the same domain, so it needs no CORS. The privacy policy links to it, and its address is the deletion URL given to Google Play. Telugu and English, in the website's existing styles.

## 6. Setup and operations

| Step | Who | Notes |
|---|---|---|
| Meta Business verification, WhatsApp Business number, display name | Client (C1) | Can take days to weeks; start first |
| Authentication template in Telugu and English, approved by Meta | Backend with the client (C2) | Copy-code button |
| WhatsApp system-user access token | Client and DevOps | A secret: production `.env` only, never in git |
| DLT registration: the business, a 6-letter sender name, the code message in Telugu and English; an account with an SMS provider | Client (C3) | A few days per approval; start together with the Meta steps |
| SMS provider API key | Client and DevOps | A secret: production `.env` only, never in git |
| Production `.env` | DevOps | `WHATSAPP_*`, `SMS_*`, `CITIZEN_CONSENT_VERSION`; `WALLET_ENABLED` stays off |
| Per-IP limits on sign-up and codes | Backend | Depend on real visitor IPs (BVTGC-105, BVTGC-39) |

**Cost.** WhatsApp authentication messages for an Indian business to Indian numbers are reported at about ₹0.115 each (2026 figures from third-party guides; check Meta's rate card). One verification normally needs one message. SMS codes cost about ₹0.10 to ₹0.45 each, Telugu at the higher end (third-party 2026 figures; the provider's rate card decides). If a fifth of codes go by SMS, 10,000 sign-ins a month cost roughly ₹1,500 to ₹3,000 with tax.

## 7. Rollout

1. Backend merged and deployed with `WALLET_ENABLED` off. Nothing visible changes.
2. App build with the new screens, hidden while the switch is off. Released through the website as usual.
3. On qa: switch on, run the UAT checklist (new section in `docs/uat.md`).
4. Production: set the pilot mandals, then switch on in the console. Widen the list after the pilot (requirements Q11).
5. Before switching on in production: the client's amounts (C4), the legal texts (C5) and the owner's answers to Q8 and Q9.

## 8. Documents to update with the code

- `docs/api-contract.md`: new section 21, the error codes, the screen map and the `app-config` field.
- `docs/authorization.md`: the Citizen group and the new permissions.
- `.claude/project.md` in all three repos: the roles table (Citizen: an optional account for the wallet), and owner decision entries D1–D7 of 2026-11-25. They include WhatsApp and SMS codes for citizens, an exception to the 2026-10-21 rule "nothing is sent by text message or email for login", which stays true for officials.
- `docs/security-review.md`: new data, the WhatsApp secret, fraud controls.
- `docs/uat.md`: a wallet and referrals section.

## 9. Work and effort

Rough estimates in working days for one developer each.

| Team | Work | Days |
|---|---|---|
| Backend | Models and migrations (2.1, 2.2) | 1.5 |
| Backend | Citizen role, permissions, the section 3.2 fixes, code sign-in and sign-up, number change, unused account check | 3.5 |
| Backend | WhatsApp and SMS gateways and code rules | 2.5 |
| Backend | Referrals (with the family rule), wallet, jobs, deletion (with the website's code) | 4 |
| Backend | Admin endpoints, settings (with the pilot mandals), CSV | 2.5 |
| Backend | Notifications, audit labels, contract and documents, Telugu | 1.5 |
| Backend | Tests (section 3.12) | 2.5 |
| **Backend total** | | **18** |
| Mobile | Phone Number Hint, SMS code autofill and the account state | 2 |
| Mobile | Seven screens and dialogs and the profile entries, Telugu and English | 5.5 |
| Mobile | Invite links, pushes, feature switch with the pilot | 2 |
| Mobile | Tests | 2 |
| **Mobile total** | | **11.5** |
| Web | Console page and settings (with the pilot mandal picker) | 4.5 |
| Web | Website code display, legal texts, the deletion page with a code | 2 |
| **Web total** | | **6.5** |
| QA | Test plan and UAT on qa | 5 |
| DevOps | Secrets, WhatsApp template, SMS provider and DLT details, `.env` | 1.5, plus Meta's and DLT review time |

With backend, app and web in parallel after the contract (section 3.9) is agreed: about **5 weeks**. Meta's verification and the DLT registration are the likely critical path.

**Suggested Jira breakdown** (one epic, "Citizen wallet and referrals"):
- Backend: models and the Citizen role; the "role means official" fixes; mobile sign-in (codes, sign-up, number change); WhatsApp and SMS gateways; referrals and wallet; admin API and settings; deletion; tests and contract.
- Mobile: sign-in and account; number change; wallet and invite; invite links.
- Web: console page; website and legal texts.
- Client inputs: C1 to C6, and the pilot mandals (requirements Q11).

## 10. Risks and open technical questions

| # | Risk or question | Mitigation or default |
|---|---|---|
| R1 | Fake referrals with spare SIMs (Play Integrity is off) | Holding period, active-days rule, at most 3 welcome bonuses and no referrer reward on one phone, monthly limit, Super Admin review, the pilot; require Play Integrity at sign-up once on Google Play |
| R2 | WhatsApp may report a non-WhatsApp number only later (webhook) | **Send by SMS instead** when the resend timer ends; a status webhook later if support needs it |
| R3 | Recycled numbers: someone given an old number could open the previous owner's account | The unused account check (FR-12a, `CITIZEN_STALE_DAYS`). A new owner who falsely answers Yes gets the old points: accepted for small balances. Numbers can go to someone new after 90 days, so the client may lower the setting (requirements Q10) |
| R4 | Per-IP limits on sign-up and codes depend on real visitor IPs | BVTGC-105 and the production team's confirmation (BVTGC-39) |
| R5 | Account deletion conflicts with "data is kept permanently" | Owner's decision (requirements Q8) before production |
| R6 | Officials' code paths assume "any role = official" (section 3.2) | Fix with the new permission; tests per endpoint |
| R7 | iPhone: no Android id, so the per-phone rules do not work there (a sign-up without an Android id gets no welcome bonus) | iPhone is not in Phase 1; revisit with App Attest (BVTGC-27) |
| Q-T1 | Store the hashes of deleted accounts for 1 year? | Pending legal (requirements Q9) |
| Q-T2 | One-tap autofill of WhatsApp codes on Android | After launch; copy-code first. SMS codes fill in from day one (SMS User Consent) |
| Q-T3 | Should a citizen account also post (instead of the device token) later? | Out of scope (D2); the account and the device stay separate |
| R8 | Points gain a rupee value when Bills & Recharges go live (D4) | Keep `WalletEntry` append-only and idempotent from day one; the bills design adds a SPENT kind and refunds ([../bills-recharges/technical-implementation.md](../bills-recharges/technical-implementation.md), section 3.6) |
| R9 | Bots asking for many codes to run up the SMS bill | Indian 10-digit numbers only, limits per number, per phone and per address (3.4), and a daily spending cap at the SMS provider |
| R10 | The pilot depends on the village chosen in the app, which anyone can change | Accepted: the pilot limits who sees the wallet, not who may join; every fraud rule applies everywhere |

## References

- Android Phone Number Hint: https://developer.android.com/identity/phone-number-hint
- Android SMS Retriever and SMS User Consent APIs: https://developer.android.com/identity/sms-retriever
- India's SMS rules and DLT registration (third-party guide): https://www.telerivet.com/blog/india-sms-compliance-trai-dlt-registration-and-tcccpr-guide
- WhatsApp authentication templates: https://developers.facebook.com/docs/whatsapp/business-management-api/authentication-templates
- WhatsApp Business Platform pricing: https://developers.facebook.com/docs/whatsapp/pricing
- Google Play Install Referrer: https://developer.android.com/google/play/installreferrer
- Firebase Dynamic Links (shut down in 2025, so not an option for invite links): https://firebase.google.com/support/dynamic-links-faq
- Google Play account deletion requirements: https://support.google.com/googleplay/android-developer/answer/13327111
