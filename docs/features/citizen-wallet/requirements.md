# Citizen wallet and referrals: requirements

| | |
|---|---|
| Status | Draft for the client's review |
| Date | 2026-11-25 |
| Version | v4: family phones, deletion on the website with a code, support by the Super Admin only, a pilot in chosen mandals (D10 to D13). v3: login by mobile number and a one-time code, with no Google account or email (D1, D6, D8, D9) |
| Decisions by | Admin (project lead), 2026-11-25 |
| Technical design | [technical-implementation.md](technical-implementation.md) |
| Applies to | Mobile app, backend API, Super Admin console, public website |

## 1. Summary

Citizens get an optional account with a **wallet** of points. A citizen who invites a friend earns points when the friend joins, and the friend gets a welcome bonus. When the wallet goes live, points are only collected: they cannot be spent, transferred or turned into money. **Bills & Recharges go live after the wallet**; from then on, confirmed points give a discount on bills and recharges, paid for by the client ([Bills and recharges](../bills-recharges/requirements.md), D9 to D13). Points never become cash and cannot be transferred.

Today citizens use the app without any account. Reading news, posting and service requests stay exactly as they are. The new account is needed only for the wallet and referrals.

Citizens sign in with their **mobile number** and a **one-time code**, sent on WhatsApp or by SMS. No email address, Google account or password is needed: many villagers have no email they know, and a family often shares one phone. Each account gets a new **Citizen** role, which works like the officials' roles (a Django group with permissions, tied to the citizen's village).

## 2. Decisions taken (2026-11-25)

| # | Question | Decision |
|---|---|---|
| D1 | How citizens sign up and log in | With their 10-digit mobile number and a one-time code. No email, Google account or password. Changed 2026-11-25; before that: Google sign-in, then the mobile number verified by a WhatsApp code. |
| D2 | What needs a login | Only the wallet and referrals. Reading news, posting and service requests stay without login. |
| D3 | What earns points | The referrer, when a friend signs up; a welcome bonus for the new citizen who used a referral code. Both are **held** until the new citizen has been active for a while. |
| D4 | What points can be used for | Collect only when the wallet goes live. Once Bills & Recharges go live after it, confirmed points give a discount on bills and recharges, paid for by the client, within limits the Super Admin sets ([Bills and recharges](../bills-recharges/requirements.md), D9 to D13). Never cash, never transferred. Updated 2026-11-25; before that: "collect only, decided later". |
| D5 | Who may have an account | 18 and over only. Sign-up asks the citizen to confirm their age. |
| D6 | How the code reaches the citizen | On WhatsApp first; by SMS for anyone who asks for it or gets nothing on WhatsApp. So every account has a verified mobile number from the start. Changed 2026-11-25; before that: WhatsApp only, and a citizen without WhatsApp could sign in but earned no points. |
| D7 | How citizens get the Citizen role | Automatically at sign-up, for the village chosen in the app. |
| D8 | A changed or lost mobile number | The citizen moves the account to a new number in the app, with a code sent to the new number. For a lost SIM, the operator gives a duplicate SIM with the same number. The Super Admin does not move accounts. An account unused for 6 months or more first asks **Is this your account?**, because a number can be given to someone new (FR-12a). |
| D9 | When a friend's referral code can be entered | At sign-up only. Before 2026-11-25: until the mobile number was verified. |
| D10 | Several accounts on one phone (families) | Up to 3 accounts per phone can get the welcome bonus. A referral between two accounts that signed up on the same phone is allowed: the new citizen gets the welcome bonus, the referrer gets no reward. From the 4th account on a phone, no welcome bonus. Before: one welcome bonus per phone, and such referrals were refused. |
| D11 | Who helps citizens with their points | The Super Admin only. The wallet history shows the reason for every decision, and the app's help shows whom to contact. District, Mandal and Village Admins and Panchayat members see no citizen accounts or wallets. |
| D12 | Deleting the account without the app | On the website: the citizen enters the mobile number, gets a code, and confirms. This replaces the request through the contact form, which proved nothing about who was asking. |
| D13 | Launch | A pilot in mandals the Super Admin picks. Sign-up is open only for villages in those mandals; elsewhere the wallet does not show yet. The Super Admin widens the list, or opens it to everyone. |

