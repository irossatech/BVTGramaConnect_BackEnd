# Bills and recharges: requirements

| | |
|---|---|
| Status | Draft for the client's review |
| Date | 2026-11-25 |
| Decisions by | Admin (project lead), 2026-11-25 |
| Technical design | [technical-implementation.md](technical-implementation.md) |
| Depends on | Citizen accounts and the wallet from [Citizen wallet and referrals](../citizen-wallet/requirements.md) (BVTGC-106), which go live first (D13) |
| Applies to | Mobile app, backend API, Super Admin console, public website |

## 1. Summary

Citizens pay their bills and recharge their prepaid mobiles inside the app, as in Paytm or PhonePe. Today the **Bills & Recharges** row on Home only says "coming soon" (`apps/bills`, contract section 18).

Bills are paid through **Bharat Connect** (BBPS, the national bill payment network run by NBBL, part of NPCI). The client joins it as an **Agent Institution** through a licensed partner, the **Customer Operating Unit (COU)**. The partner holds the money, pays the billers and handles disputes with the network. The app fetches the bill, takes the payment through the partner's checkout and shows a Bharat Connect receipt.

Prepaid mobile recharges for Jio, Airtel and Vi are not available on Bharat Connect today, so they go through a **recharge API** from the same partner.

Paying needs a **citizen account with a verified mobile number** (the account planned in BVTGC-106). Reading news, posting and service requests stay without login.

Citizens can use their **wallet points** as a discount on any bill or recharge whose category accepts points. The client pays the discounted part from its own money, so points never pay a biller and never become cash. The Super Admin controls everything about points at any time: what a point is worth, how much of a payment points may cover, and which categories accept them.

**The wallet goes live first**, and Bills & Recharges follow.

## 2. Decisions taken (2026-11-25)

| # | Question | Decision |
|---|---|---|
| D1 | How bills are paid | In the app, as a Bharat Connect Agent Institution through a licensed partner (COU). The client never runs its own bill payment system. |
| D2 | Which bills in the first version | Every Bharat Connect category the chosen partner offers live (electricity, water, gas, LPG, DTH, broadband, landline, postpaid mobile, FASTag, insurance, loan repayment, credit card, municipal taxes and so on). |
| D3 | Prepaid mobile recharge (Jio, Airtel, Vi) | In the first version, through a recharge API. |
| D4 | Who can pay | Only a signed-in citizen whose mobile number is verified. This extends wallet decision D2, which said only the wallet and referrals need a login. |
| D5 | Which partner | The client asks **PayU** and **Setu** for terms. Both offer Bharat Connect and a prepaid recharge API, so one partner covers both. The client picks one. |
| D6 | Earning wallet points by paying | None in the first version. |
| D7 | Fee to the citizen | None. The citizen pays exactly the bill or plan amount. The client earns only the partner's commission. |
| D8 | How this phase is tracked | These two documents and a requirements-phase epic in Jira, with no tickets until the requirements are final. |
| D9 | Spending wallet points | In the first version, as a **discount the client pays for**. The citizen pays less at checkout, and the client pays the rest of the bill from its own money through the partner. Points never pay the biller, never become cash and cannot be transferred. This changes wallet decision D4. |
| D10 | What a point is worth | A Super Admin setting. The client sets it before go-live. |
| D11 | How much of a payment points may cover | Fully controlled by the Super Admin, who can change it at any time: the share of each payment (up to the whole payment) and a monthly limit per account. |
| D12 | Which bills accept points | Controlled by the Super Admin, category by category, at any time. Each category, and prepaid recharge, has its own switch for active or inactive, and its own switch for points on or off. |
| D13 | Order of release | The wallet goes live first. Bills & Recharges come after it. Points cannot be spent until then. |

## 3. Scope

