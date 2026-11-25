# Citizen wallet and referrals: technical implementation

| | |
|---|---|
| Status | Draft design, 2026-11-25. Nothing is built yet. |
| Requirements | [requirements.md](requirements.md) (decisions D1–D7, rules in section 7) |
| Code read for this design | BackEnd `dev` `70b0f19`, Web `dev` `241cf1b`, Mobile `dev` `b3b7eff` |
| Owners | Backend (Basam Bhargavi), Mobile and web (Mohit), QA (Anvesh Kolli) |

The design follows the backend's own rules (`.claude/conventions.md`): roles and permissions only in `apps/accounts/role_matrix.py`, every check through `HasPerm` and `authz`, portable ORM, audit only through `record()`, every user-facing message in `_()` with its Telugu, and the contract first.

## 1. Overview

```mermaid
sequenceDiagram
    participant App as Mobile app
    participant G as Google
    participant API as Backend
    participant WA as WhatsApp Cloud API
    App->>G: Google sign-in (serverClientId = web client id)
    G-->>App: ID token
    App->>API: POST citizen/account/google/ {id_token, village_id, age_confirmed, consent_version, referral_code?}
    API->>G: verify the ID token (google-auth, cached keys)
    API-->>App: JWT pair + account (Citizen role in the village)
    App->>API: POST citizen/account/mobile/ {mobile}
    API->>WA: send the authentication template with a 6-digit code
    WA-->>App: code on WhatsApp
    App->>API: POST citizen/account/mobile/verify/ {code}
    API-->>App: account (mobile verified); a waiting referral becomes PENDING
    Note over API: Hourly job: PENDING referrals past their hold date are CONFIRMED or REJECTED
```

Three things are new: a **citizen account** (a Django user with the new Citizen role), **mobile verification** by WhatsApp, and a **wallet** with **referrals**. Device posting tokens (`apps.citizens`, `X-Citizen-Token`) stay as they are: posting and service requests do not need the account (D2).

## 2. Data model

### 2.1 Extend `apps.citizens` (accounts)

`apps.citizens` already owns citizen identity (phones, posting tokens). The account lives next to it.

**CitizenAccount**

| Field | Type | Notes |
|---|---|---|
| `user` | OneToOne `accounts.User`, primary key | The Django user. `set_unusable_password()`: a citizen never logs in with a password. |
| `google_sub` | Char(255), unique, null | Google's stable account id (`sub`). Null after deletion. |
| `village_id` | PositiveInteger | Also the area of the Citizen role assignment. |
| `age_confirmed_at` | DateTime | 18+ confirmation (D5). |
| `consent_version`, `consent_at` | Char(20), DateTime | Which consent notice was agreed, and when. |
| `mobile_hash` | Char(64), unique, null | Keyed hash of the verified 10-digit number (same keying as `citizens.services._keyed_hash`). Not `User.mobile`: that column is unique across officials (`accounts/models.py:101-108`), so an official's number would clash. |
| `mobile_last4` | Char(4) | For display and for the Super Admin's search. |
| `mobile_verified_at` | DateTime, null | Points need this (D6). |
| `referral_code` | Char(8), unique | Generated, no look-alike characters (the alphabet of `accounts/credentials.py:32`). |
| `signup_android_id_hash` | Char(64), indexed | From the phone used at sign-up; one welcome bonus per phone. |
| `deleted_at` | DateTime, null | Set when the citizen deletes the account (section 3.8). |
| `deleted_google_hash`, `deleted_mobile_hash` | Char(64), indexed | Kept after deletion to stop repeat welcome bonuses (requirements Q9, pending legal review). |
| `created_at` | DateTime | |

**MobileCode** (one row per code sent; never holds the full number)

| Field | Notes |
|---|---|
| `account` | FK CitizenAccount |
| `mobile_hash`, `mobile_last4` | The number the code was sent to |
| `code_hash` | HMAC of the code with the row id and `SECRET_KEY`; the code itself is never stored or logged |
| `expires_at` | Sent + 10 minutes |
| `attempts` | Wrong tries, at most 5 |
| `verified_at` | Null until used |
| `provider_message_id` | WhatsApp message id, for support |
| `created_at` | Counts the daily limits |

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
| `status` | `WAITING` (code given, mobile not verified), `PENDING` (held), `CONFIRMED`, `REJECTED` |
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