Officials keep their username and password login. Nothing changes for them. An official who also wants a wallet gets a separate citizen account, even with the same mobile number.

## 3. Scope

**In this version**
- Citizen sign-up and login with the mobile number and a one-time code (WhatsApp or SMS), with the name, the 18+ confirmation and a consent notice.
- Changing the account's mobile number in the app.
- Up to 3 accounts per phone for families (D10).
- Account deletion on the website with a code (D12).
- A pilot in mandals the Super Admin picks (D13).
- The Citizen role (Django group), given automatically.
- A personal referral code and invite link for each citizen.
- Referral points and a welcome bonus, held and then confirmed or not confirmed.
- A wallet screen: balance, points on hold, history.
- Push notifications about points.
- Super Admin tools: reward settings, referral review, point corrections, citizen account blocking, CSV export.
- Website: the invite link shows the friend's referral code next to the download buttons; privacy policy and terms updated.
- Account deletion by the citizen.

**Not in this version**
- Spending points. That comes with Bills & Recharges, which go live after the wallet.
- Transferring points, cashing them or buying vouchers with them.
- Points for posting, service requests or other activity.
- Wallets for officials.
- Login with an email address, a Google account or a password.
- Moving an account to a new number by the Super Admin, for a citizen who lost their number for good.
- Accounts for anyone under 18.
- Leaderboards or public rankings.
- iPhone release (the design works on iPhone, but Phase 1 ships Android only).

## 4. Who uses it

| Who | What changes |
|---|---|
| Citizen without an account | Nothing. Reads, posts and requests services as today. Sees "Sign in to earn points" and invite links, in the pilot mandals while the pilot runs (D13). |
| Citizen with an account | Signs in with their mobile number and a code, invites friends, sees the wallet. Holds the new **Citizen** role for their village. |
| Super Admin | Sets the reward amounts and rules, reviews referrals, corrects points, blocks or unblocks citizen accounts, exports CSV. |
| District, Mandal and Village Admins, Panchayat members | No change. Citizen accounts never appear in their user lists, and they cannot look up citizens' wallets (D11). |

## 5. User journeys

### J1. Sign up
1. On Home or Profile, the citizen taps **Sign in to earn points**.
2. The citizen enters their 10-digit mobile number. On Android the app first offers the phone's own SIM numbers, so one tap is enough. The screen says why the number is needed, with a link to the privacy policy.
3. The citizen taps **Send code on WhatsApp**, or **Send code by SMS** if there is no WhatsApp on that number. If nothing arrives, **Send by SMS instead** appears when the resend timer ends.
4. A 6-digit code arrives. On Android an SMS code fills in after one tap on **Allow**, and a WhatsApp code is copied with its button. The citizen enters the code.
5. For a new number, the app asks for the citizen's name (Telugu or English) and shows the consent notice with links to the privacy policy and terms. The citizen ticks **I am 18 or older** and **I agree**.
6. If the citizen came through a friend's invite link, the friend's code is already filled in; otherwise they may type one. This is the only time a code can be entered (D9).
7. The account is created for the village already chosen in the app. The citizen now holds the Citizen role. If a referral code was used, the referral starts its holding period.

While the pilot runs, **Sign in to earn points** shows only when the village chosen in the app is in a pilot mandal (D13). Elsewhere the wallet stays hidden, and a friend's invite link says the wallet is coming to that area soon.

Family members can sign up on one shared phone, each with their own mobile number: they sign out and sign in with their own number and code (D10).

### J2. Sign in again
On a new phone, after signing out, or when the 90-day login ends, the citizen enters the same number and a new code (J1 steps 2 to 4). The account opens with its wallet; the name and consent are not asked again.

