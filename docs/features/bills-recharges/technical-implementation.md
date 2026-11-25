# Bills and recharges: technical implementation

| | |
|---|---|
| Status | Draft design, 2026-11-25. Nothing is built yet. |
| Requirements | [requirements.md](requirements.md) (decisions D1–D13, rules in section 7) |
| Builds on | [Citizen wallet and referrals](../citizen-wallet/technical-implementation.md): CitizenAccount, the Citizen role, Wallet and WalletEntry. The wallet goes live first (D13). |
| Code read for this design | BackEnd `dev` `81de633`, Web `dev` `241cf1b`, Mobile `dev` `b3b7eff` (build 25) |
| Owners | Backend (Basam Bhargavi), Mobile and web (Mohit), QA (Anvesh Kolli) |

The design follows the backend's own rules (`.claude/conventions.md`): roles and permissions only in `apps/accounts/role_matrix.py`, every check through `HasPerm` and `authz`, portable ORM, audit only through `record()`, every user-facing message in `_()` with its Telugu, and the contract first. Money adds four more rules:
- Amounts are whole **paise** in integer fields, never floats or decimals.
- A payment changes state only through one conditional UPDATE.
- Every call to the partner that moves money carries our own unique reference.
- The partner's word is final: the app's "paid" is never trusted.

The partner is not chosen yet (D5: PayU or Setu). Everything partner-specific sits behind one gateway interface (section 3.2), and only the chosen partner's adapter is built.

## 1. Overview

```mermaid
sequenceDiagram
    participant App as Mobile app
    participant API as Backend
    participant P as Partner (COU + checkout)
    participant N as Bharat Connect
    App->>API: POST billpay/fetch/ {biller_id, inputs}
    API->>P: fetch bill
    P->>N: fetch
    N-->>P: amount, due date, name
    P-->>API: bill + fetch reference
    API-->>App: BillFetch
    App->>API: POST billpay/payments/ {fetch_id, amount_paise, points, client_ref}
    Note over API: one transaction: take the points (wallet), create the payment
    API->>P: create a checkout for the amount left
    API-->>App: payment + checkout parameters
    App->>P: partner checkout (UPI app, card, net banking)
    P-->>API: signed notification: collected
    API->>P: confirm the collection (status call)
    API->>P: pay the bill, full amount, our reference
    P->>N: payment
    N-->>P: success, pending or failed + Bharat Connect reference
    P-->>API: result (reply or notification)
    API-->>App: push; GET billpay/payments/{id}/ shows the receipt
    Note over API: failed after collection: the partner refunds the money, the backend returns the points
```

Prepaid recharge follows the same payment flow. Its first steps are an operator lookup and a plan instead of a bill fetch (section 3.8).

## 2. Data model

### 2.1 `apps.bills`: categories and the Home tiles

**New model BillCategory.** One row for each Bharat Connect category the partner lists, plus one row `prepaid_recharge`. This is where the Super Admin's switches live (D12).

| Field | Notes |
|---|---|
| `key` | Slug, unique. For Bharat Connect, the partner's category code normalised (`electricity`, `dth`, `credit_card` …); `prepaid_recharge` for recharges. |
| `source` | `BBPS` or `RECHARGE` |
| `name_en`, `name_te` | Filled from the partner. The Super Admin can edit the Telugu. |
| `is_active` | Default **False**: a new category stays hidden until the Super Admin turns it on (FR-4a) |
| `points_allowed` | Default **False** (D12) |
| `order` | For **View all** |
| `partner_listed_at` | Last daily sync that saw it. A category the partner drops is shown as "not offered by the partner" and cannot be used, whatever its switch says. |
| `created_at`, `updated_at` | |

**Bill (the Home tile)** gains `category` (FK BillCategory, null). A tile without a category, or with an inactive one, keeps today's "coming soon" behaviour (FR-4). The five seeded tiles are linked in a data migration once the partner's codes are known:
- `electricity`, `water_bill`, `dth_recharge` and `broadband` go to their Bharat Connect categories;
- `mobile_recharge` goes to `prepaid_recharge`.

Contract section 18 gains an optional `category` on `Bill`. Older apps ignore it.

Permission: the existing `bills.manage_bills` (Super Admin) covers the tiles. The category switches use `billpay.manage_billpay` (section 3.1).

### 2.2 New app `apps.billpay`

Create it as `.claude/conventions.md` says (`startapp billpay apps/billpay`, label `billpay`, add to `LOCAL_APPS`).

