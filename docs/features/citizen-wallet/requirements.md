# Citizen wallet and referrals: requirements

| | |
|---|---|
| Status | Draft for the client's review |
| Date | 2026-11-25 |
| Version | v5: the client's sign-up form. Name, mobile number and email (both required) and a password; login with the email or the mobile number; a forgotten password reset with an email code; WhatsApp and SMS in a later stage, before Bills & Recharges (D1, D6, D8, D12, D14 to D17). v4: family phones, deletion on the website, support by the Super Admin only, a pilot in chosen mandals (D10 to D13) |
| Decisions by | Admin (project lead), 2026-11-25 |
| Technical design | [technical-implementation.md](technical-implementation.md) |
| Applies to | Mobile app, backend API, Super Admin console, public website |

## 1. Summary

Citizens get an optional account with a **wallet** of points. A citizen who invites a friend earns points when the friend joins, and the friend gets a welcome bonus. When the wallet goes live, points are only collected: they cannot be spent, transferred or turned into money. **Bills & Recharges go live after the wallet**; from then on, confirmed points give a discount on bills and recharges, paid for by the client ([Bills and recharges](../bills-recharges/requirements.md), D9 to D13). Points never become cash and cannot be transferred.

Today citizens use the app without any account. Reading news, posting and service requests stay exactly as they are. The new account is needed only for the wallet and referrals.

Citizens create the account with a **sign-up form**: their name, mobile number, email address and a password. A code sent to the email confirms it. They then log in with **their email or their mobile number, and the password**. A forgotten password is reset with a code sent to the email. **WhatsApp and SMS codes come in a later stage**, before Bills & Recharges. In that stage every citizen confirms their mobile number once. Each account gets a new **Citizen** role, which works like the officials' roles (a Django group with permissions, tied to the citizen's village).

## 2. Decisions taken (2026-11-25)