If the account has not been used for 6 months or more, the app first shows **Is this your account?** with the masked name (for example "R*** K***"). **Yes** opens it. **No** closes the old account, whose points are lost, and starts a new sign-up for the number: the number has probably been given to someone new (FR-12a).

### J3. Invite friends
1. The wallet screen shows the citizen's referral code and an **Invite** button.
2. **Invite** shares a message with the link `https://www.gramaconnect.com/app/?ref=<code>`, usually on WhatsApp.
3. The friend opens the link: the website's download section shows the code, and the app, once installed and opened from the link, fills it in.

### J4. Points on hold, then confirmed
1. When the friend signs up with the code, both citizens see the points as **on hold** in their wallets, with the date they are expected.
2. After the holding period, if the friend has been active (section 7), the points are **confirmed** and added to the balance. Both get a push notification.
3. If not, the points are **not confirmed** and both see why in the history.

### J5. Wallet
The wallet shows the confirmed balance, the points on hold, and a history list (date, reason, points, status). **Questions about points?** opens the app's help-line numbers, where the Super Admin lists the support number (D11, C6). Points have no cash value and cannot be transferred; the wallet says so. Once Bills & Recharges are live, the wallet also shows what the balance is worth as a discount.

### J6. Sign out, change the number, delete the account
- **Sign out** returns the app to citizen mode without an account.
- **Change my mobile number** (Profile): the citizen enters the new number and the code sent to it. The account, its wallet and its referral code move to the new number, and the old number is free. A citizen who lost their SIM gets a duplicate SIM with the same number from the operator, then signs in as usual.
- **Delete my account** (in the app, or on the website with the mobile number and a code, D12) closes the account: the citizen loses their points, and their personal data is removed as section 9 describes.

### J7. Super Admin
1. Turns the feature on or off, picks the pilot mandals, and sets the amounts and rules (section 7).
2. Sees all referrals with their status and reason, and can confirm or reject one, with a comment.
3. Corrects a wallet with a reason (for example after fraud). Every correction is in the audit log.
4. Finds citizen accounts (name, village, last 4 digits of the mobile), and blocks or unblocks them. Answers citizens' questions about their points; local officials do not see wallets (D11).
5. Exports referrals and wallet entries as CSV.

## 6. Functional requirements

### Account and login
- **FR-1** A citizen signs in with a 10-digit Indian mobile number and a 6-digit one-time code. The server checks the code itself; the app's word is never enough. There is no email, Google account or password.
- **FR-2** The first sign-in with a number creates the account. Later sign-ins with the same number, on any phone, open the same account.
- **FR-3** Sign-up requires the citizen's name, the 18+ confirmation and agreement to the consent notice. The server records the date and the notice version.
- **FR-4** Every account gets the **Citizen** role (a Django group) for the village chosen in the app. Changing the village in the app moves the role to the new village.
- **FR-5** A citizen account cannot use the officials' login, the Super Admin console, or any official screen or action. An official's account and a citizen account stay separate, even when they share a mobile number.
- **FR-6** Blocking an account ends all its sessions at once, as for officials.
- **FR-7** Logins last at most 90 days, as for officials; after that the citizen signs in again with a new code.
- **FR-8** The citizen can delete their account in the app, or on the website after entering the mobile number and a code sent to it (D12). The Super Admin can also delete an account, with a reason (for example fraud or a legal request); it is audited.

### Codes and the mobile number
- **FR-9** The code is sent on WhatsApp, or by SMS when the citizen asks for it. On Android the app offers the phone's own SIM numbers, and an SMS code fills in after one tap on **Allow**.
- **FR-10** A code is valid for 10 minutes and allows 5 tries. A new code can be sent after 60 seconds, at most 5 times a day per number, WhatsApp and SMS together. Codes are also limited per phone and per internet address, so nobody can run up the message bill.
- **FR-11** One mobile number belongs to only one citizen account. The server never says whether a number is registered until that number's code is entered.
- **FR-12** A signed-in citizen can move the account to a new mobile number with a code sent to the new number. A number that belongs to another account is refused. The old number is free at once. The Super Admin does not move accounts between numbers (D8).
- **FR-12a** When someone signs in to an account unused for the set time (section 7), the app first shows the masked name and asks **Is this your account?** **No**, after a warning that its points are lost, closes the old account as a deletion would (FR-8, section 9) and starts a new sign-up for the number.

