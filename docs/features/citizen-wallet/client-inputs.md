# Citizen wallet: what we need from you before the launch

For the client's team. Jira BVTGC-144. Prepared on 2026-11-25.

The wallet is built and switched off. Before we switch it on for the first mandals, we need your answers below. Where we suggest something, that is what the wallet does today: if you agree, just write "Agreed". Most answers are settings or texts, so we can change them later without a new app version.

Please return this list to Admin, filled in, before the testing on the qa server ends (BVTGC-146).

## 1. The points

| # | Question | What the wallet does today | Your answer |
|---|---|---|---|
| Q1 | What are the points called, in Telugu and in English? | "గ్రామ పాయింట్లు" / "Grama points" | |
| Q2 | How many points does a citizen get for each friend who joins, and how many does the friend get? | 50 for the citizen who invited, 20 for the friend | |
| Q3 | Should the friend's welcome points also wait until the friend has used the app for a few days? | Yes: both wait 7 days, and need the friend to open the app on 2 of them | |
| — | How many days do points wait, and on how many of them must the friend open the app? | 7 days; 2 days of use | |
| — | How many friends can one citizen earn points for in a month? | 30 | |
| — | How many accounts made on one phone can get the welcome points? (For families sharing a phone.) | 3 | |
| Q4 | Do points expire? | No. We ask again before Bills & Recharges, when points get a value. | |
| Q7 | Should officials (admins and Panchayat members) also get wallets? | No | |

Points have no cash value and cannot be transferred. The app and the terms say so.

## 2. The pilot

| # | Question | What we suggest | Your answer |
|---|---|---|---|
| Q11 | Which mandals start first, and for how long before we open it to more? | 2 or 3 mandals your team can visit, for about a month | |
| — | Who in your team watches the first weeks with us (sign-ups, points, support calls)? | One person, checking the console daily | |

## 3. Support for citizens

| # | Question | Why we need it | Your answer |
|---|---|---|---|
| C6 | A phone or WhatsApp number citizens can call about their points or their account, and who answers it | The app shows it under the help-line numbers. The person who answers checks the caller by calling the account's mobile number back, then can set a new email or free a number taken by someone else. They never see or set a password. | |
| — | The hours that number is answered | Shown next to the number | |

## 4. Privacy policy and terms

| # | Question | What we suggest | Your answer |
|---|---|---|---|
| C5 | Updated privacy policy and terms, checked by your lawyer | We have draft texts that cover: the optional account, the data collected (name, mobile number, email, village), accounts for people aged 18 or over only, points with no cash value, and how to delete the account (in the app, or on the website with a code sent to the email). We send them to you for review. | |
| Q9 | When a citizen deletes their account, we keep a protected fingerprint of the mobile number and the email for one year, only to stop the same person collecting welcome points again. Is one year right? | 1 year, if your lawyer agrees | |

## 5. Email for the codes

Citizens confirm their email with a 6-digit code, and get codes for a forgotten password and for deleting the account on the website. We send nothing else: no newsletters, no offers.

| # | Question | What we suggest | Your answer |
|---|---|---|---|
| C7 | The sender address, on your own domain | `no-reply@<your domain>` | |
| C7 | The email-sending service | Amazon SES (Mumbai) or Zoho ZeptoMail, or your domain's own mail server. Your account, in your name. Cost on SES is about US$0.10 per 1,000 emails. | |
| C7 | Your domain admin adds three DNS records our server team will send (SPF, DKIM and DMARC) | Without them the codes may land in spam | Domain admin's name and contact: |

## 6. Later, before Bills & Recharges (not needed for the launch)

To send codes by WhatsApp and SMS later (Jira BVTGC-148), you will need a verified Meta Business account with a WhatsApp number (C1), Meta's approval of the code message (C2), and a DLT registration with a 6-letter SMS sender name and an Indian SMS provider (C3). Each approval takes days or weeks, so it is worth starting soon. We will send a separate list.

## After your answers

Admin records each answer in the requirements (section 12), changes the settings in the console, and puts the texts in the app and the website. Anything not answered by the launch keeps the suggestion above, and can be changed later.