| # | Question | Decision |
|---|---|---|
| D1 | How citizens sign up and log in | A sign-up form with the name, the 10-digit mobile number, an email address (both required), a password and the password again. Login with the email or the mobile number, plus the password. Changed 2026-11-25 (v5, the client's plan). Before that: the mobile number and a one-time code (v3), and before that Google sign-in. |
| D2 | What needs a login | Only the wallet and referrals. Reading news, posting and service requests stay without login. |
| D3 | What earns points | The referrer, when a friend signs up; a welcome bonus for the new citizen who used a referral code. Both are **held** until the new citizen has been active for a while. |
| D4 | What points can be used for | Collect only when the wallet goes live. Once Bills & Recharges go live after it, confirmed points give a discount on bills and recharges, paid for by the client, within limits the Super Admin sets ([Bills and recharges](../bills-recharges/requirements.md), D9 to D13). Never cash, never transferred. Updated 2026-11-25; before that: "collect only, decided later". |
| D5 | Who may have an account | 18 and over only. Sign-up asks the citizen to confirm their age. |
| D6 | How codes reach the citizen | By email only, at first: a code at sign-up to confirm the email (D14), and a code when the password is forgotten. WhatsApp and SMS come in a later stage (D17). Changed 2026-11-25 (v5). Before that: WhatsApp first, with SMS as the backup. |
| D7 | How citizens get the Citizen role | Automatically at sign-up, for the village chosen in the app. |
| D8 | A changed or lost mobile number | The citizen changes the number in the app after typing their password. A lost SIM does not stop the login, because the password does not depend on it. A number someone else has already registered is freed by the Super Admin after a call-back (D16). Changed 2026-11-25 (v5). Before that: a code to the new number, and an "Is this your account?" check for long-unused accounts. The check is no longer needed, because someone given an old number does not know the password. |
| D9 | When a friend's referral code can be entered | At sign-up only. Before 2026-11-25: until the mobile number was verified. |
| D10 | Several accounts on one phone (families) | Up to 3 accounts per phone can get the welcome bonus. Each account still needs its own mobile number and email. A referral between two accounts that signed up on the same phone is allowed: the new citizen gets the welcome bonus, the referrer gets no reward. From the 4th account on a phone, no welcome bonus. Before: one welcome bonus per phone, and such referrals were refused. |
| D11 | Who helps citizens with their points and accounts | The Super Admin only. The wallet history shows the reason for every decision, and the app's help shows whom to contact. District, Mandal and Village Admins and Panchayat members see no citizen accounts or wallets. |
| D12 | Deleting the account without the app | On the website: the citizen types the email or the mobile number, gets a code at the account's email, and confirms. Changed 2026-11-25 (v5); in v4 the code went to the mobile number. Before v4: a request through the contact form, which proved nothing about who was asking. |
| D13 | Launch | A pilot in mandals the Super Admin picks. Sign-up is open only for villages in those mandals; elsewhere the wallet does not show yet. The Super Admin widens the list, or opens it to everyone. |
| D14 | Checking the email | At sign-up a 6-digit code goes to the email. The account is created only when the code is typed in. This catches mistyped addresses, which would make a password reset impossible, and made-up addresses used for welcome bonuses. (v5) |
| D15 | Password rule for citizens | At least 8 characters: letters, numbers or both. Very common passwords (such as 12345678 or password) are refused, and so are passwords too close to the citizen's own name, email or mobile number. Officials keep their own rule: at least 12 characters with a capital letter, a small letter, a number and a special character. (v5) |
| D16 | A lost email, or a number taken by someone else | The citizen calls the support number (C6). The Super Admin calls the mobile number back to make sure the caller owns it. Then the Super Admin sets a new email for the account, and the citizen resets the password with a code sent to it. Or, for a number another account registered, the Super Admin removes it from that account. Each action is audited. The Super Admin never sees or sets a password. (v5) |
| D17 | WhatsApp and SMS | A later stage, which must go live **before Bills & Recharges**: every citizen confirms their mobile number once with a WhatsApp or SMS code, and a forgotten password can also be reset by WhatsApp or SMS. Login by password stays. Bills & Recharges need a confirmed number. (v5) |

Officials keep their username and password login and their own password rule. Nothing changes for them. The officials' login does not accept citizen accounts. An official who also wants a wallet gets a separate citizen account, even with the same mobile number or email.

## 3. Scope

**In this version**
- Citizen sign-up: name, mobile number, email address, password and the password again, the 18+ confirmation and the consent notice. A code sent to the email confirms it (D14).
- Login with the email or the mobile number and the password.
- **Forgot password:** a code by email, then a new password.
- In Profile: changing the password, the email (with a code sent to the new email) and the mobile number (with the password).
- Super Admin help for a lost email or a number taken by someone else, after a call-back (D16).
- Up to 3 accounts per phone for families (D10).
- Account deletion in the app, and on the website with an email code (D12).
- A pilot in mandals the Super Admin picks (D13).
- The Citizen role (Django group), given automatically.
- A personal referral code and invite link for each citizen.
- Referral points and a welcome bonus, held and then confirmed or not confirmed.
- A wallet screen: balance, points on hold, history.
- Push notifications about points.
- Super Admin tools: reward settings, referral review, point corrections, citizen account blocking, CSV export.
- Website: the invite link shows the friend's referral code next to the download buttons; privacy policy and terms updated.

**Not in this version**
- WhatsApp and SMS codes, and confirming the mobile number. They come in the later stage (D17), before Bills & Recharges.
- Spending points. That comes with Bills & Recharges, which go live after the wallet.
- Transferring points, cashing them or buying vouchers with them.
- Points for posting, service requests or other activity.
- Wallets for officials.
- Login with a Google account, or with a one-time code instead of the password.
- Accounts for anyone under 18.
- Leaderboards or public rankings.
- iPhone release (the design works on iPhone, but Phase 1 ships Android only).

## 4. Who uses it

| Who | What changes |
|---|---|
| Citizen without an account | Nothing. Reads, posts and requests services as today. Sees "Sign in to earn points" and invite links, in the pilot mandals while the pilot runs (D13). |
| Citizen with an account | Logs in with their email or mobile number and password, invites friends, sees the wallet. Holds the new **Citizen** role for their village. |
| Super Admin | Sets the reward amounts and rules, reviews referrals, corrects points, blocks or unblocks citizen accounts, exports CSV. Helps citizens who lost their email or whose number is taken (D16). |
| District, Mandal and Village Admins, Panchayat members | No change. Citizen accounts never appear in their user lists, and they cannot look up citizens' wallets (D11). |

## 5. User journeys

### J1. Sign up
1. On Home or Profile, the citizen taps **Sign in to earn points**, then **Create account**.
2. The form asks for the name (Telugu or English), the 10-digit mobile number, the email address, a password and the password again. On Android the mobile field offers the phone's own SIM numbers, so one tap is enough. The form says why the number and the email are needed, with a link to the privacy policy.
3. The citizen ticks **I am 18 or older** and **I agree** to the consent notice, which links to the privacy policy and terms.
4. If the citizen came through a friend's invite link, the friend's code is already filled in; otherwise they may type one. This is the only time a code can be entered (D9).
5. The citizen taps **Create account**. If the mobile number or the email already has an account, the app says so and offers **Log in** and **Forgot password?**.
6. A 6-digit code goes to the email. The app shows the address with most of it hidden, for example "r***@gmail.com", and reminds the citizen to check the spam folder. The citizen types the code. **Send again** appears after 60 seconds, and **Change email** goes back to the form.
7. The account is created for the village already chosen in the app, and the citizen is logged in. The citizen now holds the Citizen role. If a referral code was used, the referral starts its holding period.

While the pilot runs, **Sign in to earn points** shows only when the village chosen in the app is in a pilot mandal (D13). Elsewhere the wallet stays hidden, and a friend's invite link says the wallet is coming to that area soon.

Family members can sign up on one shared phone, each with their own mobile number and email: they log out and log in with their own details (D10).

A citizen whose mobile number is already registered by someone else calls the support number. The Super Admin frees the number after calling it back (D16).

### J2. Log in again
On a new phone, after logging out, or when the 90-day login ends, the citizen taps **Log in** and types the email or the mobile number, and the password. After 5 wrong passwords from one place, logging in to that account waits 15 minutes, as for officials.

### J3. Forgot password
1. **Forgot password?** on the login screen asks for the email or the mobile number.
2. A 6-digit code goes to the account's email. The app shows the same message whatever was typed, so the screen never tells whose details are registered.
3. The citizen types the code and a new password twice. The password changes, every other login of the account ends, and an email tells the citizen that the password was changed.
4. A citizen who can no longer open that email calls the support number (D16).

### J4. Invite friends
1. The wallet screen shows the citizen's referral code and an **Invite** button.
2. **Invite** shares a message with the link `https://www.gramaconnect.com/app/?ref=<code>`, usually on WhatsApp.
3. The friend opens the link: the website's download section shows the code, and the app, once installed and opened from the link, fills it in.

### J5. Points on hold, then confirmed
1. When the friend signs up with the code, both citizens see the points as **on hold** in their wallets, with the date they are expected.
2. After the holding period, if the friend has been active (section 7), the points are **confirmed** and added to the balance. Both get a push notification.
3. If not, the points are **not confirmed** and both see why in the history.

### J6. Wallet
The wallet shows the confirmed balance, the points on hold, and a history list (date, reason, points, status). **Questions about points?** opens the app's help-line numbers, where the Super Admin lists the support number (D11, C6). Points have no cash value and cannot be transferred; the wallet says so. Once Bills & Recharges are live, the wallet also shows what the balance is worth as a discount.

### J7. Profile: log out, change details, delete the account
- **Log out** returns the app to citizen mode without an account.
- **Change password:** the current password, then the new one twice. Every other login of the account ends.
- **Change email:** the new email and the password. A code goes to the new email; once it is typed in, the email changes and the old address gets a notice.
- **Change mobile number:** the new number and the password. A number that belongs to another account is refused, and the app points to the support number (D16). Until the later stage (D17), the new number is not checked with a code.
- **Delete my account** (in the app with the password, or on the website with a code sent to the email, D12) closes the account: the citizen loses their points, and their personal data is removed as section 9 describes.

### J8. Super Admin
1. Turns the feature on or off, picks the pilot mandals, and sets the amounts and rules (section 7).
2. Sees all referrals with their status and reason, and can confirm or reject one, with a comment.
3. Corrects a wallet with a reason (for example after fraud). Every correction is in the audit log.
4. Finds citizen accounts by name, village or the last 4 digits of the mobile, or by the full mobile number or email a caller gives. Blocks or unblocks them. Answers citizens' questions about their points; local officials do not see wallets (D11).
5. Helps a citizen who lost their email, or whose number another account holds, after calling the number back (D16).
6. Exports referrals and wallet entries as CSV.

## 6. Functional requirements

### Account and login
- **FR-1** Sign-up asks for the name, a 10-digit Indian mobile number, an email address, and a password typed twice. All are required. The app checks that the two passwords match; the server checks everything else itself, and the app's word is never enough.
- **FR-2** Login takes the email or the mobile number, plus the password. Capital and small letters in the email make no difference.
- **FR-3** Sign-up requires the 18+ confirmation and agreement to the consent notice. The server records the date and the notice version.
- **FR-4** Every account gets the **Citizen** role (a Django group) for the village chosen in the app. Changing the village in the app moves the role to the new village.
- **FR-5** A citizen account cannot use the officials' login, the Super Admin console, or any official screen or action. An official's account and a citizen account stay separate, even when they share a mobile number or an email.
- **FR-6** Blocking an account ends all its sessions at once, as for officials.
- **FR-7** Logins last at most 90 days, as for officials; after that the citizen logs in again with the password.
- **FR-8** 5 wrong passwords for one account from one internet address lock logging in to that account from there for 15 minutes, as for officials. The email and the mobile number count as the same account. A password reset ends the lock.
- **FR-9** The citizen can delete their account in the app with the password, or on the website after typing a code sent to the account's email (D12). The Super Admin can also delete an account, with a reason (for example fraud or a legal request); it is audited.

### Email, password and the mobile number
- **FR-10** At sign-up the email is confirmed with a 6-digit code; the account exists only after the right code (D14). An unfinished sign-up is forgotten after 24 hours.
- **FR-11** A mobile number belongs to only one citizen account, and so does an email. Sign-up says when the number or the email is already registered, so the citizen can log in or reset the password instead. Gmail addresses that differ only by dots, or by a "+" part before the @, count as the same email.
- **FR-12** A code is valid for 10 minutes and allows 5 tries. A new code can be sent after 60 seconds, at most 5 times a day per account or email address. Codes are also limited per phone and per internet address.
- **FR-13** The password rule (D15): at least 8 characters; very common passwords, and passwords too close to the citizen's name, email or mobile number, are refused. The server keeps only a secure fingerprint of the password, as for officials; nobody can read it.
- **FR-14** **Forgot password:** the citizen types the email or the mobile number and gets a code at the account's email. The answer is the same whether or not the details are registered. The right code and a new password reset it, end every other login of the account, end a lock (FR-8), and send a notice to the email.
- **FR-15** A signed-in citizen can change the password (with the current password), the email (with the password and a code sent to the new email; the old address gets a notice) and the mobile number (with the password; a number that belongs to another account is refused). Every change is audited.
- **FR-16** The Super Admin's help (D16), after calling back the mobile number the caller gives, which must match the account:
  - set a new email for the account, which the citizen then confirms through **Forgot password**;
  - or remove a mobile number from the account that registered it, which must then add a new number at its next login.
  The Super Admin never sees or sets a password. Each action needs a reason and is audited.

### Referrals
- **FR-17** Every account has a unique referral code (8 letters and digits, no look-alike characters) and an invite link.
- **FR-18** A referral code can be entered only at sign-up, never later (D9).
- **FR-19** A citizen cannot refer themselves: the same mobile number or the same email is refused.
- **FR-20** A referral between two accounts that signed up on the same phone is allowed, but only the new citizen's welcome bonus is paid; the referrer gets no reward (D10).
- **FR-21** At most 3 accounts that signed up on one phone get the welcome bonus, deleted accounts included (a Super Admin setting, section 7).
- **FR-22** A new citizen can be referred only once.
- **FR-23** A referral is created when the new citizen's account is created (after the email code). Its points are then held for the holding period.
- **FR-24** At the end of the holding period the referral is confirmed if the new citizen was active and neither account is blocked or deleted; otherwise it is not confirmed, with a reason.
- **FR-25** A referrer can earn at most the monthly limit of confirmed referrals. Referrals over the limit are not confirmed, with that reason.

### Wallet and points
- **FR-26** Points are whole numbers. Every change is an entry in the wallet's history; entries are never edited or deleted, only reversed by a new entry.
- **FR-27** The wallet shows the confirmed balance, the points on hold and the history.
- **FR-28** Points cannot be transferred or exchanged for cash. Until Bills & Recharges go live, they cannot be spent either. After that, confirmed points are used only as a discount on bills and recharges, within the Super Admin's limits (Bills and recharges, FR-23 to FR-29). The wallet and the terms say so.
- **FR-29** The Super Admin can correct a balance with a reason. The balance cannot go below zero.

### Notifications and emails
- **FR-30** The referrer gets a push when a friend joins with their code, and both get one when the points are confirmed or not confirmed.
- **FR-31** Pushes use the citizen's language. Their texts are editable in the console's notification texts, like the existing ones.
- **FR-32** Emails go only for codes and account notices (password changed, email changed, account deleted), never for marketing, in the citizen's language.

### Super Admin console
- **FR-33** A new page lists citizen accounts, referrals and wallet entries, with search and filters, CSV export, and the help actions of FR-16.
- **FR-34** The Settings page gets the wallet settings of section 7.
- **FR-35** Every Super Admin action on accounts, referrals and points is recorded in the audit log.

### Website
- **FR-36** The invite link opens the website's download section and shows the friend's referral code with a **Copy** button.
- **FR-37** The privacy policy and terms describe the account, the data collected (the email included), the 18+ rule and that points have no cash value and cannot be transferred. They link to the deletion page (FR-9).

### Language
- **FR-38** Every new screen, message, push and email is available in Telugu (default) and English.

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

Fixed rules, not settings: a referral code only at sign-up (D9), the password rule (D15), the code limits (FR-12) and the login lock (FR-8).

## 8. Fraud and abuse

Referral rewards attract fake accounts. Play Integrity is off for now, so the following rules carry the weight:
- One account per email and one per mobile number. Until the later stage (D17) the mobile number is not checked with a code, so the email code (D14) and the per-phone limits do more of the work. Gmail variants of one address count as one email (FR-11).
- At most 3 welcome bonuses per phone, for families (D10), and no referrer reward between accounts that signed up on the same phone. A phone with many emails and SIMs therefore earns little.
- Self-referral with the same mobile number or email refused.
- The pilot (D13) keeps early fraud, and its cost, to a few mandals.
- Codes limited per account, per email address, per phone and per internet address (FR-12). Logins locked after 5 wrong passwords (FR-8).
- Points held, and confirmed only after real use of the app (section 7).
- A monthly limit per referrer.
- The Super Admin reviews and can reject referrals, correct wallets and block accounts.
- Once the app is on Google Play, sign-up should also require the Play Integrity check.
- **Points will gain a rupee value** when Bills & Recharges go live. By then every citizen who pays has confirmed their mobile number (D17). Points earned by fake referrals during the wallet-only period would still become spendable, so the Super Admin should review referrals before that date, especially accounts that never confirm their number. The bills limits (Bills and recharges, section 7) cap what points can cover.

## 9. Privacy and legal

- **Personal data collected:**
  - the name the citizen types;
  - the mobile number (kept as a protected fingerprint plus the last 4 digits);
  - the email address, kept in full because codes are sent to it, shown to the Super Admin with most of it hidden, and never written to logs;
  - the password, kept only as a secure fingerprint;
  - the village, the 18+ confirmation and consent dates, and app-use days for the active rule.
- **Consent (DPDP):** India's Digital Personal Data Protection Rules were notified in November 2025, and their core duties (notice, consent, security, erasure) apply from about May 2027. The sign-up consent notice and account deletion are built now so the feature is ready. The sign-up form says why the mobile number and the email are needed, with a link to the privacy policy, before the account is created.
- **Children:** accounts are for 18 and over only (D5), so no parental-consent process is needed.
- **Account deletion:** Google Play requires apps that let people create an account to let them delete it, in the app and from a web page. The web page asks for the email or the mobile number and a code sent to the account's email, so only someone who can open that email can delete the account (D12). **This makes citizen accounts an exception to the "data is kept permanently" decision of 2026-11-14** (needs the owner's confirmation, Q8).
- **Money rules (RBI):** at the wallet's launch points are collect-only, with no cash value and no transfer, so they are not a payment instrument. With Bills & Recharges they become a **discount the client gives** on its own bill-payment service. The client pays the rest of the bill with its own money, and points never pay a biller and never become cash. A lawyer must confirm this before Bills & Recharges go live. Points that paid a third party directly would be a prepaid payment instrument, which needs an RBI licence.
- **Email, and later WhatsApp and SMS:** messages go only when the citizen asks for a code or changes the account. The email service receives the address and the message only to deliver it. In the later stage, Meta and the SMS provider receive the number and the code only to deliver them; business SMS in India must come from a sender registered on DLT, the operators' register of business senders (C3).