### Referrals
- **FR-13** Every account has a unique referral code (8 letters and digits, no look-alike characters) and an invite link.
- **FR-14** A referral code can be entered only at sign-up, never later (D9).
- **FR-15** A citizen cannot refer themselves: the same mobile number is refused.
- **FR-15a** A referral between two accounts that signed up on the same phone is allowed, but only the new citizen's welcome bonus is paid; the referrer gets no reward (D10).
- **FR-15b** At most 3 accounts that signed up on one phone get the welcome bonus, deleted accounts included (a Super Admin setting, section 7).
- **FR-16** A new citizen can be referred only once.
- **FR-17** A referral is created when the new citizen signs up with the code. Its points are then held for the holding period.
- **FR-18** At the end of the holding period the referral is confirmed if the new citizen was active and neither account is blocked or deleted; otherwise it is not confirmed, with a reason.
- **FR-19** A referrer can earn at most the monthly limit of confirmed referrals. Referrals over the limit are not confirmed, with that reason.

### Wallet and points
- **FR-20** Points are whole numbers. Every change is an entry in the wallet's history; entries are never edited or deleted, only reversed by a new entry.
- **FR-21** The wallet shows the confirmed balance, the points on hold and the history.
- **FR-22** Points cannot be transferred or exchanged for cash. Until Bills & Recharges go live, they cannot be spent either. After that, confirmed points are used only as a discount on bills and recharges, within the Super Admin's limits (Bills and recharges, FR-23 to FR-29). The wallet and the terms say so.
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
- **FR-30** The privacy policy and terms describe the account, the data collected, the 18+ rule and that points have no cash value and cannot be transferred.

### Language
- **FR-31** Every new screen, message and push, and the WhatsApp and SMS code messages, are available in Telugu (default) and English.

## 7. Rules and settings

The Super Admin can change these at any time in the console. The values are starting suggestions for the client to confirm.

| Setting | Meaning | Suggested start |
|---|---|---|
| Wallet on or off | Hides the whole feature in the app and the website when off | Off until launch |
| Pilot mandals | Mandals whose villages can sign up and see the wallet; empty means everywhere (D13) | The first pilot mandals *(client to choose, Q11)* |
| Referral reward | Points for the referrer per confirmed referral | 50 *(client to confirm)* |
| Welcome bonus | Points for the new citizen who used a code | 20 *(client to confirm)* |
| Welcome bonuses per phone | How many accounts signed up on one phone can get the welcome bonus (D10) | 3 |
| Holding period | Days the points are held | 7 |
| Active rule | Days on which the new citizen must open the app during the holding period | 2 |
| Monthly limit | Confirmed referrals one referrer can earn in a calendar month | 30 |
| Unused account check | Months without use after which signing in asks **Is this your account?** (FR-12a) | 6 *(a number can go to someone new after 90 days, so the client may choose 3; Q10)* |

A referral code can be entered only at sign-up (D9); that is a fixed rule, not a setting.

## 8. Fraud and abuse

Referral rewards attract fake accounts. Play Integrity is off for now, so the following rules carry the weight:
- One account per mobile number. A SIM needs KYC at the operator, so numbers are harder to make in bulk than email or Google accounts.
- At most 3 welcome bonuses per phone, for families (D10), and no referrer reward between accounts that signed up on the same phone. A phone with many SIMs therefore earns little.
- Self-referral with the same mobile number refused.
- The pilot (D13) keeps early fraud, and its cost, to a few mandals.
- Code requests limited per number, per phone and per internet address (FR-10).
- Points held, and confirmed only after real use of the app (section 7).
- A monthly limit per referrer.
- The Super Admin reviews and can reject referrals, correct wallets and block accounts.
- Once the app is on Google Play, sign-up should also require the Play Integrity check.
- **Points will gain a rupee value** when Bills & Recharges go live. Points earned by fake referrals during the wallet-only period would then be spendable, so the Super Admin should review referrals before that date. The bills limits (Bills and recharges, section 7) cap what points can cover.