### 3.3 Google sign-in

- `google-auth` is already installed (`requirements.txt`, used for FCM). Verify the token with `google.oauth2.id_token.verify_oauth2_token(token, google.auth.transport.requests.Request())`, then check `aud` against `GOOGLE_OAUTH_CLIENT_IDS` (the Web client id the app uses as `serverClientId`), `iss` (`accounts.google.com`) and expiry. Cache Google's keys (the library fetches them over HTTPS).
- Find the account by `sub`. If none, sign-up needs `age_confirmed: true`, `consent_version`, an active `village_id`, and the Wallet on setting.
- Create the user with a generated username that passes `username_validator` (never shown, never used to log in), `name` from the token, `preferred_language` from the request, an unusable password, the Citizen assignment, the account, its referral code and the wallet, in one transaction.
- Issue tokens with `accounts.tokens.issue_tokens(user)`, so the token version, blocking and the 90-day limit (`JWT_ABSOLUTE_DAYS`) work as for officials. Refresh and logout use the existing `auth/refresh/` and `auth/logout/`.
- The email from Google is not stored (requirements Q5).
- A blocked account (`is_active` false) answers 403 `account_blocked`, the existing code.

### 3.4 Mobile verification by WhatsApp

**Gateway.** Pluggable, like `apps/notifications/push.py`: setting `WHATSAPP_GATEWAY` names `apps.citizens.whatsapp.MetaCloudGateway` (production) or `LocmemWhatsAppGateway` (tests). Without `WHATSAPP_ACCESS_TOKEN` the gateway is off and `mobile/` answers 503 `whatsapp_unavailable`.

**Sending.** `POST https://graph.facebook.com/<version>/<WHATSAPP_PHONE_NUMBER_ID>/messages` with the approved authentication template (`WHATSAPP_OTP_TEMPLATE`), in the account's language (`te` or `en`), the code as the body parameter and the copy-code button parameter.
- The send happens **in the request**, with a 10-second timeout, not in a django-q job: the conventions forbid full mobile numbers in task arguments, and this way the number is never stored.
- Start with the **copy-code** button. One-tap autofill needs the package name `com.bvt.app`, each signing key's hash, and a native Android handshake in the app; add it later.

**Rules** (FR-10, FR-11)
- 6 digits from `secrets.randbelow`, valid 10 minutes, 5 wrong tries.
- A new code after 60 seconds (429 `code_cooldown` with `retry_after_s`), at most 5 codes a day per number and 10 per account (429 `code_limit`).
- Wrong tries go through a conditional UPDATE (`attempts < 5`), so parallel tries cannot exceed the limit on any database.
- On a correct code, setting `mobile_hash` hits the unique constraint if another account holds the number: 409 `mobile_in_use`, told only to the person who entered that number's code.

**Delivery.** WhatsApp may accept a message for a number without WhatsApp and report the failure later by webhook. **Check this during implementation.** The app always offers "Didn't get the code?" with the resend timer; a status webhook can come later.

**Audit.** `citizen.code_sent` (last 4 digits only) and `citizen.mobile_verified`. Never the code or the full number.

### 3.5 Referrals and the wallet

**Giving a code** (at sign-up or `POST citizen/account/referral/`, until the mobile is verified; FR-14)

The code is refused with:

| Error code | When |
|---|---|
| `referral_code_invalid` | No such code |
| `referral_code_own` | The referrer is the same person: same account, or same mobile hash or Android id hash on either account |
| `referral_not_open` | The referrer's mobile is not verified, or their account is blocked or deleted |
| `referral_window_closed` | The mobile is already verified, or a referral already exists |

Otherwise a Referral is created as WAITING.

**Mobile verified** (in the same transaction as the verification): a WAITING referral becomes PENDING with `confirm_after = now + REFERRAL_HOLD_DAYS`. Two HELD entries are created, with idempotency keys `ref:<referral id>:referrer` and `ref:<referral id>:welcome`:
- the referrer's reward;
- the referee's welcome bonus, unless an earlier account with the same `signup_android_id_hash`, or a deleted account with the same Google or mobile hash, already got one (the bonus is then skipped, with a note in the history).

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