## 10. Non-functional requirements

- Works on budget Android phones on 2G/3G: small screens, few requests, no large downloads.
- Every permission is checked on the server.
- Codes, passwords, tokens, full email addresses and full mobile numbers are never written to logs or the audit log.
- Telugu displays correctly everywhere, emails included.
- Every permission rule has a test, as for the rest of the project.
- If the email service cannot be reached, sign-up and password reset ask the citizen to try again later; logging in and the rest of the app keep working.
- Emails are short and plain, come from an address on the client's domain, and are set up so that they do not land in spam (C7).

## 11. What the client needs to provide

| # | Item | Why |
|---|---|---|
| C1 | *(Later stage, D17)* A verified Meta Business account, a WhatsApp Business phone number and display name | To send codes on WhatsApp |
| C2 | *(Later stage, D17)* Approval of the WhatsApp code message (Telugu and English) | Meta approves the message before use |
| C3 | *(Later stage, D17)* DLT registration for SMS: the client's business on an operator's DLT portal, a 6-letter sender name (for example GRAMAC), the code message in Telugu and English approved there, and an account with an Indian SMS provider | To send codes by SMS. Each approval usually takes a few days; start together with C1, well before Bills & Recharges |
| C4 | Reward amounts and the name of the points, in Telugu and English | Section 7 |
| C5 | Updated privacy policy and terms (with legal review of the points wording: no cash value now, a discount on bills later) | Section 9 |
| C6 | A support phone or WhatsApp number for citizens' questions about points and accounts, and who answers it | D11, D16; listed as a statewide help line in the app |
| C7 | Email sending: a sender address on the client's domain (for example no-reply@gramaconnect.com), an email-sending service (for example Amazon SES or Zoho ZeptoMail, or the domain's own mail server), and the domain's SPF, DKIM and DMARC records | To send sign-up and password codes that reach the inbox, not spam. Needed for launch |