**Biller** (copied from the partner every day): `partner_id` (unique), `category` (FK BillCategory), `name`, `coverage` (state, or `IND`), `fetch_rule` (`MANDATORY`, `OPTIONAL`, `NOT_SUPPORTED`), `amount_rule` (`EXACT`, `EXACT_AND_ABOVE`, `EXACT_AND_BELOW`, `ANY`), `inputs` (JSON: name, label, regex, min and max length, optional), `payment_modes` (JSON), `is_active`, `synced_at`.

**SavedBiller**: `account` (FK CitizenAccount), `biller` (FK Biller), `label`, `inputs` (JSON), `created_at`, `last_paid_at`. Unique on `(account, biller, inputs_hash)`.

**BillFetch** (short-lived): `id` (UUID), `account`, `biller`, `inputs` (JSON), `inputs_hash`, `amount_paise`, `due_date`, `bill_date`, `customer_name`, `partner_ref`, `expires_at` (the partner's validity, at most a few hours), `created_at`. Kept with its payment.

**BillPayment**

| Field | Notes |
|---|---|
| `id` | UUID. Shown to the citizen as a short reference. |
| `account` | FK CitizenAccount |
| `kind` | `BILL` or `RECHARGE` |
| `category` | FK BillCategory (the switches are checked against it) |
| `biller`, `fetch` | FK, null for recharges |
| `recharge_number`, `recharge_last4`, `operator`, `circle`, `plan_id`, `plan_text` | Recharges only. The full number is stored for the recharge and shown only to its owner; logs, audit, jobs and admin lists use the last 4 digits (FR-13). |
| `bill_paise` | Full amount paid to the biller or operator. BigInteger, > 0. |
| `points_used`, `point_value_paise`, `discount_paise` | A snapshot when the payment is created (FR-28). `discount_paise = points_used × point_value_paise`, never more than `bill_paise`. |
| `cash_paise` | `bill_paise − discount_paise`. CheckConstraint keeps the sum exact. |
| `client_ref` | UUID from the app. Unique with `account`, so a retried create returns the same payment (FR-9). |
| `status` | Section 3.4 |
| `checkout_ref`, `collection_ref`, `payment_mode` | From the partner's checkout |
| `partner_txn_ref`, `bbps_ref` | From the bill payment. `bbps_ref` is the Bharat Connect reference on the receipt. |
| `failure_code`, `failure_text` | The partner's, kept as they are |
| `refund_status`, `refund_ref` | For a collected payment that failed |
| `next_check_at`, `checks` | For the status job |
| `created_at`, `collected_at`, `submitted_at`, `completed_at` | |

Indexes: `(status, next_check_at)` for the job; `(account, created_at)` for history; `(created_at, status)` for the console and reconciliation.

**PaymentEvent** (append-only): `payment`, `from_status`, `to_status`, `source` (`app`, `notification`, `status_check`, `admin`, `reconciliation`), `note`, `created_at`. This is the step-by-step history the Super Admin sees (requirements, section 10).

**BillComplaint**: `payment` (FK), `partner_complaint_id` (unique), `reason` (Bharat Connect disposition code), `description`, `status`, `assigned_to` (the partner's text), `resolution`, `last_checked_at`, `created_at`, `closed_at`.

**PartnerEvent** (the inbox for notifications): `event_id` (unique, the partner's id, or a hash of the body when there is none), `kind`, `received_at`, `signature_ok`, `payment` (FK null), `body` (JSON, with card and UPI fields removed), `processed_at`.

**ReconDay** and **ReconItem**: one row per day (counts, totals, `status`), and one item per difference (`payment` null, partner reference, our amount and the partner's, `kind`: `missing_ours`, `missing_partner`, `amount_differs`, `status_differs`; `resolved_by`, `resolved_at`, `note`).

### 2.3 `apps.wallet`: spending points

Added as new migrations in `apps.wallet`, which by then is live (D13). WalletEntry gains:
- kinds `BILL_DISCOUNT` (negative, CONFIRMED at once) and `BILL_DISCOUNT_RETURNED` (positive, CONFIRMED);
- `bill_payment` (FK BillPayment, null).

Idempotency keys `pay:<payment id>:spend` and `pay:<payment id>:return`: the existing unique `(wallet, idempotency_key)` makes "never returned twice" (FR-26) a database guarantee. The "points have no cash value" texts stay true.

## 3. Backend design

### 3.1 Access and permissions

Add to `apps/accounts/role_matrix.py`:

| Permission | Held by |
|---|---|
| `billpay.pay_bills` | Citizen (next to `wallet.use_wallet`) |
| `billpay.view_payments` | Super Admin (`PLATFORM_ADMIN`): payments, events, reconciliation, CSV |
| `billpay.handle_complaints` | Super Admin |
| `billpay.manage_billpay` | Super Admin: category switches, points settings, pilot accounts, recheck a payment, resolve a reconciliation item |

Every citizen endpoint uses `HasPerm("billpay.pay_bills")`, and a service check refuses with `403 mobile_not_verified` until the account's mobile is verified (FR-1). While the pilot list is not empty, accounts outside it get `404 not_found`, so the feature stays invisible to them. Officials hold none of these permissions. A test in the `test_matrix.py` style locks that in.

### 3.2 The partner gateway

Setting `BILLPAY_GATEWAY` names the class, as `PUSH_GATEWAY` does (`apps/notifications/push.py`, `import_string`):
- `apps.billpay.gateways.payu.PayUGateway` or `apps.billpay.gateways.setu.SetuGateway`: only the chosen one is built;
- `apps.billpay.gateways.fake.FakeGateway`: tests, laptops and qa demos. It covers success, pending, failure, refund and complaint scenarios driven by test consumer numbers.

Interface (`apps/billpay/gateways/base.py`):

| Method | Use |
|---|---|
| `categories()`, `billers(since)` | Daily sync (3.3) |
| `fetch_bill(biller, inputs, account)` | Fetch (FR-6) |
| `create_checkout(payment)` | Collect `cash_paise`; returns what the Flutter kit needs |
| `collection_status(payment)` | Confirm a collection before paying the bill |
| `pay_bill(payment)`, `payment_status(payment)` | Bharat Connect payment, our `id` as the reference |
| `recharge_lookup(number)`, `recharge_plans(operator, circle)`, `recharge(payment)`, `recharge_status(payment)` | Prepaid (3.8) |
| `raise_complaint(payment, reason, text)`, `complaint_status(complaint)` | Complaints (3.9) |
| `recon_report(day)` | Reconciliation (3.10) |
| `verify_notification(headers, body)` | Signature check (3.7) |

**Calls**
- Calls use `requests` with a 10-second connect timeout and a 30-second read timeout.
- Reads are retried through `grama.retry.backoff_delays`. Calls that move money are never retried blindly: after a timeout, the job asks `payment_status` with the same reference.
- Secrets come from the env only.
- The partner usually accepts calls only from listed IP addresses (requirements C6).

### 3.3 Catalogue sync

`apps.billpay.tasks.sync_catalogue` runs daily at 03:00 India time, registered by `ensure_schedules`.
- It upserts BillCategory and Biller rows. New categories arrive inactive with points off.
- Billers the partner no longer lists become inactive. Rows are never deleted.
- It audits `billpay.catalogue_synced` with the counts.

The app reads billers from our database, never from the partner directly: the lists are small and cached, which suits 2G.

### 3.4 Payment states

| Status | Meaning | Next |
|---|---|---|
| `CREATED` | Points taken, checkout being made | `AWAITING_PAYMENT`; `SUBMITTED` when `cash_paise` is 0; `EXPIRED` |
| `AWAITING_PAYMENT` | Checkout open in the app | `COLLECTED`, `EXPIRED` (30 minutes, `BILLPAY_CHECKOUT_MINUTES`), `FAILED` |
| `COLLECTED` | The partner confirmed the money | `SUBMITTED` |
| `SUBMITTED` | Sent to Bharat Connect or the operator | `SUCCESS`, `PENDING`, `FAILED` |
| `PENDING` | The network has not answered yet | `SUCCESS`, `FAILED` |
| `SUCCESS` | Final | (a complaint may follow) |
| `FAILED` | Final for the bill. If money was collected, `refund_status` follows it to `REFUNDED`. | |
| `EXPIRED` | Final, nothing collected | |

**Rules**
- Every move is `BillPayment.objects.filter(id=…, status=<from>).update(status=<to>, …)`. When no row is updated, someone else moved it first, and that is fine.
- Each move writes a PaymentEvent and an audit entry in the same transaction. Pushes and points returns follow after commit.
- Only the backend submits a bill, and only from `COLLECTED`, or from `CREATED` when nothing is to be collected. A second notification for the same collection finds the status already moved and does nothing.
- `FAILED`, `EXPIRED` and `REFUNDED` return the points (section 3.6).

### 3.5 How the money moves (requirements Q1, Q10)

The gateway hides which model the partner uses:
- **Direct:** the checkout collects `cash_paise` into the partner's escrow, and `pay_bill` sends `bill_paise`. The partner must accept that the client pays the difference (`discount_paise`), from a prefunded balance or by invoice.
- **Prefunded balance:** the client keeps a balance with the partner. `pay_bill` debits `bill_paise`. The citizen's `cash_paise` reaches the client through the partner's checkout and tops the balance back up.

Either way, `bill_paise = cash_paise + discount_paise`, and the client carries the discount (D9). A payment fully covered by points (`cash_paise = 0`, only when the share setting is 100%) skips the checkout. It works only when the partner allows the client to pay the whole bill, so the settings screen warns when 100% is chosen and the partner does not allow it (gateway flag `supports_full_discount`).

### 3.6 Points as a discount

On `POST billpay/payments/`, in one transaction:
1. Check that bills (or recharges) and points are on, and that the category is active with points allowed. Read the point value, share and monthly limit through `runtime()`.
2. Work out the most points allowed:
   `min(confirmed_points, floor(bill_paise × share / 100 / point_value), monthly_limit − used_this_month, floor(bill_paise / point_value))`.
   The month is the calendar month in India time, and `used_this_month` is BILL_DISCOUNT minus BILL_DISCOUNT_RETURNED entries in it. Asking for more answers `400 points_not_allowed` with `max_points`.
3. Take the points with `Wallet.objects.filter(pk=…, confirmed_points__gte=n).update(confirmed_points=F(...) - n)`. When no row is updated: `409 not_enough_points`. Then add the BILL_DISCOUNT entry with key `pay:<id>:spend`.
4. Create the BillPayment with the snapshot (`points_used`, `point_value_paise`, `discount_paise`, `cash_paise`).

**Returning points.** On `FAILED`, `EXPIRED` or a refund, add a BILL_DISCOUNT_RETURNED entry with key `pay:<id>:return` and `confirmed_points = F(...) + n` in one transaction. The unique key makes a second return impossible. A push says so (FR-26).

**Points never touch the partner.** The partner sees only `cash_paise` at checkout and `bill_paise` on the bill.

**Cost report (FR-29):** `discount_paise` summed per day and per category from BillPayment rows with status `SUCCESS`.

The wallet's daily consistency job already compares the cache with the entries, so it covers the new kinds without changes.

### 3.7 Partner notifications and status checks

**Notifications.** `POST billpay/notifications/<partner>/` is public and has no session.
- The signature is checked by `verify_notification`. A bad signature answers 401 and is audited without the body.
- A PartnerEvent is inserted. Its unique `event_id` drops repeats.
- The endpoint answers 200 at once, and a django-q job processes the event by id.
- Processing never trusts the notification's amount or status alone. It asks `collection_status` or `payment_status`, then moves the payment.

**Jobs** (registered by `ensure_schedules`; arguments are ids only, never numbers):

| Job | When | What |
|---|---|---|
| `check_payments` | Every 2 minutes | Payments in `AWAITING_PAYMENT`, `COLLECTED`, `SUBMITTED` or `PENDING` whose `next_check_at` has passed. Backoff 2, 5, 15, 60 minutes, then hourly for 3 days, then the payment is flagged for the Super Admin. Expires open checkouts. |
| `check_complaints` | Hourly | Open complaints |
| `reconcile` | Daily, 06:00 | Yesterday (3.10) |
| `sync_catalogue` | Daily, 03:00 | Section 3.3 |

`GET billpay/payments/{id}/` also queues one check when the payment is unfinished and its last check is more than a minute old. A citizen watching the screen sees the result quickly, without the app polling the partner.

### 3.8 Prepaid recharge

- `GET billpay/recharge/lookup/?number=` would put a mobile number in a URL, which the conventions forbid. Use `POST billpay/recharge/lookup/ {number}` instead. It returns the operator and circle (FR-11).
- `GET billpay/recharge/plans/?operator=&circle=`: the partner's plans, cached 6 hours per operator and circle in the database cache, grouped by the partner's plan types.
- `POST billpay/payments/` with `kind: RECHARGE`, `{number, operator, circle, plan_id, amount_paise}`. The server checks the plan and amount against the cached plans and refreshes once if the plan is missing. A custom amount is accepted only where the operator allows it (Jio: plans only).
- The payment then follows section 3.4, with `recharge` and `recharge_status` in place of `pay_bill` and `payment_status`.

### 3.9 Complaints

- `POST billpay/payments/{id}/complaint/ {reason, description}` is allowed on `SUCCESS`, `PENDING` and `FAILED` payments. One open complaint per payment: `409 complaint_open`.
- The reasons are the Bharat Connect disposition list, served by `GET billpay/complaint-reasons/` in Telugu and English.
- `check_complaints` updates the status, and each change sends a push.
- The Super Admin sees complaints with the network's deadlines and can add internal notes. The partner and the network resolve them.

### 3.10 Reconciliation

`reconcile` fetches the partner's report for the day and matches it on our `id` and the partner reference:
- amounts and statuses that agree are matched;
- every difference becomes a ReconItem.

Items in `missing_partner` or `status_differs` trigger a status check first. The Super Admin resolves each remaining item with a note (`billpay.manage_billpay`), and it is audited. A day with open items shows a badge on the console dashboard.

### 3.11 Settings

Runtime settings, overridable from the console (`apps/posts/services.py` `RUNTIME_SETTINGS`, SettingOverride), with env defaults in `grama/settings.py`:

| Setting | Default |
|---|---|
| `BILLPAY_ENABLED` | False |
| `RECHARGE_ENABLED` | False |
| `BILLPAY_MAX_PAISE` | Set with the client (Q4) |
| `BILLPAY_PAYMENTS_PER_DAY` | 10 |
| `BILLPAY_PILOT_ACCOUNTS` | Empty (comma-separated account ids) |
| `BILLPAY_POINTS_ENABLED` | False |
| `POINT_VALUE_PAISE` | 0, so points are off until the client's value is set (C7) |
| `POINTS_MAX_SHARE_PERCENT` | 10 (0–100) |
| `POINTS_MONTHLY_LIMIT` | 0 (no limit) until the client sets it (C7) |
| `BILLPAY_CHECKOUT_MINUTES` | 30 |

The category switches live on BillCategory, not in settings.

Env only:
- `BILLPAY_GATEWAY`;
- the partner's keys and notification secret (secrets);
- the agent id, base URLs and timeouts.

`GET app-config/` adds `billpay_enabled`, `recharge_enabled` and `billpay_points_enabled`, each false for accounts outside the pilot list.

### 3.12 API (draft for `docs/api-contract.md`, new section 22)

All under `/api/v1/`. Citizen endpoints need a citizen JWT with `billpay.pay_bills` and a verified mobile. They answer 404 while the matching switch is off.

| Method and path | Who | Request | Response |
|---|---|---|---|
| `GET billpay/categories/` | citizen | — | Active categories in order: `key`, names, `source`, `points_allowed` |
| `GET billpay/billers/` | citizen | `category`, `q`, `state`, cursor | Billers: `id`, `name`, `coverage`, `inputs`, `fetch_rule`, `amount_rule` |
| `POST billpay/fetch/` | citizen | `{biller_id, inputs}` | `BillFetch`; 400 `fields.<input>`, 404 `no_bill_due`, 503 `billpay_unavailable` |
| `GET billpay/points-quote/` | citizen | `category`, `amount_paise` | `{max_points, point_value_paise, max_discount_paise}`; nothing personal goes in the URL |
| `POST billpay/payments/` | citizen | `{client_ref, kind, fetch_id \| biller_id+inputs, amount_paise, points, save_biller?}` or the recharge fields | `201 {payment, checkout}`; `200` with the same payment for a repeated `client_ref` |
| `GET billpay/payments/` | citizen | cursor | The account's payments, newest first |
| `GET billpay/payments/{id}/` | citizen | — | `Payment` with `receipt` once final; the account's own only, otherwise 404 |
| `GET`, `POST`, `PATCH`, `DELETE billpay/saved-billers/` | citizen | `{biller_id, inputs, label}` | Saved billers |
| `POST billpay/recharge/lookup/` | citizen | `{number}` | `{operator, circle}`; 404 `operator_unknown` |
| `GET billpay/recharge/plans/` | citizen | `operator`, `circle` | Plans grouped by type |
| `GET billpay/complaint-reasons/` | citizen | — | Reasons |
| `POST billpay/payments/{id}/complaint/` | citizen | `{reason, description}` | `Complaint`; 409 `complaint_open` |
| `GET billpay/complaints/` | citizen | cursor | The account's complaints |
| `POST billpay/notifications/{partner}/` | partner | signed body | 200 |
| `GET admin/billpay/payments/` | `billpay.view_payments` | `status`, `category`, dates, `last4`, page | Rows, masked |
| `GET admin/billpay/payments/{id}/` | `billpay.view_payments` | — | Payment with events and complaint |
| `POST admin/billpay/payments/{id}/recheck/` | `billpay.manage_billpay` | — | Payment after a status call |
| `GET admin/billpay/complaints/` | `billpay.handle_complaints` | `status`, dates, page | Rows |
| `POST admin/billpay/complaints/{id}/note/` | `billpay.handle_complaints` | `{note}` | Complaint |
| `GET admin/billpay/reconciliation/` | `billpay.view_payments` | `date` | Day and items |
| `POST admin/billpay/reconciliation/items/{id}/resolve/` | `billpay.manage_billpay` | `{note}` | Item |
| `GET`, `PATCH admin/billpay/categories/` | `billpay.manage_billpay` | `{key, is_active?, points_allowed?, name_te?, order?}` | Every category, with `partner_listed` |
| `GET admin/billpay/discounts/` | `billpay.view_payments` | `from`, `to`, `format=csv?` | Discounts per day and category (FR-29) |
| `GET admin/billpay/export/` | `billpay.view_payments` | `type=payments\|complaints`, filters | CSV with BOM, last 4 digits only |
| `GET`, `PATCH admin/settings/` | `posts.manage_settings` (existing) | Adds the settings of 3.11 | Settings |

New error codes for section 1 of the contract:
- `billpay_unavailable`, `mobile_not_verified`, `category_inactive`, `biller_inactive`, `no_bill_due`, `fetch_expired`
- `amount_not_allowed`, `amount_limit`, `daily_limit`
- `points_off`, `points_not_allowed` (with `max_points`), `not_enough_points` (already in the wallet section)
- `operator_unknown`, `plan_unavailable`, `complaint_open`

### 3.13 Notifications

- New `NotificationKind.PAYMENT`.
- Templates (a data migration, editable in the console like the others), in Telugu and English: `payment_success`, `payment_failed`, `payment_refunded`, `points_returned`, `complaint_updated`.
- The data carries only ids (`type`, `payment_id`). Lock-screen text names the category and never the consumer or mobile number (FR-20).

### 3.14 Audit actions

| Action | Data |
|---|---|
| `billpay.payment_created`, `billpay.payment_status` | Id, kind, category, amounts in paise, points, from and to status. Never the consumer or full mobile number. |
| `billpay.points_spent`, `billpay.points_returned` | Payment id, points |
| `billpay.complaint_raised`, `billpay.complaint_updated` | Ids, reason |
| `billpay.category_changed` | Key, `is_active` and `points_allowed`, old and new |
| `billpay.recheck`, `billpay.recon_resolved` | Ids, note |
| `billpay.catalogue_synced`, `billpay.recon_day` | Counts |
| `billpay.notification_rejected` | Partner, reason; no body |

Settings changes are already audited by the settings screen.

### 3.15 Privacy

- Consumer inputs and recharge numbers are stored for the payment and shown only to their owner. The console, CSV exports, audit, logs and job arguments show the last 4 characters.
- Partner notification bodies are stored with card and UPI fields removed.
- Payment data stays on the production server in India (requirements Q5).
- Account deletion (wallet design, section 3.8) removes saved billers and replaces the account's name. It keeps payments with consumer inputs replaced by their last 4 characters, for the retention period legal sets (Q6). This is the second exception to "data is kept permanently" and needs the owner's confirmation together with the wallet's.

### 3.16 Tests (all on SQLite with the FakeGateway, each new file in `grama/tests/suite.json`)

- **Permissions:** every group × the new permissions (`test_matrix.py` style); a citizen cannot read another account's payment (404); officials get 403.
- **States:** every allowed move, every refused move, two notifications at once, a notification after the job got there first, a timeout followed by a status check. None of them pays twice.
- **Points:** each limit on its own (balance, share, monthly limit, amount, category switch, master switch); returns on failed, expired and refunded payments, with a repeated return refused; a value or limit change does not alter a payment already started; the 100% path with and without `supports_full_discount`.
- **Money arithmetic:** paise only, `bill = cash + discount` on every path, odd amounts and rounding down of points.
- **Recharge:** lookup, plans cache, plan-only operators, custom amounts.
- **Complaints and reconciliation:** each difference kind, and resolution.
- **Privacy:** no full numbers in audit, logs or CSV (a test that scans them); notifications without a valid signature are refused.
- **Telugu:** every new message has its Telugu (`test_i18n.py`).

## 4. Mobile app (Flutter)

- **Entry points:**
  - the Bills row on Home (`citizen_home.dart`) and **View all**: `cBills`, `ServicesScreen(bills: true)` (`shell.dart:53`, `services_screen.dart:146-206`);
  - a tile whose `category` is set and active opens the flow; anything else keeps "Coming soon" (`svcSoon`), so with the switches off the app looks exactly as today.
- **Sign-in gate:** tapping a tile without an account opens the wallet's sign-in screen, and an unverified mobile opens the verify screen (wallet design, section 4).
- **Screens**, in Telugu and English with existing widgets and colours, plus the Bharat Connect logo where NBBL's brand rules require it:
  - **categories**;
  - **biller list** with search and the citizen's state first;
  - **details form**, built from the biller's `inputs`;
  - **bill**, showing amount, due date and name;
  - **pay**, with the "Use points" slider and the amount left;
  - **result and receipt**;
  - **history**;
  - **saved billers**;
  - **complaint**, with its status;
  - **recharge**: number, operator and circle, plan tabs, then pay.
- **Checkout:**
  - the chosen partner's Flutter checkout kit, pinned in `pubspec.yaml`, or a UPI app hand-off where the partner offers it;
  - after the kit returns, the app always asks the backend (`GET billpay/payments/{id}/`) and shows that answer, never the kit's own (FR-8);
  - the app makes `client_ref` once per Pay tap and reuses it on retry.
- **Cache:** categories and billers are cached for a day, which suits 2G. Plans are fetched when the screen opens.
- **Feature switches:** from `app-config/` (`billpay_enabled`, `recharge_enabled`, `billpay_points_enabled`).
- **Super Admin in the app:** the existing bills management screen (`super_admin_screens.dart`, `sBills`) gains the tile's category link. The payments, complaints and reconciliation pages live in the web console only.
- **Tests:** flows against the local fake server (as in `test/app_flow_test.dart`):
  - fetch, pay with and without points, pending then success, failure with points returned;
  - recharge with a plan;
  - a complaint.

## 5. Web (BVTGramaConnect)

**Console.** New pages for the Super Admin, shown through `NAV_PERM` with the new permissions:
- **Bill payments:** list, detail with events, recheck;
- **Complaints:** list, detail, notes;
- **Reconciliation:** day view, resolve an item;
- **Bill categories:** the two switches per category, Telugu name, order; categories the partner dropped are marked;
- **Points discounts:** per day and category, with CSV.

The Settings page gains the settings of section 3.11. The bills tile editor (`src/console/screens/Services.tsx`) gains the category link.

The console is a port with "zero visual change" (`.claude/conventions.md` in the web repo), so new pages need the owner's sign-off and must reuse the existing table, tabs and panel styles.

**Website.** The privacy policy and terms (`legal` in the locale files) gain:
- payments through the partner, as its Bharat Connect agent;
- refunds and complaints;
- the grievance officer;
- points as a discount with no cash value, whose value and limits the client may change.

The texts are in `locales/te.json` and `en.json`.

## 6. Setup and operations

| Step | Who | Notes |
|---|---|---|
| Terms from PayU and Setu, choice, agreement | Client (C2) | Start now. It runs during the wallet build (D13). |
| Agent Institution KYC, field visit, NBBL certification | Client with the partner (C1) | Usually the critical path |
| Partner test-system credentials, notification URL, IP listing | DevOps (C6) | qa first, then production |
| NBBL certification test cases on qa | QA with the partner | Screens, receipts and complaints are checked |
| Lawyer's confirmation of the points model | Client (C8) | Before points are switched on |
| Point value, monthly limit, budget | Client (C7) | Entered by the Super Admin |
| Production `.env` | DevOps | Partner secrets; every switch stays off |
| Prefunded balance and its top-ups, if the partner needs them | Client (C4) | A low balance stops payments. The console shows the partner's balance when the gateway offers it. |

## 7. Rollout

1. The wallet is live in production (D13).
2. Backend merged and deployed with every switch off. Nothing visible changes.
3. An app build with the new screens, hidden while the switches are off.
4. qa against the partner's test system: UAT, then NBBL certification.
5. Production with pilot accounts (Q8): bills first, then recharges, then points.
6. Production for everyone. The Super Admin turns on categories one by one.

## 8. Documents to update with the code

- `docs/api-contract.md`:
  - new section 22;
  - `category` on `Bill` in section 18 and the end of "coming soon";
  - the error codes, the screen map and the `app-config` fields.
- `docs/authorization.md`: the new permissions.
- `.claude/project.md` in all three repos: decisions D1–D13 of 2026-11-25, and the second exception to "data is kept permanently".
- `.claude/architecture.md`: the `billpay` app, the new jobs, and the `bills` row ("no payment behind a bill yet" ends).
- `docs/security-review.md`: money flows, partner secrets, notification signatures, the points model.
- `docs/uat.md`: a bills and recharges section.

## 9. Work and effort

Rough estimates in working days for one developer each.

| Team | Work | Days |
|---|---|---|
| Backend | Categories, tile link, models and migrations (2.1–2.3) | 2.5 |
| Backend | Gateway interface, FakeGateway, the chosen partner's adapter | 4 |
| Backend | Fetch, payments, states, notifications, status job | 4 |
| Backend | Points as a discount (3.6) | 2 |
| Backend | Prepaid recharge | 2.5 |
| Backend | Complaints and reconciliation | 3 |
| Backend | Admin endpoints, settings, CSV | 2.5 |
| Backend | Pushes, audit labels, contract and documents, Telugu | 1.5 |
| Backend | Tests (3.16) | 4 |
| **Backend total** | | **26** |
| Mobile | Categories, billers, details form, fetch | 4 |
| Mobile | Checkout kit, result, receipt | 2.5 |
| Mobile | Points on the pay screen | 1 |
| Mobile | Recharge: lookup, plans, pay | 3 |
| Mobile | History, saved billers, complaints, switches | 3 |
| Mobile | Tests | 2.5 |
| **Mobile total** | | **16** |
| Web | Console pages and settings | 5 |
| Web | Tile category link, legal texts | 1 |
| **Web total** | | **6** |
| QA | Test plan, UAT, NBBL certification cases | 10 |
| DevOps | Partner credentials, IP listing, `.env`, notification URL | 2, plus the partner's onboarding time |

With backend, app and web in parallel after the contract (section 3.12) is agreed, the build takes about **6 to 7 weeks**. It starts after the wallet is live (D13). The partner's onboarding and NBBL certification are likely the critical path, so the client should start them now.

**Suggested Jira breakdown**, for when the requirements are final (one epic, "Bills and recharges"):
- **Backend:** categories and models; gateway and adapter; payments and states; points discount; recharge; complaints and reconciliation; admin API and settings; tests and contract.
- **Mobile:** bill flow; checkout and receipts; recharge; history, saved billers and complaints.
- **Web:** console pages; legal texts.
- **Client inputs:** C1 to C8.

## 10. Risks and open technical questions

| # | Risk or question | Mitigation or default |
|---|---|---|
| R1 | Partner onboarding and NBBL certification take longer than the build | Start the paperwork now, while the wallet is built (D13) |
| R2 | Payments stuck pending, or a notification lost | Status job with backoff, the status call on every read, daily reconciliation, flag after 3 days |
| R3 | The production host has no fixed outbound IP for the partner's allow-list | Confirm with the production team (C6); a small fixed-IP proxy if needed |
| R4 | Points in the wallet-only period gain value (wallet design R8) | Review referrals before switching points on; share and monthly limits; points off by default |
| R5 | The partner does not support a client-funded part of a payment | Prefunded balance (3.5); the 100% path only with `supports_full_discount` |
| R6 | The lawyer says the points model needs a licence | Points stay collect-only; bills go live without points (`BILLPAY_POINTS_ENABLED` off) |
| R7 | Fraud with stolen UPI or cards | Partner's risk checks, daily limits, largest amount, account blocking |
| R8 | Payment data outside India | Confirm the server and backups are in India (Q5) |
| R9 | New console pages vs "zero visual change" | Owner's sign-off; reuse existing styles |
| R10 | Bharat Connect rules change (guidelines V6, November 2025) | Keep the UI texts and receipt fields in one place; recheck at certification |
| Q-T1 | Partner's Flutter kit vs UPI app hand-off | Decide after D5, from the partner's documentation |
| Q-T2 | Show the partner's prefunded balance in the console? | Yes if the gateway offers it, with a low-balance alert |

## References

- NBBL Procedural Guidelines V6 (November 2025): https://www.bharat-connect.com/strapi/uploads/Procedural_Guidelines_BBPS_V6_November_2025_Website_b15d06fcb5.pdf
- RBI Bharat Bill Payment System Directions 2024: https://www.rbi.org.in/scripts/BS_ViewMasDirections.aspx?id=12616
- RBI Master Directions on Prepaid Payment Instruments: https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=11142
- PayU Connect Agent API: https://docs.payu.in/docs/connect-agent-api-integration ; PayU recharge APIs: https://docs.payu.in/docs/apis-for-recharge-integration
- Setu Bharat Connect COU: https://setu.co/real-time-payments/bbps/cou ; Setu prepaid recharge: https://docs.setu.co/payments/billpay/mobile-prepaid-recharge
- Cashfree Bharat Connect COU API (balance and ledger endpoints): https://www.cashfree.com/docs/api-reference/other-apis/bbps-cou/overview
- Google Play payments policy: https://support.google.com/googleplay/android-developer/answer/10281818