A WAITING referral whose referee has not verified within 30 days is rejected (`mobile_not_verified`) by the same job.

**Super Admin**
- Confirm or reject a PENDING referral early, with a comment.
- Adjust a wallet with a reason: an ADJUSTMENT entry; a negative one uses `UPDATE … WHERE confirmed_points >= n`, else 409 `not_enough_points` (FR-23).

**Consistency.** A daily job compares each wallet's cache with the sum of its entries and audits `wallet.mismatch` if they differ. The entries are the truth.

### 3.6 Active days

`GET citizen/account/`, which the app calls when it opens, inserts today's CitizenActiveDay row if it is missing (`get_or_create` on the unique pair). That is one cheap write per account per day, and it gives the active rule (requirements section 7) without tracking anything else.

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

Env only (secrets and fixed values):
- `GOOGLE_OAUTH_CLIENT_IDS`
- `WHATSAPP_GATEWAY`, `WHATSAPP_ACCESS_TOKEN` (secret), `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_OTP_TEMPLATE`, `WHATSAPP_API_VERSION`
- `CITIZEN_CONSENT_VERSION`

`GET app-config/` adds `wallet_enabled`, so the app and the website hide the feature while it is off.

### 3.8 Account deletion

`POST citizen/account/delete/` (the citizen) and `POST admin/citizen-accounts/{id}/delete/` (the Super Admin, for requests that arrive through the website contact form with the mobile number). In one transaction:
- `is_active` false and `revoke_all_sessions`;
- the name replaced with "Deleted citizen";
- the username scrambled;
- push devices unlinked;
- `google_sub` and `mobile_hash` moved to the `deleted_*` hash columns (kept for 1 year if legal agrees, requirements Q9, then cleared by housekeeping);
- the role assignment deactivated;
- HELD entries cancelled.