## 12. Open questions for the client

| # | Question | Our suggestion |
|---|---|---|
| Q1 | What are the points called? | "Grama points" / "గ్రామ పాయింట్లు" |
| Q2 | Reward and welcome amounts | 50 and 20 |
| Q3 | Is the welcome bonus also held until the new citizen is active? | Yes (assumed in this document) |
| Q4 | Do points expire? | No, in this version. Asked again for Bills & Recharges (its Q9), since points then have a value. |
| Q5 | Do we collect the citizen's email? | Answered 2026-11-25 (v5): yes, it is required at sign-up and confirmed with a code, and used only for codes and account notices (D1, D14). In v3 no email was collected. |
| Q6 | What will points buy later? | Answered 2026-11-25: a discount on bills and recharges, paid for by the client, after Bills & Recharges go live (D4; Bills and recharges D9 to D13). A lawyer confirms the model first. |
| Q7 | Should officials have wallets? | No, in this version |
| Q8 | Citizen accounts can be deleted, as an exception to "data is kept permanently". Agreed? | Yes; wallet history is kept without the person's details |
| Q9 | Keep a protected fingerprint of a deleted citizen's mobile number and email for a time, to stop repeat welcome bonuses? | Yes, for 1 year, subject to legal review |
| Q10 | After how many months without use should signing in ask **Is this your account?** | Withdrawn in v5: login needs the password, so someone given an old number cannot open the account. |
| Q11 | Which mandals for the pilot, and for how long? (D13) | 2 or 3 mandals the client's team can visit, for about a month, then widen |
| Q12 | In the later stage (D17), should citizens also be able to log in with a WhatsApp or SMS code instead of the password? | Not at first: confirm the number once and allow password reset by code. Add login by code only if many citizens keep forgetting their password. |

