# Citizen wallet and referrals: requirements

| | |
|---|---|
| Status | Draft for the client's review |
| Date | 2026-11-25 |
| Decisions by | Admin (project lead), 2026-11-25 |
| Technical design | [technical-implementation.md](technical-implementation.md) |
| Applies to | Mobile app, backend API, Super Admin console, public website |

## 1. Summary

Citizens get an optional account with a **wallet** of points. A citizen who invites a friend earns points when the friend joins, and the friend gets a welcome bonus. Points are only collected in this first version: they cannot be spent, transferred or turned into money.

Today citizens use the app without any account. Reading news, posting and service requests stay exactly as they are. The new account is needed only for the wallet and referrals.

Citizens sign in with their **Google account** and prove their mobile number with a **code sent on WhatsApp**. Each account gets a new **Citizen** role, which works like the officials' roles (a Django group with permissions, tied to the citizen's village).

## 2. Decisions taken (2026-11-25)

| # | Question | Decision |
|---|---|---|
| D1 | How citizens sign up and log in | Google account sign-in. The mobile number is verified with a one-time code sent on WhatsApp. |
| D2 | What needs a login | Only the wallet and referrals. Reading news, posting and service requests stay without login. |
| D3 | What earns points | The referrer, when a friend signs up; a welcome bonus for the new citizen who used a referral code. Both are **held** until the new citizen has been active for a while. |
| D4 | What points can be used for | Nothing yet: collect only. What points buy is decided later. |
| D5 | Who may have an account | 18 and over only. Sign-up asks the citizen to confirm their age. |
| D6 | A citizen without WhatsApp on that number | Can sign in and see the wallet, but earns no points until the mobile number is verified. |
| D7 | How citizens get the Citizen role | Automatically at sign-up, for the village chosen in the app. |

Officials keep their username and password login. Nothing changes for them.

## 3. Scope

**In this version**
- Citizen sign-up and login with Google, 18+ confirmation and a consent notice.
- Mobile number verification by a WhatsApp code.
- The Citizen role (Django group), given automatically.
- A personal referral code and invite link for each citizen.
- Referral points and a welcome bonus, held and then confirmed or not confirmed.
- A wallet screen: balance, points on hold, history.
- Push notifications about points.
- Super Admin tools: reward settings, referral review, point corrections, citizen account blocking, CSV export.
- Website: the invite link shows the friend's referral code next to the download buttons; privacy policy and terms updated.
- Account deletion by the citizen.

**Not in this version**
- Spending, transferring or cashing points, recharges or vouchers.
- Points for posting, service requests or other activity.
- Wallets for officials.
- SMS codes (WhatsApp only).
- Accounts for anyone under 18.
- Leaderboards or public rankings.
- iPhone release (the design works on iPhone, but Phase 1 ships Android only).

## 4. Who uses it

| Who | What changes |
|---|---|
| Citizen without an account | Nothing. Reads, posts and requests services as today. Sees "Sign in to earn points" and invite links. |
| Citizen with an account | Signs in with Google, verifies the mobile number, invites friends, sees the wallet. Holds the new **Citizen** role for their village. |
| Super Admin | Sets the reward amounts and rules, reviews referrals, corrects points, blocks or unblocks citizen accounts, exports CSV. |
| District, Mandal and Village Admins, Panchayat members | No change. Citizen accounts never appear in their user lists. |

## 5. User journeys

### J1. Sign up
1. On Home or Profile, the citizen taps **Sign in to earn points**.
2. The app shows what is collected and why (consent notice), with links to the privacy policy and terms.
3. The citizen ticks **I am 18 or older** and **I agree**.
4. The citizen picks a Google account on the phone.
5. If the citizen came through a friend's invite link, the friend's code is already filled in; otherwise they may type one.
6. The account is created for the village already chosen in the app. The citizen now holds the Citizen role.

### J2. Verify the mobile number
1. The citizen enters their 10-digit mobile number and taps **Send code on WhatsApp**.
2. A 6-digit code arrives on WhatsApp. On Android it can fill itself in with one tap; otherwise the citizen copies it.
3. The citizen enters the code. The number is verified.
4. If a referral code was used, the referral now starts its holding period.

A citizen without WhatsApp can skip this step. They keep the account but earn no points until they verify (D6).

### J3. Invite friends
1. Once the citizen's mobile number is verified, the wallet screen shows their referral code and an **Invite** button.
2. **Invite** shares a message with the link `https://www.gramaconnect.com/app/?ref=<code>`, usually on WhatsApp.
3. The friend opens the link: the website's download section shows the code, and the app, once installed and opened from the link, fills it in.

### J4. Points on hold, then confirmed
1. When the friend verifies their mobile number, both citizens see the points as **on hold** in their wallets, with the date they are expected.
2. After the holding period, if the friend has been active (section 7), the points are **confirmed** and added to the balance. Both get a push notification.
3. If not, the points are **not confirmed** and both see why in the history.

### J5. Wallet
The wallet shows the confirmed balance, the points on hold, and a history list (date, reason, points, status). Points have no money value; the wallet says so.

