# Citizen wallet: test plan

| | |
|---|---|
| Jira | BVTGC-145 (this plan and the UAT checklist), BVTGC-146 (running UAT), epic BVTGC-106 |
| Written by | Admin (with AI assistance), 2026-11-25 |
| Reviewed by | Anvesh Kolli (QA) |
| Checklist | `docs/uat.md`, sections "Citizen wallet" (CW1 to CW42) and X5 |
| Requirements | [requirements.md](requirements.md), section 13 (acceptance criteria) |
| Design | [technical-implementation.md](technical-implementation.md), section 11 |

## 1. What is tested

- **In scope:** the optional citizen account (sign-up with an email code, login by email or mobile number, forgotten password, profile changes, log out, deletion in the app and on the website); referrals and the wallet (points on hold, confirmed or not, family phones, limits); the wallet pushes and emails; the Super Admin's tools (accounts, call-back help, block, delete, referrals, adjustments, wallet entries, CSV, settings, push texts); the switch and the pilot mandals; Telugu and English everywhere; officials not seeing citizens.
- **Not in scope:** WhatsApp and SMS codes (later stage, BVTGC-118); Bills & Recharges; Google Play (the app is released through the website); load tests beyond the existing ones.
- **Unchanged and re-checked briefly:** citizens without an account, citizen posts and service requests, and officials' logins (CW1, CW9, CW40).

## 2. Where

| Environment | What | Who |
|---|---|---|
| Laptop | Each developer's own checks against a local backend with `WALLET_ENABLED=True`; emails print in the terminal | Developers |
| qa server | UAT, with real email (BVTGC-143) and push; the wallet switched on in the console for one pilot mandal; holding period 1 day and active days 2 for the referral checks | Testing team (BVTGC-146) |
| Production | Smoke tests only, after the release, for the pilot mandals (technical-implementation.md, section 13) | Admin |

## 3. Test data

- **Officials and areas:** `python manage.py seed_sample_data --allow-server` on qa (as in `docs/uat.md`, "Before you start").
- **Citizen accounts:** made through the app, never by script, so the sign-up, the email and the code are tested each time. One account per tester's email; three on one shared phone for the family checks.
- **Inboxes:** at least two Gmail addresses and one other provider, owned by the team.
- **Phones:** two budget Android phones (2 GB RAM), one with two SIM cards; mobile data and 3G.
- **Clean-up:** each test account is deleted at the end (in the app, on the website, or by the Super Admin), which also tests deletion. Deleted accounts keep a fingerprint for a year (Q9), so a deleted test number gets no welcome points again on qa (CW20): use fresh numbers for the referral checks.

## 4. Automated tests

They run on every pull request (backend: GitHub Actions on SQLite, MariaDB 11.4 and PostgreSQL 17; mobile: `flutter test` on Codemagic) and must pass before UAT starts.

| Area | Where | What they cover |
|---|---|---|
| Accounts and models | BackEnd `apps/citizens/tests/test_account_models.py`, `apps/wallet/tests/test_models.py` | Unique email, number and referral code, freed by deletion; one referral per new citizen, none to oneself; entries never 0 points, only adjustments negative, idempotency keys; wallet caches that never go below zero |
| Email and codes | `apps/citizens/tests/test_email_codes.py` | The emails in Telugu and English per purpose; codes kept only as a keyed hash; 10 minutes, 5 tries, one purpose each; 60 seconds between codes; 5 a day per account or address, 10 per phone; 503 when the service is down; no code or address in logs or audit; the server warning while email is not set up |
| Sign-up | `apps/citizens/tests/test_signup.py` | The form's checks, the password rule, the pilot, 409 for a registered number or email, Gmail variants, two sign-ups racing for one number |
| Login and forgotten password | `apps/citizens/tests/test_citizen_login.py` | Email and mobile login, the shared lockout, the same answer for unknown logins, blocked and deleted accounts, reset ending other sessions |
| Own account | `apps/citizens/tests/test_citizen_account.py` | Village, language, password, email with its code, mobile |
| Referrals and wallet | `apps/wallet/tests/test_referrals.py` | The family rules, the hold, active days, the monthly limit, the hourly job, the daily check, the wallet's pages |
| Deletion and housekeeping | `apps/citizens/tests/test_account_deletion.py`, `grama/tests/test_housekeeping.py` | App and website deletion (a reset code refused), what is removed and what stays, the referrals ending on both sides, the housekeeping steps |
| Super Admin | `apps/citizens/tests/test_admin_accounts.py`, `apps/wallet/tests/test_admin_wallet.py` | Every endpoint for every official group, a citizen and an anonymous caller; call-back rule; adjustments never below zero; the CSV |
| Settings, app-config, pushes | `apps/wallet/tests/test_wallet_settings.py` | Ranges, audit, the off switch on every citizen wallet route, the three pushes |
| Permissions | `apps/accounts/tests/test_matrix.py`, `apps/accounts/tests/test_citizen_role.py`, `grama/tests/test_citizen_token_scope.py` | Every group × every permission (the Citizen group included); a citizen's token refused on every officials' route |
| Privacy | `apps/citizens/tests/test_wallet_privacy.py`, `grama/tests/test_log_leak.py` | No email, number, password, code or token in the log, the audit log or the job queue, through the whole wallet |
| Mobile app | Mobile `test/wallet_session_test.dart` and the wallet flow tests (BVTGC-138) | The citizen session (sign in, restore, refused, offline, one account at a time) and the screens' flows against a fake server |
| Console and website | Web repo, the wallet tests of BVTGC-139 to 142 | The console's page and actions, the website's code box and deletion page |

The full backend suite runs with `python -m pytest -q -p no:logging`, or by category with `python -m grama.testsuite all` (a report in `test-reports/`).

## 5. Manual tests (UAT)

`docs/uat.md`, "Citizen wallet", role by role:
- the citizen in the app (CW1 to CW25): without an account, sign-up, login, forgotten password, profile changes, invite, referrals confirmed and not confirmed, family phones, deletion;
- the website (CW26 to CW28): deletion with a code, the friend's code, the legal texts;
- the Super Admin (CW29 to CW39): accounts, the call-back help, block and delete, referrals, adjustments, entries and CSV, settings, push texts, the audit log;
- other officials (CW40);
- emails and pushes (CW41, CW42), and data privacy (X5).

What only people can judge, and what UAT must look at closely:
- the Telugu wording on every screen, email and push;
- whether the codes land in the inbox rather than spam, per provider;
- the 3-minute sign-up on a budget phone (acceptance criterion);
- the call-back procedure the support person follows.

## 6. Start and finish

- **UAT starts when:** the wallet tickets are merged into `dev` and deployed to qa; email works on qa (BVTGC-143); the automated tests are green; the app build with the wallet screens is installed on the test phones.
- **UAT is done when:** every CW line passes, or each failure has a Jira ticket the lead accepts for later (BVTGC-146).
- **Defects:** one Jira bug per failure in project BVTGC, under the epic BVTGC-106, with the CW number, the phone, the language, the steps and a screenshot; never a full email, number or code in the ticket (write the last 4 digits).

## 7. Risks to watch

- Codes in spam: test every provider the testers have; the release waits for production email (BVTGC-143).
- The phone rules trust the app's Android ID: UAT checks the family limit on a real shared phone, not an emulator.
- Time-based rules (hold, active days, 60-second wait, 15-minute lockout, 10-minute codes): note the clock time of each step, so a failure can be told from waiting too little.