Confirmed entries and referrals stay, with no personal details, for the audit trail. Audit `citizen.account_deleted`. This is the exception to "data is kept permanently" (requirements Q8, needs the owner's confirmation).

### 3.9 API (draft for `docs/api-contract.md`, new section 21)

All under `/api/v1/`. Citizen endpoints need a citizen JWT holding `wallet.use_wallet`. Every endpoint answers 404 `not_found` while `WALLET_ENABLED` is off.

| Method and path | Who | Request | Response |
|---|---|---|---|
| `POST citizen/account/google/` | public | `{id_token, village_id, age_confirmed, consent_version, language, referral_code?, android_id?}` | `{access, refresh, account}`; 201 on sign-up, 200 on sign-in |
| `GET citizen/account/` | citizen | — | `Account`: name, village, `mobile_verified`, `mobile_last4`, `referral_code`, `invite_url`, wallet summary, `referral` (own, if any). Records today's active day |
| `PATCH citizen/account/` | citizen | `{village_id?, language?}` | `Account` |
| `POST citizen/account/mobile/` | citizen | `{mobile}` | `{expires_in, resend_after}` |
| `POST citizen/account/mobile/verify/` | citizen | `{code}` | `Account` |
| `POST citizen/account/referral/` | citizen | `{code}` | `Account` |
| `POST citizen/account/delete/` | citizen | `{confirm: true}` | 204 |
| `GET wallet/` | citizen | — | `{confirmed_points, held_points, next_release_at, entries}`; entries are a cursor list (`id, points, kind, status, reason, created_at, decided_at`) |
| `GET admin/citizen-accounts/` | `accounts.manage_citizen_accounts` | `q` (name or last 4 digits), `village_id`, `status`, page | rows: name, village, `mobile_last4`, verified, balance, held, status, `created_at` |
| `POST admin/citizen-accounts/{id}/block/`, `/unblock/`, `/delete/` | `accounts.manage_citizen_accounts` | `{reason}` | row |
| `GET admin/referrals/` | `wallet.review_referrals` | `status`, `q`, page | rows with both people (name, last 4 digits), dates, reason, `allowed_actions` |
| `POST admin/referrals/{id}/confirm/`, `/reject/` | `wallet.review_referrals` | `{comment}` | row; 409 `already_decided` if the job got there first |
| `POST admin/wallets/{account_id}/adjust/` | `wallet.adjust_points` | `{points, reason}` | wallet; 409 `not_enough_points` |
| `GET admin/wallet/export/?type=referrals\|entries&format=csv` | `wallet.view_wallets` | filters | CSV with BOM, last 4 digits only |
| `GET`/`PATCH admin/settings/` | `posts.manage_settings` (existing) | adds the six runtime settings above | settings |
| `GET app-config/` | public | — | adds `wallet_enabled` |

New error codes for section 1 of the contract:
- `google_token_invalid`, `age_required`
- `whatsapp_unavailable`, `code_cooldown`, `code_limit`, `code_wrong` (with `attempts_left`), `code_expired`, `mobile_invalid`, `mobile_in_use`
- `referral_code_invalid`, `referral_code_own`, `referral_not_open`, `referral_window_closed`
- `not_enough_points`

### 3.10 Notifications

- New `NotificationKind.WALLET`.
- Templates (data migration, editable in the console like the others): `referral_joined` (to the referrer), `points_confirmed`, `points_not_confirmed`, in Telugu and English.
- The app registers its device with the citizen's JWT (`POST devices/` already links a device to the signed-in user, `apps/notifications/views.py`). Pushes go through `send_push_to_users`, so retries and audit work as today.
- The data carries only ids (`type`, `referral_id`), never names.

### 3.11 Audit actions

| Group | Actions |
|---|---|
| Accounts | `citizen.account_created`, `citizen.code_sent`, `citizen.mobile_verified`, `citizen.village_changed`, `citizen.account_blocked`, `citizen.account_unblocked`, `citizen.account_deleted` |
| Referrals | `referral.created`, `referral.pending`, `referral.confirmed`, `referral.rejected` |
| Wallet | `wallet.adjusted`, `wallet.mismatch` |
| Settings | `setting.changed` (existing) for the wallet settings |

Logins use the existing `auth.login` with `via="app"`. Audit `data` holds the last 4 digits of a mobile at most. Add every key to the console's and the app's audit labels.

### 3.12 Tests (all on SQLite, each new file in `grama/tests/suite.json`)

- **Permissions:** extend `apps/accounts/tests/test_matrix.py` with the Citizen group. Test that a citizen account gets 403 on every officials' endpoint (one test per endpoint) and never appears in `users/` for any official.
- **Auth:** valid and invalid Google tokens (wrong `aud`, expired, bad signature through a fake verifier); blocked account; 90-day limit; password change refused.
- **Codes:** expiry, attempts, cooldown, daily limits, `mobile_in_use`, and that no code or full number appears in the audit log or logs.
- **Referrals:** each reject reason; self-referral by mobile and by phone; one welcome bonus per phone; the monthly limit; two parallel confirmations (one wins); the idempotency key against double rewards.
- **Wallet:** the cache equals the sum of entries after every operation; a negative adjustment below zero is refused.
- **Deletion:** personal fields cleared, entries kept, re-sign-up gets no second welcome bonus.
- **Telugu:** every new message has its Telugu (`grama/tests/test_i18n.py`).

## 4. Mobile app (Flutter)

- **Package:** `google_sign_in`, pinned in `pubspec.yaml`. Its setup changed in recent major versions; check the current documentation when implementing.
- **Google project setup** (in the Firebase / Google Cloud project `bvt-grama-connect`):
  - An Android OAuth client for `com.bvt.app` with the SHA-1 of **every** signing key: debug, the key that signs the website's APK, and later the Google Play app signing key.
  - A Web OAuth client whose id is the app's `serverClientId` and the backend's `GOOGLE_OAUTH_CLIENT_IDS`.
- **One account at a time:** an official or a citizen account, never both. The existing `ApiClient` token handling (secure storage, refresh, `session_revoked`) is reused unchanged. Citizen mode without an account stays the default.
- **Screens** (Telugu and English, existing widgets and colours):
  - **Sign in to earn points:** consent notice with links to the privacy policy and terms, the 18+ tick, the referral code field (pre-filled from an invite link), the Google button.
  - **Verify your mobile:** the number, "Send code on WhatsApp", the code field, the resend timer, "Didn't get it?".
  - **Wallet:** confirmed balance, points on hold with the expected date, history with each status and reason, "Points have no cash value".
  - **Invite friends:** the code, Copy, and Share (WhatsApp first, through the existing `share_plus`).
  - **Profile:** Wallet, Invite, Sign out, Delete my account (with a confirmation).
- **Invite links:** add `android:pathPrefix="/app/"` next to the existing `/p/` App Link (`android/app/src/main/AndroidManifest.xml:44`). The server's `/.well-known/assetlinks.json` covers the domain, so no backend change is needed. The app stores `ref` until sign-up. Installs from the website get the code from the link or by typing it. Once the app is on Google Play, the Play Install Referrer can fill it too (it does not work for APKs installed outside Play).
- **On open:** `GET citizen/account/` (records the active day), and `POST devices/` with the citizen's JWT so pushes reach the account.
- **Feature switch:** everything hides while `app-config/` says `wallet_enabled: false`.
- **Tests:** flows against the local fake server (as `test/app_flow_test.dart`): sign-up, code, wallet, invite link pre-fill, sign out, delete.

## 5. Web (BVTGramaConnect)

**Console.** A new page, **Citizens & wallet**, for the Super Admin, shown through `NAV_PERM` with the new permissions:
- citizen accounts (search by name or last 4 digits, block, unblock, delete);
- referrals (status tabs, confirm, reject, reason);
- wallet adjustment with a reason;
- CSV export.

The Settings page gains the six wallet settings. The console is a port with "zero visual change" (`.claude/conventions.md` in the web repo), so a new page needs the owner's sign-off and must reuse existing styles (table, tabs, panels).

**Website**
- `/app/?ref=<code>` already redirects to the website's download section and keeps the query (`apps/share/views.py:138-144`). The website reads `ref` (8 characters from the code alphabet, anything else ignored) and shows "Your friend's referral code: **CODE**" with a **Copy** button in the download section (`src/website/sections/home/Download.tsx`).
- New texts go in `locales/te.json` and `en.json`.
- The privacy policy and terms (`legal` in the locale files) gain the account, the data, the 18+ rule, "points have no cash value", and how to delete the account.
- A short "Delete my account" section points to the in-app option, and to the contact form (with the mobile number) for people without the app.

## 6. Setup and operations

| Step | Who | Notes |
|---|---|---|
| Meta Business verification, WhatsApp Business number, display name | Client (C1) | Can take days to weeks; start first |
| Authentication template in Telugu and English, approved by Meta | Backend with the client (C2) | Copy-code button |
| WhatsApp system-user access token | Client and DevOps | A secret: production `.env` only, never in git |
| Google OAuth clients (Android SHA-1s, Web client) and consent screen | Mobile and DevOps (C3) | Needs access to the Firebase project (BVTGC-32) |
| Production `.env` | DevOps | `GOOGLE_OAUTH_CLIENT_IDS`, `WHATSAPP_*`, `CITIZEN_CONSENT_VERSION`; `WALLET_ENABLED` stays off |
| Per-IP limits on sign-up and codes | Backend | Depend on real visitor IPs (BVTGC-105, BVTGC-39) |

**Cost.** WhatsApp authentication messages for an Indian business to Indian numbers are reported at about ₹0.115 each (2026 figures from third-party guides; check Meta's rate card). One verification normally needs one message.

## 7. Rollout

1. Backend merged and deployed with `WALLET_ENABLED` off. Nothing visible changes.
2. App build with the new screens, hidden while the switch is off. Released through the website as usual.
3. On qa: switch on, run the UAT checklist (new section in `docs/uat.md`).
4. Production: switch on in the console.
5. Before switching on in production: the client's amounts (C4), the legal texts (C5) and the owner's answers to Q8 and Q9.

## 8. Documents to update with the code

- `docs/api-contract.md`: new section 21, the error codes, the screen map and the `app-config` field.
- `docs/authorization.md`: the Citizen group and the new permissions.
- `.claude/project.md` in all three repos: the roles table (Citizen: an optional account for the wallet), and owner decision entries D1–D7 of 2026-11-25. They include WhatsApp codes for citizens, an exception to the 2026-10-21 rule "nothing is sent by text message or email for login", which stays true for officials.
- `docs/security-review.md`: new data, the WhatsApp secret, fraud controls.
- `docs/uat.md`: a wallet and referrals section.

## 9. Work and effort

Rough estimates in working days for one developer each.

| Team | Work | Days |
|---|---|---|
| Backend | Models and migrations (2.1, 2.2) | 1.5 |
| Backend | Citizen role, permissions, the section 3.2 fixes, Google sign-in | 2.5 |
| Backend | WhatsApp gateway and code rules | 2 |
| Backend | Referrals, wallet, jobs, deletion | 3 |
| Backend | Admin endpoints, settings, CSV | 2 |
| Backend | Notifications, audit labels, contract and documents, Telugu | 1.5 |
| Backend | Tests (section 3.12) | 2.5 |
| **Backend total** | | **15** |
| Mobile | Google sign-in setup and the account state | 2 |
| Mobile | Five screens and the profile entries, Telugu and English | 4.5 |
| Mobile | Invite links, pushes, feature switch | 1.5 |
| Mobile | Tests | 2 |
| **Mobile total** | | **10** |
| Web | Console page and settings | 4 |
| Web | Website code display, legal texts, delete section | 1 |
| **Web total** | | **5** |
| QA | Test plan and UAT on qa | 5 |
| DevOps | Secrets, OAuth clients, template, `.env` | 1.5, plus Meta's review time |

With backend, app and web in parallel after the contract (section 3.9) is agreed: about **4 to 5 weeks**. Meta's verification and template approval are the likely critical path.

**Suggested Jira breakdown** (one epic, "Citizen wallet and referrals"):
- Backend: models and the Citizen role; the "role means official" fixes; Google sign-in; WhatsApp codes; referrals and wallet; admin API and settings; deletion; tests and contract.
- Mobile: sign-in and account; mobile verification; wallet and invite; invite links.
- Web: console page; website and legal texts.
- Client inputs: C1 to C5.

## 10. Risks and open technical questions

| # | Risk or question | Mitigation or default |
|---|---|---|
| R1 | Fake referrals with several Google accounts and spare WhatsApp numbers (Play Integrity is off) | Holding period, active-days rule, one welcome bonus per phone, monthly limit, Super Admin review; require Play Integrity at sign-up once on Google Play |
| R2 | WhatsApp may report a non-WhatsApp number only later (webhook) | Resend timer and "Didn't get it?"; add a status webhook if support needs it. Check during implementation. |
| R3 | Google sign-in fails on the website's APK if its signing key's SHA-1 is not registered | Register every signing key before testing; keep the list in `.claude/operations.md` of the mobile repo |
| R4 | Per-IP limits on sign-up and codes depend on real visitor IPs | BVTGC-105 and the production team's confirmation (BVTGC-39) |
| R5 | Account deletion conflicts with "data is kept permanently" | Owner's decision (requirements Q8) before production |
| R6 | Officials' code paths assume "any role = official" (section 3.2) | Fix with the new permission; tests per endpoint |
| R7 | iPhone: no Android id, so the one-bonus-per-phone rule is weaker | iPhone is not in Phase 1; revisit with App Attest (BVTGC-27) |
| Q-T1 | Store the hashes of deleted accounts for 1 year? | Pending legal (requirements Q9) |
| Q-T2 | One-tap code autofill on Android | After launch; copy-code first |
| Q-T3 | Should a citizen account also post (instead of the device token) later? | Out of scope (D2); the account and the device stay separate |
| R8 | Points gain a rupee value when Bills & Recharges go live (D4) | Keep `WalletEntry` append-only and idempotent from day one; the bills design adds a SPENT kind and refunds ([../bills-recharges/technical-implementation.md](../bills-recharges/technical-implementation.md), section 3.6) |

## References

- Verify a Google ID token on the server: https://developers.google.com/identity/sign-in/web/backend-auth
- WhatsApp authentication templates: https://developers.facebook.com/docs/whatsapp/business-management-api/authentication-templates
- WhatsApp Business Platform pricing: https://developers.facebook.com/docs/whatsapp/pricing
- Google Play Install Referrer: https://developer.android.com/google/play/installreferrer
- Firebase Dynamic Links (shut down in 2025, so not an option for invite links): https://firebase.google.com/support/dynamic-links-faq
- Google Play account deletion requirements: https://support.google.com/googleplay/android-developer/answer/13327111