## 13. Acceptance criteria

- A new citizen can fill in the sign-up form and type the email code in under 3 minutes on a budget phone, in Telugu.
- A citizen without an account can still read, post and request services exactly as before.
- A sign-up with a mistyped email cannot finish. A mobile number or email that is already registered is refused, with **Log in** and **Forgot password?** offered.
- A citizen can log in with the email or with the mobile number. 5 wrong passwords lock that account's login from that place for 15 minutes, whichever of the two was typed.
- A citizen who forgot the password gets a code by email and sets a new password; their other logins end.
- An invite link carries the code to the website and into the app.
- Referral and welcome points show as on hold once the new citizen's account is created, and are confirmed or not confirmed automatically after the holding period.
- No self-referral or second welcome bonus works with the same mobile number, email or phone.
- Officials never see citizen accounts in their user lists. Citizen accounts cannot open any official screen, and the officials' login refuses them.
- The Super Admin can change every setting in section 7, review referrals, correct points, block accounts and give the help of FR-16; each action is in the audit log.
- A citizen can delete their account from the app, and from the web page with an email code; nobody can delete it from the web page without the code.
- Two family members can each sign up on one shared phone, with their own mobile number and email, and get the welcome bonus; a 4th account on that phone gets none.
- While the pilot runs, the wallet does not show for villages outside the pilot mandals, and sign-up there is refused by the server.