## 9. Privacy and legal

- **Personal data collected:** the name the citizen types, the mobile number (kept as a protected fingerprint plus the last 4 digits), the village, the 18+ confirmation and consent dates, and app-use days for the active rule and the unused account check. **No email address** is collected.
- **Consent (DPDP):** India's Digital Personal Data Protection Rules were notified in November 2025, and their core duties (notice, consent, security, erasure) apply from about May 2027. The sign-up consent notice and account deletion are built now so the feature is ready. The number screen says why the number is needed, with a link to the privacy policy, before any code is sent.
- **Children:** accounts are for 18 and over only (D5), so no parental-consent process is needed.
- **Account deletion:** Google Play requires apps that let people create an account to let them delete it, in the app and from a web page. The web page asks for the mobile number and a code, so only the number's owner can delete the account (D12). **This makes citizen accounts an exception to the "data is kept permanently" decision of 2026-11-14** (needs the owner's confirmation, Q8).
- **Money rules (RBI):** at the wallet's launch points are collect-only, with no cash value and no transfer, so they are not a payment instrument. With Bills & Recharges they become a **discount the client gives** on its own bill-payment service. The client pays the rest of the bill with its own money, and points never pay a biller and never become cash. A lawyer must confirm this before Bills & Recharges go live. Points that paid a third party directly would be a prepaid payment instrument, which needs an RBI licence.
- **WhatsApp and SMS:** a code is sent only when the citizen asks for it in the app. Meta and the SMS provider receive the number and the code only to deliver it. Business SMS in India must come from a sender registered on DLT, the operators' register of business senders (C3).

## 10. Non-functional requirements

- Works on budget Android phones on 2G/3G: small screens, few requests, no large downloads.
- Every permission is checked on the server.
- Codes, tokens and full mobile numbers are never written to logs or the audit log.
- Telugu displays correctly everywhere.
- Every permission rule has a test, as for the rest of the project.
- If WhatsApp cannot be reached, the app offers SMS; if neither works, the citizen is told to try again later. Nothing else in the app is affected.

## 11. What the client needs to provide

| # | Item | Why |
|---|---|---|
| C1 | A verified Meta Business account, a WhatsApp Business phone number and display name | To send codes on WhatsApp |
| C2 | Approval of the WhatsApp code message (Telugu and English) | Meta approves the message before use |
| C3 | DLT registration for SMS: the client's business on an operator's DLT portal, a 6-letter sender name (for example GRAMAC), the code message in Telugu and English approved there, and an account with an Indian SMS provider | To send codes by SMS. Each approval usually takes a few days; start together with C1 |
| C4 | Reward amounts and the name of the points, in Telugu and English | Section 7 |
| C5 | Updated privacy policy and terms (with legal review of the points wording: no cash value now, a discount on bills later) | Section 9 |
| C6 | A support phone or WhatsApp number for citizens' questions about points, and who answers it | D11; listed as a statewide help line in the app |

## 12. Open questions for the client