### J6. Sign out and delete the account
- **Sign out** returns the app to citizen mode without an account.
- **Delete my account** (in the app, and on a web page) closes the account: the citizen loses their points, and their personal data is removed as section 9 describes.

### J7. Super Admin
1. Turns the feature on or off and sets the amounts and rules (section 7).
2. Sees all referrals with their status and reason, and can confirm or reject one, with a comment.
3. Corrects a wallet with a reason (for example after fraud). Every correction is in the audit log.
4. Finds citizen accounts (name, village, last 4 digits of the mobile), and blocks or unblocks them.
5. Exports referrals and wallet entries as CSV.

## 6. Functional requirements

### Account and login
- **FR-1** A citizen signs in with a Google account. The server checks the Google sign-in itself; the app's word is never enough.
- **FR-2** The first sign-in creates the account. Later sign-ins, on any phone, open the same account.
- **FR-3** Sign-up requires the 18+ confirmation and agreement to the consent notice. The server records the date and the notice version.
- **FR-4** Every account gets the **Citizen** role (a Django group) for the village chosen in the app. Changing the village in the app moves the role to the new village.
- **FR-5** A citizen account cannot use the officials' login, the Super Admin console, or any official screen or action.
- **FR-6** Blocking an account ends all its sessions at once, as for officials.
- **FR-7** Logins last at most 90 days, as for officials; after that the citizen signs in with Google again.
- **FR-8** The citizen can delete their account in the app and through a web page.

### Mobile number
- **FR-9** The citizen verifies one 10-digit Indian mobile number with a 6-digit code sent on WhatsApp.
- **FR-10** A code is valid for 10 minutes and allows 5 tries. A new code can be sent after 60 seconds, at most 5 times a day per number.
- **FR-11** One mobile number can belong to only one citizen account. The server never says whether a number is registered to someone else until that number's code is entered.
- **FR-12** Without a verified number, the citizen earns no points.

### Referrals
- **FR-13** Every account has a unique referral code (8 letters and digits, no look-alike characters) and an invite link. The code works for friends only once its owner's mobile number is verified; until then the wallet asks the citizen to verify before inviting.
- **FR-14** A referral code can be added at sign-up or until the mobile number is verified, but never later.
- **FR-15** A citizen cannot refer themselves: the same Google account, mobile number or phone is refused.
- **FR-16** A new citizen can be referred only once.
- **FR-17** A referral is created when the new citizen verifies their mobile number. Its points are then held for the holding period.
- **FR-18** At the end of the holding period the referral is confirmed if the new citizen was active and neither account is blocked or deleted; otherwise it is not confirmed, with a reason.
- **FR-19** A referrer can earn at most the monthly limit of confirmed referrals. Referrals over the limit are not confirmed, with that reason.

### Wallet and points
- **FR-20** Points are whole numbers. Every change is an entry in the wallet's history; entries are never edited or deleted, only reversed by a new entry.
- **FR-21** The wallet shows the confirmed balance, the points on hold and the history.
- **FR-22** Points cannot be transferred, spent or exchanged for money, goods or services in this version. The wallet and the terms say so.
- **FR-23** The Super Admin can correct a balance with a reason. The balance cannot go below zero.

### Notifications
- **FR-24** The referrer gets a push when a friend joins with their code, and both get one when the points are confirmed or not confirmed.
- **FR-25** Pushes use the citizen's language. Their texts are editable in the console's notification texts, like the existing ones.

### Super Admin console
- **FR-26** A new page lists citizen accounts, referrals and wallet entries, with search and filters, and CSV export.
- **FR-27** The Settings page gets the wallet settings of section 7.
- **FR-28** Every Super Admin action on accounts, referrals and points is recorded in the audit log.

### Website
- **FR-29** The invite link opens the website's download section and shows the friend's referral code with a **Copy** button.
- **FR-30** The privacy policy and terms describe the account, the data collected, the 18+ rule and that points have no money value.

### Language
- **FR-31** Every new screen, message, push and WhatsApp code message is available in Telugu (default) and English.

## 7. Rules and settings

The Super Admin can change these at any time in the console. The values are starting suggestions for the client to confirm.

| Setting | Meaning | Suggested start |
|---|---|---|
| Wallet on or off | Hides the whole feature in the app and the website when off | Off until launch |
| Referral reward | Points for the referrer per confirmed referral | 50 *(client to confirm)* |
| Welcome bonus | Points for the new citizen who used a code | 20 *(client to confirm)* |
| Holding period | Days the points are held | 7 |
| Active rule | Days on which the new citizen must open the app during the holding period | 2 |
| Monthly limit | Confirmed referrals one referrer can earn in a calendar month | 30 |
| Referral code window | How long after sign-up a code can be added | Until the mobile number is verified |

## 8. Fraud and abuse

Referral rewards attract fake accounts. Play Integrity is off for now, so the following rules carry the weight:
- One account per Google account, per verified mobile number, and one welcome bonus per phone.
- Self-referral refused across Google account, mobile number and phone.
- Points held, and confirmed only after real use of the app (section 7).
- A monthly limit per referrer.
- The Super Admin reviews and can reject referrals, correct wallets and block accounts.
- Once the app is on Google Play, sign-up should also require the Play Integrity check.