## 14. Phasing

| Step | What | Depends on |
|---|---|---|
| 1 | Backend: accounts, Citizen role, sign-up with the email code, login with the password, forgotten password, wallet and referrals, behind the on/off setting | C7 |
| 2 | App: sign-up, login, forgotten password, profile changes, wallet, invite, delete account; console page; website code display, deletion page and legal texts | Step 1, C4, C5 |
| 3 | Test on the qa server with the feature on; UAT | Steps 1–2 |
| 4 | Production: release the app, then switch the feature on for the pilot mandals (D13); widen after the pilot | Step 3, C6, Q11 |
| 5 | Later stage (D17): WhatsApp and SMS codes; every citizen confirms their mobile number once; a forgotten password can also be reset by WhatsApp or SMS | C1, C2, C3; Q12 |
| 6 | Bills & Recharges, with points as a discount ([Bills and recharges](../bills-recharges/requirements.md)) | Step 5, and the wallet live in production |

Effort and the work for each team are in [technical-implementation.md](technical-implementation.md), section 9.

## Sources

- RBI, Master Directions on Prepaid Payment Instruments (closed system PPIs): https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=12156
- Digital Personal Data Protection Rules, 2025 (PIB): https://static.pib.gov.in/WriteReadData/specificdocs/documents/2025/nov/doc20251117695301.pdf
- Google Play, account deletion requirements: https://support.google.com/googleplay/android-developer/answer/13327111
- Android Phone Number Hint (the phone offers its own SIM numbers): https://developer.android.com/identity/phone-number-hint
- Django password validation (the minimum length, common-password and similarity checks): https://docs.djangoproject.com/en/5.2/topics/auth/passwords/#password-validation
- Amazon SES pricing (an example email service): https://aws.amazon.com/ses/pricing/
- Later stage: WhatsApp Business Platform pricing: https://developers.facebook.com/docs/whatsapp/pricing
- Later stage: WhatsApp authentication templates: https://developers.facebook.com/docs/whatsapp/business-management-api/authentication-templates
- Later stage: Android SMS Retriever and SMS User Consent (an SMS code fills in without the SMS permission): https://developer.android.com/identity/sms-retriever
- Later stage: India's SMS rules and DLT registration (third-party guide): https://www.telerivet.com/blog/india-sms-compliance-trai-dlt-registration-and-tcccpr-guide
- Later stage: SMS code prices in India, 2026 (third-party guide): https://www.messagecentral.com/blog/sms-otp-pricing-india
- TRAI to the Supreme Court: a disconnected number is not given to someone new for at least 90 days: https://www.livelaw.in/top-stories/once-a-mobile-number-is-deactivated-it-is-not-assigned-to-a-new-user-for-90-days-trai-to-supreme-court-241506