| # | Question | Our suggestion |
|---|---|---|
| Q1 | What are the points called? | "Grama points" / "గ్రామ పాయింట్లు" |
| Q2 | Reward and welcome amounts | 50 and 20 |
| Q3 | Is the welcome bonus also held until the new citizen is active? | Yes (assumed in this document) |
| Q4 | Do points expire? | No, in this version. Asked again for Bills & Recharges (its Q9), since points then have a value. |
| Q5 | Should we store the citizen's email from Google? | Answered 2026-11-25: no email is collected at all; login is by mobile number (D1). |
| Q6 | What will points buy later? | Answered 2026-11-25: a discount on bills and recharges, paid for by the client, after Bills & Recharges go live (D4; Bills and recharges D9 to D13). A lawyer confirms the model first. |
| Q7 | Should officials have wallets? | No, in this version |
| Q8 | Citizen accounts can be deleted, as an exception to "data is kept permanently". Agreed? | Yes; wallet history is kept without the person's details |
| Q9 | Keep a protected fingerprint of a deleted citizen's mobile number for a time, to stop repeat welcome bonuses? | Yes, for 1 year, subject to legal review |
| Q10 | After how many months without use should signing in ask **Is this your account?** (FR-12a) | 6. Operators may give a number to someone new after 90 days, so 3 protects more, but asks returning citizens more often. |
| Q11 | Which mandals for the pilot, and for how long? (D13) | 2 or 3 mandals the client's team can visit, for about a month, then widen |

## 13. Acceptance criteria

- A new citizen can sign up with their mobile number in under a minute on a budget phone, in Telugu, with or without WhatsApp.
- A citizen without an account can still read, post and request services exactly as before.
- An invite link carries the code to the website and into the app.
- Referral and welcome points show as on hold after the friend's mobile verification, and are confirmed or not confirmed automatically after the holding period.
- No self-referral or second welcome bonus works with the same mobile number or phone.
- A citizen can move the account to a new number, and an account unused for the set time asks **Is this your account?** before it opens.
- Officials never see citizen accounts in their user lists, and citizen accounts cannot open any official screen.
- The Super Admin can change every setting in section 7, review referrals, correct points and block accounts; each action is in the audit log.
- A citizen can delete their account from the app, and from the web page with a code; nobody can delete it from the web page without the code.
- Two family members can each sign up on one shared phone and get the welcome bonus; a 4th account on that phone gets none.
- While the pilot runs, the wallet does not show for villages outside the pilot mandals, and sign-up there is refused by the server.

## 14. Phasing

| Step | What | Depends on |
|---|---|---|
| 1 | Backend: accounts, Citizen role, mobile sign-in with WhatsApp and SMS codes, wallet and referrals, behind the on/off setting | C1, C3 |
| 2 | App: sign-in, mobile verification, wallet, invite, delete account; console page; website code display and legal texts | Step 1, C2, C4, C5 |
| 3 | Test on the qa server with the feature on; UAT | Steps 1–2 |
| 4 | Production: release the app, then switch the feature on for the pilot mandals (D13); widen after the pilot | Step 3, Q11 |
| 5 | Bills & Recharges, with points as a discount ([Bills and recharges](../bills-recharges/requirements.md)) | The wallet live in production |

Effort and the work for each team are in [technical-implementation.md](technical-implementation.md), section 9.

## Sources

- RBI, Master Directions on Prepaid Payment Instruments (closed system PPIs): https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12156
- Digital Personal Data Protection Rules, 2025 (PIB): https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf
- Google Play, account deletion requirements: https://support.google.com/googleplay/android-developer/answer/13327111
- WhatsApp Business Platform pricing: https://developers.facebook.com/docs/whatsapp/pricing
- WhatsApp authentication templates: https://developers.facebook.com/docs/whatsapp/business-management-api/authentication-templates
- Android Phone Number Hint (the phone offers its own SIM numbers): https://developer.android.com/identity/phone-number-hint
- Android SMS Retriever and SMS User Consent (an SMS code fills in without the SMS permission): https://developer.android.com/identity/sms-retriever
- India's SMS rules and DLT registration (third-party guide): https://www.telerivet.com/blog/india-sms-compliance-trai-dlt-registration-and-tcccpr-guide
- SMS code prices in India, 2026 (third-party guide): https://www.messagecentral.com/blog/sms-otp-pricing-india
- TRAI to the Supreme Court: a disconnected number is not given to someone new for at least 90 days: https://www.livelaw.in/top-stories/once-a-mobile-number-is-deactivated-it-is-not-assigned-to-a-new-user-for-90-days-trai-to-supreme-court-241506
