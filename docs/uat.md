# UAT checklist

For the QA engineer and the client's testers during user acceptance testing. Work through each role in order. Mark each line Pass or Fail, and for a failure write what happened and attach a screenshot.

## Before you start

1. Load the sample data on the UAT server: `python manage.py seed_sample_data`. It creates 2 districts with 2 mandals each and 3 villages per mandal, one official per role, and posts in every state.
2. Note the usernames and temporary passwords it prints for each role. Each official must choose a new password at first login. Invite and reset emails need SMTP on the UAT server; with the console email backend, ask the developer for the email from the server log.
3. Test the app on a low-cost Android phone (2 GB RAM) on mobile data, and once with the network set to 3G or slower.
4. Test the website on a phone (375 px wide) and a laptop (1280 px wide).
5. Test both languages wherever there is a language switch. Telugu is the default; every Telugu word must show correctly, with no boxes or broken letters.

## Citizen (mobile app, no login)

| # | Check | Expected |
|---|---|---|
| C1 | First launch | Splash, then language choice. Telugu and English both work. |
| C2 | Location allowed | The app finds your nearest village and shows it. |
| C3 | Location refused | The app offers State, District, Mandal, Village lists; picking a village opens its feed. |
| C4 | Feed | Only published news for your village, its mandal, its district and the state. Newest first. Nothing from other villages or districts. |
| C5 | Scrolling | More posts load as you scroll. No duplicates. |
| C6 | Offline | Turn on airplane mode and reopen the app. The last feed still shows. |
| C7 | Post detail | Photos load in low-data size, then full size on tap. Telugu text is correct. |
| C8 | Change village | The feed switches. Push alerts now come for the new village only. |
| C9 | Push alert | When an admin publishes a post for your village, an alert arrives. Tapping it opens the post. |
| C10 | Save, share, listen | Saved posts stay on the phone. Share opens WhatsApp and others. Listen reads the post aloud. |
| C11 | No login needed | At no point does the app ask a citizen to log in. |

## Panchayat Member (mobile app)

| # | Check | Expected |
|---|---|---|
| P1 | Invite email | When the Village Admin adds the member, the member gets an email (Telugu by default) with their username and a temporary password. |
| P2 | First login | Username (or email) and the temporary password work, and the app goes straight to "Change password". Nothing else opens until a new password is set. A weak password such as 12345678 is refused with a reason. |
| P3 | Wrong password | A wrong password and an unknown username give the same message. After 5 wrong tries the login waits 15 minutes. |
| P3a | Forgot password | The Village Admin taps "Reset password"; the member gets a new email, is logged out everywhere, and must change the password again. |
| P4 | Create post | Title, description, category, up to 5 photos. Photos upload quickly on mobile data. |
| P5 | After submitting | The post shows as Pending in "My posts". It is not in the citizen feed. |
| P6 | Sent back | When an admin sends it back, the comment shows and "Edit & resubmit" works. The post returns to Pending. |
| P7 | Discarded | The reason shows. The post cannot be edited. |
| P8 | Other villages | The member can post only to their own village. |
| P9 | No admin screens | No review queue, no user management. |

## Village Admin (mobile app)

| # | Check | Expected |
|---|---|---|
| V1 | Own post | Publishes straight away (auto-publish on). Citizens of the village see it. |
| V2 | Review queue | Shows pending posts from this village only. |
| V3 | Publish | The post appears in the citizen feed and the author is notified. |
| V4 | Recheck | Needs a comment. The author sees it. |
| V5 | Discard | Needs a reason. The author sees it. |
| V6 | Someone else decided first | Open the same pending post on two phones (Village Admin and Mandal Admin). Publish on one, then act on the other. The second gets "Already decided by …" and nothing changes. |
| V7 | Take down | A published post disappears from the feed. A reason is required. |
| V8 | Users | Can add, block and unblock Panchayat members of this village only. Cannot see members of other villages. |
| V9 | Block | The blocked member is logged out at once on their phone and cannot log in again. |

## Mandal Admin (mobile app)

| # | Check | Expected |
|---|---|---|
| M1 | Post to the whole mandal | Citizens of every village in the mandal see it. |
| M2 | Post to one village in the mandal | Only that village's citizens see it. |
| M3 | Review queue | Pending posts from every village in the mandal; nothing from other mandals. |
| M4 | Reverse a Village Admin's decision | Allowed (higher level). A Village Admin cannot reverse a Mandal Admin's decision. |
| M5 | Users | Can manage Village Admins and Panchayat members in the mandal; not other mandals, not other Mandal Admins. |
| M6 | Escalation alert | A post pending longer than the escalation time (24 hours by default) triggers an alert to the Mandal Admin. |

## District Admin (mobile app)

| # | Check | Expected |
|---|---|---|
| D1 | Post to the whole district | Every village in the district sees it. |
| D2 | Review queue | Everything pending in the district. |
| D3 | Users | Can manage Mandal Admins, Village Admins and Panchayat members in the district only. |
| D4 | Other district | Nothing from the other district is visible: posts, users or queue. |

## Super Admin (web console at /console/ and the app)

| # | Check | Expected |
|---|---|---|
| S1 | Console login | Username and password work for the Super Admin, including the forced change at first login. Any other official is refused with a clear message. |
| S1a | Default passwords | Settings shows a default password per role. Changing one is saved, appears in the audit log without the password, and the next invite for that role uses it. A weak default is refused. |
| S2 | Session | Reloading the page keeps you logged in. Logging out ends the session. |
| S3 | Dashboard | Counts match the data: total posts, pending, published today, active admins, blocked users. |
| S4 | Geography | Add, edit, deactivate and reactivate a district, mandal and village. A deactivated village disappears from the app's lists. |
| S5 | CSV import | A dry run reports errors and changes without saving. A real import adds villages. Running it again changes nothing. Villages without coordinates are listed. |
| S6 | Users | Create, block, unblock and reassign any official below Super Admin. |
| S7 | Posts | Filter by status, district and category. Publish, recheck, discard, take down and reverse work and show in the history. |
| S8 | Already decided | If another admin decided first, the console shows "Already decided" and refreshes the post. |
| S9 | Audit log | Every action above appears with who, when and where. Filters work. CSV export opens in Excel with Telugu intact. |
| S10 | Reports | Numbers match the posts. CSV export works. |
| S11 | Settings | Escalation hours, the English switch and push texts save. |
| S12 | Look | Every screen looks like the approved prototype. |

## Public website

| # | Check | Expected |
|---|---|---|
| W1 | Pages | Home, How it works, For officials, FAQ, Contact and Admin login open from the menu on phone and laptop. |
| W2 | Language | The EN / తెలుగు switch changes every text on the page. |
| W3 | Store buttons | Open the store pages once the app is published; before that a "coming soon" message shows. |
| W4 | Contact form | Name, 10-digit mobile and message are required. A sent message shows the thank-you panel and appears in Django admin. |
| W5 | Office details | Address, phone, email and hours are correct. |
| W6 | Admin login link | Opens the console login. |
| W7 | Look | Matches the approved prototype at 375 px and 1280 px. |

## Across the platform

| # | Check | Expected |
|---|---|---|
| X1 | Telugu | Names, posts, invite emails and CSV exports keep Telugu correctly everywhere. |
| X2 | Speed on 2G/3G | The feed's first page appears within a few seconds; photos are small. |
| X3 | Blocked user | Is logged out on every device within one request. |
| X4 | Data privacy | Passwords never appear in logs, the audit log or web addresses, and searches by email or mobile do not appear in the Nginx access log. |