## 9. Privacy and legal

- **Personal data collected:** Google account id, name from Google, the verified mobile number (kept as a protected fingerprint plus the last 4 digits), the village, the 18+ confirmation and consent dates, and app-use days for the active rule. The email address from Google is **not stored** unless the client asks for it.
- **Consent (DPDP):** India's Digital Personal Data Protection Rules were notified in November 2025, and their core duties (notice, consent, security, erasure) apply from about May 2027. The sign-up consent notice and account deletion are built now so the feature is ready.
- **Children:** accounts are for 18 and over only (D5), so no parental-consent process is needed.
- **Account deletion:** Google Play requires apps that let people create an account to let them delete it, in the app and from a web page. **This makes citizen accounts an exception to the "data is kept permanently" decision of 2026-11-14** (needs the owner's confirmation, Q8).
- **Money rules (RBI):** points are collect-only, with no cash value and no transfer, so they are not a payment instrument. If points later buy anything, legal must review it first: points usable only for BVT's own services are a "closed system" that needs no RBI licence; anything cash-like or usable with others likely does.
- **WhatsApp:** the code is sent only when the citizen asks for it in the app.

## 10. Non-functional requirements

- Works on budget Android phones on 2G/3G: small screens, few requests, no large downloads.
- Every permission is checked on the server.
- Codes, tokens and full mobile numbers are never written to logs or the audit log.
- Telugu displays correctly everywhere.
- Every permission rule has a test, as for the rest of the project.
- If WhatsApp cannot be reached, the citizen is told to try again later; nothing else in the app is affected.

## 11. What the client needs to provide

| # | Item | Why |
|---|---|---|
| C1 | A verified Meta Business account, a WhatsApp Business phone number and display name | To send codes on WhatsApp |
| C2 | Approval of the WhatsApp code message (Telugu and English) | Meta approves the message before use |
| C3 | Access to the Google Cloud / Firebase project (already asked in BVTGC-32) and the OAuth consent screen details | For Google sign-in |
| C4 | Reward amounts and the name of the points, in Telugu and English | Section 7 |
| C5 | Updated privacy policy and terms (with legal review of the "no money value" wording) | Section 9 |

## 12. Open questions for the client

| # | Question | Our suggestion |
|---|---|---|
| Q1 | What are the points called? | "Grama points" / "గ్రామ పాయింట్లు" |
| Q2 | Reward and welcome amounts | 50 and 20 |
| Q3 | Is the welcome bonus also held until the new citizen is active? | Yes (assumed in this document) |
| Q4 | Do points expire? | No, in this version |
| Q5 | Should we store the citizen's email from Google? | No |
| Q6 | What will points buy later? | Decide before any redemption work; legal review first |
| Q7 | Should officials have wallets? | No, in this version |
| Q8 | Citizen accounts can be deleted, as an exception to "data is kept permanently". Agreed? | Yes; wallet history is kept without the person's details |
| Q9 | Keep a protected fingerprint of a deleted citizen's Google account and mobile for a time, to stop repeat welcome bonuses? | Yes, for 1 year, subject to legal review |

## 13. Acceptance criteria

- A new citizen can sign up with Google in under a minute on a budget phone, in Telugu.
- A citizen without an account can still read, post and request services exactly as before.
- An invite link carries the code to the website and into the app.
- Referral and welcome points show as on hold after the friend's mobile verification, and are confirmed or not confirmed automatically after the holding period.
- No self-referral or second welcome bonus works with the same Google account, mobile number or phone.
- Officials never see citizen accounts in their user lists, and citizen accounts cannot open any official screen.
- The Super Admin can change every setting in section 7, review referrals, correct points and block accounts; each action is in the audit log.
- A citizen can delete their account from the app and from the web page.

## 14. Phasing

| Step | What | Depends on |
|---|---|---|
| 1 | Backend: accounts, Citizen role, Google sign-in, WhatsApp codes, wallet and referrals, behind the on/off setting | C1, C3 |
| 2 | App: sign-in, mobile verification, wallet, invite, delete account; console page; website code display and legal texts | Step 1, C2, C4, C5 |
| 3 | Test on the qa server with the feature on; UAT | Steps 1–2 |
| 4 | Production: release the app, then switch the feature on | Step 3 |

Effort and the work for each team are in [technical-implementation.md](technical-implementation.md), section 9.

## Sources

- RBI, Master Directions on Prepaid Payment Instruments (closed system PPIs): https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12156
- Digital Personal Data Protection Rules, 2025 (PIB): https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf
- Google Play, account deletion requirements: https://support.google.com/googleplay/android-developer/answer/13327111
- WhatsApp Business Platform pricing: https://developers.facebook.com/docs/whatsapp/pricing
- WhatsApp authentication templates: https://developers.facebook.com/docs/whatsapp/business-management-api/authentication-templates