**In this version**
- Bill payment for every live Bharat Connect category the partner offers: find the biller, enter the consumer details, fetch the bill, pay, get a receipt.
- Confirmed wallet points as a discount, within the Super Admin's limits (D9 to D11).
- The Super Admin's per-category switches: active or inactive, and points on or off (D12).
- Prepaid mobile recharge for Jio, Airtel, Vi and BSNL: the operator and circle are detected from the number, then the citizen picks a plan.
- Payment by UPI (the citizen's UPI app), debit card, credit card and net banking, as the partner's checkout allows.
- Saved billers and numbers, payment history, receipts.
- Complaints about a payment, tracked until they are closed, as Bharat Connect requires.
- Automatic refund of money taken for a payment that failed.
- Push notifications about the payment result, refunds and complaints.
- Super Admin tools: payments, complaints, the daily reconciliation, settings, CSV export.
- Website: privacy policy and terms updated, grievance contact published.

**Not in this version**
- Earning points by paying (D6) and any fee to the citizen (D7).
- A stored money balance, or points that pay a biller directly. Both would need an RBI prepaid instrument licence. Points work only as the client's discount (D9).
- AutoPay and standing instructions.
- Bill due-date reminders.
- Officials paying bills from their official accounts.
- iPhone. The design works on iPhone, but the first release is Android only, as for the wallet.
- Buying insurance, loans or new connections. Only paying existing bills or premiums.

## 4. Who uses it

| Who | What changes |
|---|---|
| Citizen without an account | Sees the Bills & Recharges row. Tapping a tile asks them to sign in with their mobile number and a code. |
| Citizen with an account | Every account has a verified mobile number from sign-up (wallet D1, D6). Pays bills and recharges, sees history, saved billers and receipts, raises complaints. |
| Super Admin | Sees payments and complaints, follows the daily reconciliation, changes the settings, exports CSV. Adds or hides Home tiles as today, and links each tile to a category. Switches each category active or inactive and points on or off. Sets the point value and the points limits, and sees what the points discounts cost the client. |
| Officials | Nothing changes. |
| Client's support person | Answers citizens' payment questions using the Super Admin console. Who this is, is open question Q2. |

## 5. User journeys

### J1. Pay a bill
1. The citizen taps a tile on Home (for example **Electricity**) or **View all**, then a category.
2. The app lists that category's billers. Billers for the citizen's state come first, and there is a search box.
3. The citizen enters the details the biller asks for (for example the consumer number). The app checks the format as they type.
4. The app fetches the bill and shows the name on the account, the amount, the due date and the bill date.
5. The citizen confirms the amount. Some billers allow a different amount, and the app says so.
6. If the category accepts points and the citizen has confirmed points, the app offers **Use points**. It shows the most points allowed and their rupee value, and the citizen can use fewer. The amount left to pay is shown.
7. The partner's checkout opens for the amount left. The citizen pays with a UPI app, a card or net banking. When points cover the whole amount, no checkout opens.
8. The app shows **Success**, **Pending** or **Failed**, then the receipt. The receipt shows the Bharat Connect logo, the Bharat Connect reference, the bill amount, the points discount and the amount paid.
9. The app offers **Save this biller** for next time.

### J2. Recharge a prepaid mobile
1. The citizen taps **Mobile Recharge**. Their own verified number is filled in, or they type or pick another one.
2. The app shows the detected operator and circle, which the citizen can change.
3. Plans appear in tabs (popular, data, unlimited, top-up and so on). Jio takes plan amounts only.
4. The citizen picks a plan, uses points if recharges accept them, and pays as in J1.
5. The app shows the result and a receipt with the operator's reference.

### J3. Payment pending or failed
- **Pending:** the app says when to expect the result. The backend keeps checking, and a push arrives when the result is known.
- **Failed after the money was taken:** the app says that a refund is on its way and when it should arrive. A push arrives when it is refunded.
- **Points:** points used on a payment that fails, expires or is refunded go back to the wallet, and the history says why.

### J4. History and saved billers
- **History:** every payment and recharge, newest first, with its status. Each one opens its receipt.
- **Saved billers:** each saved biller or number has a **Pay** button that fetches the latest bill.

### J5. Raise a complaint
1. From a receipt, the citizen taps **Raise a complaint** and picks a reason from the Bharat Connect list (for example "Amount deducted, biller account not credited").
2. The app shows the complaint number and its status, and a push arrives when the status changes.

### J6. Super Admin
- **Payments:** search by date, status, category, or the last 4 digits of the mobile number. Open a payment, see its history and ask the partner for its status again.
- **Complaints:** see them with their deadlines.
- **Reconciliation:** see each day's result: matched, missing on our side, missing on the partner's side, amounts that differ.
- **Categories:** every category from the partner, plus prepaid recharge, each with two switches: **Active** and **Points accepted**. Link each Home tile to a category.
- **Points:** the point value, the share of a payment points may cover, and the monthly limit per account. Each day's discounts given, by category, which is the client's cost.
- **Settings:** switch bills or recharges on or off and change the payment limits.
- **Export:** CSV of payments and complaints.

## 6. Functional requirements

### Access
- **FR-1** Paying needs a signed-in citizen account whose mobile number is verified. Anyone else who taps a tile is asked to sign in first. Since wallet v3, signing in proves the number, so every account qualifies.
- **FR-2** The whole feature hides when it is switched off. Bills and recharges switch separately. While switched off, the tiles show "coming soon" as today.

### Catalogue
- **FR-3** The list of billers comes from the partner and is refreshed every day. Each biller has the details to ask for (name, format, required or optional), whether a bill can be fetched, and which amounts are allowed (exact, more, less or any).
- **FR-4** Each Home tile is linked to one category, or to prepaid recharge. A tile with no link, or linked to an inactive category, keeps showing "coming soon". **View all** lists every active category.
- **FR-4a** The Super Admin switches each category, and prepaid recharge, active or inactive at any time. A new category from the partner arrives **inactive** until the Super Admin turns it on.
- **FR-5** Billers covering the citizen's state are listed first. Search works on the biller's name.

### Bill payment
- **FR-6** The app fetches the bill when the biller supports it. When fetching is not supported, the citizen enters the amount within the biller's rules.
- **FR-7** The backend checks the amount against the biller's rules and the limits (section 7) before the checkout opens.
- **FR-8** A payment is marked successful only after the backend has confirmed it with the partner. The app's own "paid" message is never trusted.
- **FR-9** One tap pays once. Retries, double taps and repeated messages from the partner never charge the citizen twice or pay the bill twice.
- **FR-10** The receipt shows everything Bharat Connect requires: the Bharat Connect logo, the reference, biller, consumer details, amount, payment mode, date and time, and status.

### Prepaid recharge
- **FR-11** The operator and circle are detected from the number and can be changed.
- **FR-12** Plans come from the partner, grouped by type. Only the partner's plans can be bought. A custom amount is offered only where the operator allows it.
- **FR-13** The number being recharged is shown in full only to the citizen who paid. Everywhere else, only its last 4 digits are shown.

### After the payment
- **FR-14** A pending payment is checked again automatically until it succeeds or fails. The citizen gets a push either way.
- **FR-15** Money taken for a failed payment is refunded to the same source by the partner. The app shows the refund status.
- **FR-16** Complaints are raised through the partner's Bharat Connect complaint service, with the reasons Bharat Connect allows. Their status is shown in the app until they are closed.
- **FR-17** History keeps every payment of the account. Saved billers can be renamed and removed.

### Super Admin console
- **FR-18** The pages from J6, for the Super Admin only. They show consumer numbers and mobile numbers masked, and never card or UPI details. Our servers never receive those.
- **FR-19** A daily reconciliation compares our payments with the partner's report and lists every difference for action.

### Wallet points (D9 to D12)
- **FR-23** Only confirmed points can be used. Held points cannot.
- **FR-24** The citizen may use up to the smallest of: their confirmed points, the share of the payment the Super Admin allows, what is left of their monthly limit, and the payment amount itself. The app shows the discount in rupees before the checkout.
- **FR-25** Points are taken from the wallet when the payment is created. The checkout asks only for the amount left. When the Super Admin allows 100% and the points cover everything, no checkout opens: the client's money pays the whole bill.
- **FR-26** Points come back to the wallet automatically when the payment fails, expires or is refunded, each time with a history entry that says why. They are never returned twice.
- **FR-27** Points are accepted only in categories (and for prepaid recharge) whose points switch is on when the payment starts.
- **FR-28** A change to the point value, the limits or a category's switches is audited, and applies only to payments started after it. A payment already started keeps its discount.
- **FR-29** The Super Admin sees the discounts given per day and per category, and can export them as CSV for the client's accounts.

### Notifications, website, language
- **FR-20** Pushes cover payment success, failure, refund and complaint updates. They carry no amounts or numbers on the lock screen beyond what the citizen chose.
- **FR-21** The website's privacy policy and terms cover payments, the partner, refunds and complaints, and publish the grievance contact.
- **FR-22** Every new screen and message is in Telugu and English. Biller and plan names come from the partner as they are, usually in English.

## 7. Rules and settings

Changeable by the Super Admin, except where marked.

| Setting | Default | Notes |
|---|---|---|
| Bill payments on | Off | Switched on after Bharat Connect certification |
| Recharges on | Off | Switched on separately |
| Largest single payment | Set with the client (Q4) | Partner and biller limits also apply |
| Payments per account per day | 10 | Limits abuse with stolen payment methods |
| Pilot accounts | Empty (everyone) | When filled, only these accounts can pay. Used for the first days in production. |
| Points on bills and recharges | Off | The master switch for D9 |
| Value of one point | Set by the client (C7) | In paise. Applies when points are spent, not when they are earned (Q11). |
| Share of a payment points may cover | 10% to start | 0 to 100%; 100% lets points pay the whole amount |
| Points a citizen may spend in a month | Set by the client (C7) | 0 means no monthly limit |
| Per category: active, points accepted | New categories inactive, points off | Changed at any time in the console (D12) |
| Partner keys, webhook secret, agent id | Server only | Secrets live in the production `.env`, never in the console or git |

## 8. Fraud and abuse

- **No money is stored.** Every rupee goes from the citizen's bank or card to the partner, and our servers never hold or forward money.
- **Our servers never see card numbers, UPI PINs or bank logins.** The partner's checkout handles them.
- Daily limits per account and a largest single amount.
- The Super Admin can block a citizen account (wallet feature), and a blocked account cannot pay.
- Refunds go only to the source of the payment, never to another account.
- **Points now have a rupee value.** Fake referrals during the wallet-only period would turn into real discounts once bills go live. Only confirmed points can be spent, the share and monthly limits cap the loss, and the Super Admin should review referrals before switching points on.
- The partner runs its own risk checks. A payment it declines shows as failed.

## 9. Privacy and legal

- **Personal data:** consumer numbers, the name on the bill, the mobile numbers recharged, amounts and payment history are personal data under the DPDP Act 2023 and Rules 2025. The consent notice and privacy policy must cover them, and so must the account deletion from the wallet feature.
- **Keeping records:** payment records must be kept for the period the partner's agreement and RBI rules require, even after an account is deleted, with the personal details removed where allowed. Legal to confirm (Q6).
- **Data in India:** RBI requires payment data to be stored only in India. The production server and its backups must be in India (Q5).
- **Bharat Connect rules:** the Agent Institution must follow NBBL's procedural guidelines, the brand rules on screens and receipts, and the complaint deadlines.
- **Google Play:** bill payments must not use Google Play billing; they use the partner's checkout. When the app goes on Google Play, the Financial features declaration is needed. Today the app is installed from the website.
- **Who the citizen deals with:** the terms must name the client as the Bharat Connect agent of the partner, the refund rules and the grievance officer.
- **Points:** points are a discount the client gives on its own service, with no cash value and no transfer. Points that paid a biller directly would be a prepaid payment instrument needing an RBI licence, so a lawyer must confirm the discount model before go-live (C8). The terms must say that the client may change the point value and limits at any time. The client's accountant should confirm how the discounts are booked and taxed.

## 10. Non-functional requirements

- **Correctness of money:** amounts are whole paise, never decimals. Every payment has one reference that makes retries safe. Every status change is recorded.
- **Speed:** a bill fetch shows a result or a clear error within 15 seconds. The partner's checkout opens within 3 seconds of tapping Pay.
- **Partner outages:** when the partner is down, tiles show "temporarily unavailable" instead of an error, and nothing is lost.
- **Security:** messages from the partner are accepted only with a valid signature. Secrets are never in git, logs or the console.
- **Logs and audit:** never full mobile numbers, consumer numbers, card or UPI details (project rule).
- **Supportability:** a Super Admin can see every step of a payment without reading server logs.

## 11. What the client needs to provide

| # | Item |
|---|---|
| C1 | The legal entity that becomes the Agent Institution and its KYC documents: PAN, TAN, GST registration, a cancelled cheque, a board resolution and the consent form. The partner also visits the client's premises. |
| C2 | Terms from PayU and Setu (D5): onboarding time, commission per category, how the money moves (section 12, Q1), checkout options and Flutter support, monthly or set-up fees. Then the signed agreement with the chosen one. |
| C3 | A grievance officer and a support contact (name, phone, email) to publish. |
| C4 | The bank account where commissions are paid, and money for a prefunded balance if the partner needs one (Q1). |
| C5 | Review of the updated privacy policy and terms. |
| C6 | The production server's fixed outbound IP address. Partners usually accept calls only from listed addresses. |
| C7 | The value of one point, the monthly limit per citizen, and a monthly budget for points discounts |
| C8 | A lawyer's confirmation that points as the client's discount need no RBI licence, and the points wording in the terms |

## 12. Open questions for the client

| # | Question |
|---|---|
| Q1 | How does the money move with the chosen partner? Either the citizen pays straight into the partner's Bharat Connect escrow account, or the client keeps a prefunded balance with the partner that each payment draws on. If it is a balance, how much, and who tops it up? |
| Q2 | Who answers citizens' payment questions and complaints day to day? |
| Q3 | Are all states' billers shown, or only Andhra Pradesh and Telangana first? |
| Q4 | What is the largest single payment allowed? |
| Q5 | Is the production server, with its backups, located in India? |
| Q6 | How long must payment records be kept, and what may be removed when a citizen deletes their account? |
| Q7 | Credit card bill payments are a Bharat Connect category and are included under D2. Does the client want them, given the higher fraud risk? |
| Q8 | Should the first production days be limited to pilot accounts (staff and volunteers)? |
| Q9 | Do points expire, now that they have a value? Default: no expiry. |
| Q10 | Does the chosen partner accept a payment where the client pays part of the bill (a discount it funds), or only through the prefunded balance? This decides how the money moves (Q1). |
| Q11 | When the point value changes, does it also apply to points already earned? Default: yes, the value at the time of spending applies, and the terms say so. |

## 13. Acceptance criteria

- A verified citizen pays an electricity bill on qa against the partner's test system and sees a receipt with the Bharat Connect reference and logo.
- A double tap, a lost connection during checkout, and repeated partner messages each charge and pay once only.
- A payment that the partner reports as failed after the money was taken shows "refund on its way" and then "refunded".
- A pending payment becomes successful or failed without the citizen doing anything, and the citizen gets a push.
- A Jio, an Airtel and a Vi number each get the right operator, circle and plans, and a recharge succeeds on the test system.
- A complaint raised in the app appears at the partner with the right reason and comes back with its status.
- A citizen without a verified mobile cannot reach the checkout.
- The day's reconciliation on qa shows zero differences after a test run, and shows a deliberately broken payment as a difference.
- Logs, audit entries and CSV exports contain no full mobile or consumer numbers.
- With points on for a category, a citizen uses points within each limit (share, monthly limit, balance), pays the rest, and the receipt and wallet history agree. A failed payment returns the points exactly once.
- A category switched inactive disappears from the app at once. With its points switch off, "Use points" is not offered for it.
- NBBL's certification checks with the partner pass.
- With the switches off, the app looks exactly as today.

## 14. Phasing

| Phase | What | Notes |
|---|---|---|
| 0 | Client gets terms, chooses the partner, signs, completes KYC and the visit | Paperwork, in parallel with phases 1 and 2. Usually the longest part. |
| 1 | The citizen wallet goes live (BVTGC-106): accounts, mobile verification, collect-only points | A prerequisite (D13). Paying needs the account. |
| 2 | Bill payments and points as a discount on the partner's test system, then NBBL certification | Backend, app and console |
| 3 | Prepaid recharges on the partner's test system | Same partner, same payment flow |
| 4 | Production with pilot accounts, then everyone | Switches in the console |
| Later | Due-date reminders, AutoPay, earning points by paying, iPhone | Each needs a new decision |

## Sources

- NBBL (Bharat Connect) Procedural Guidelines V6, November 2025: https://www.bharat-connect.com/strapi/uploads/Procedural_Guidelines_BBPS_V6_November_2025_Website_b15d06fcb5.pdf
- RBI Master Direction, Bharat Bill Payment System Directions 2024: https://www.rbi.org.in/scripts/BS_ViewMasDirections.aspx?id=12616
- RBI, prepaid mobile recharge added as a biller category (2021): https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=12113&Mode=0
- NPCI, BSNL first operator in prepaid recharges on Bharat BillPay (2021): https://www.npci.org.in/PDF/npci/press-releases/2021/NPCI-Press-Release-Bharat-BillPay-onboards-BSNL-as-first-telecom-operator-in-mobile-prepaid-recharges-category.pdf
- Setu, categories in Bharat Connect (prepaid not covered): https://support.setu.co/support/solutions/articles/81000419866-what-are-the-different-categories-in-bbps-
- Setu Bharat Connect (COU): https://setu.co/real-time-payments/bbps/cou ; Setu prepaid recharge API: https://docs.setu.co/payments/billpay/mobile-prepaid-recharge
- PayU Connect Agent API: https://docs.payu.in/docs/connect-agent-api-integration ; PayU recharge APIs: https://docs.payu.in/docs/apis-for-recharge-integration
- WhatsApp prepaid recharges through PayU (April 2026): https://about.fb.com/news/2026/04/bringing-prepaid-mobile-recharges-to-whatsapp-users-in-india/
- Cashfree Bharat Connect COU API (for comparison): https://www.cashfree.com/docs/api-reference/other-apis/bbps-cou/overview
- Google Play payments policy: https://support.google.com/googleplay/android-developer/answer/10281818
- Google Pay India, Jio plan-only recharges: https://support.google.com/pay/india/answer/9043724
- RBI Master Directions on Prepaid Payment Instruments (points that pay third parties): https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=11142
