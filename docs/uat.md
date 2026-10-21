# UAT checklist

For the QA engineer and the client's testers during user acceptance testing. Work through each role in order. Mark each line Pass or Fail, and for a failure write what happened and attach a screenshot.

## Before you start

1. Load the sample data on the UAT server: `python manage.py seed_sample_data`. It creates 2 districts with 2 mandals each and 3 villages per mandal, one official per role, posts in every state, four posts with upcoming event dates, and one urgent post for Tenali mandal. It also prints a media check: whether uploads can be stored and whether ffmpeg is installed for video posts.
2. Note the usernames and temporary passwords it prints for each role; they are shown only once (`seed_sample_data --reset-passwords` prints new ones). Each official must choose a new password at first login. Nothing is emailed or texted: an admin hands over every temporary password in person.
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
| C10 | Save, share, listen | Saved posts stay on the phone. Share opens WhatsApp and others and sends the post text plus the website link. Listen reads the post aloud. |
| C11 | No login needed | At no point does the app ask a citizen to log in. |
| C12 | News search | Searching a word from a title or description finds the post, in Telugu and in English, whatever the letter case. One letter asks for at least two. |
| C13 | Video post | A video post shows its poster picture. It plays on tap, can be paused and moved forward or back, and is at most 60 seconds. |
| C14 | "Don't miss" | Posts with an event date from today on show soonest first: at most 10. Events that ended yesterday are gone. |
| C15 | Urgent banner | An urgent post from the last 7 days shows at the top. Its push alert arrives even when the phone is idle. |
| C16 | Help lines | The help-line screen lists 112, 108, 1930, 181, 1098 and 1912 with Telugu names. Tapping one opens the dialler. |

## Panchayat Member (mobile app)

| # | Check | Expected |
|---|---|---|
| P1 | New account | The Village Admin adds the member with a username and either types a temporary password or taps Generate. A generated password shows once, with Copy; it cannot be seen again. No email or SMS is sent. |
| P2 | First login | The username, in any letter case, and the temporary password work, and the app goes straight to "Change password". Nothing else opens until a new password is set. A weak password such as 12345678 or a 7-letter one is refused with a reason. The email address does not work as a login name. |
| P3 | Wrong password | A wrong password and an unknown username give the same message. After 5 wrong tries from the same phone the account is locked for 15 minutes, even with the right password, and the message says how long. |
| P3a | Unlock | While the member is locked out, the Village Admin sees "Unlock" for them and unlocking lets them log in at once. The audit log shows the lockout and the unlock. |
| P3b | Forgot password | The Village Admin resets the password: typed or generated, shown once. The member is logged out everywhere at once, the old password stops working, any lockout is cleared, and the member must change the password again. |
| P3c | Change password | From the profile, the member changes their own password with the current one. Their other phones are logged out. |
| P4 | Create post | Title, description, category, up to 5 photos. Photos upload quickly on mobile data. |
| P5 | After submitting | The post shows as Pending in "My posts". It is not in the citizen feed. |
| P6 | Sent back | When an admin sends it back, the comment shows and "Edit & resubmit" works. The post returns to Pending. |
| P7 | Discarded | The reason shows. The post cannot be edited. |
| P8 | Other villages | The member can post only to their own village. |
| P9 | No admin screens | No review queue, no user management. |
| P10 | Event date and video | An optional event (deadline, camp, distribution, closure or meeting) with a start and optional end date can be added. A video of at most 60 seconds can be attached instead of photos, never both. |
| P11 | No "urgent" | A Panchayat member cannot mark a post as urgent; the option is not offered. |

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
| V8 | Users | Can add, block, unblock, reset and unlock Panchayat members of this village only. Cannot see members of other villages, or any Village Admin, Mandal Admin or higher. |
| V9 | Block | The blocked member is logged out at once on their phone and cannot log in again. |
| V10 | Mark as urgent | The Village Admin can mark their own post or a pending or published post from the village as urgent, and clear it again. Both show in the audit log. |
| V11 | Visible to | Only the village is offered. |

## Mandal Admin (mobile app)

| # | Check | Expected |
|---|---|---|
| M1 | Post to the whole mandal | Citizens of every village in the mandal see it. |
| M2 | Post to one village in the mandal | Only that village's citizens see it. |
| M3 | Review queue | Pending posts from every village in the mandal; nothing from other mandals. |
| M4 | Reverse a Village Admin's decision | Allowed (higher level). A Village Admin cannot reverse a Mandal Admin's decision. |
| M5 | Users | Can manage Village Admins and Panchayat members in the mandal; not other mandals, not other Mandal Admins. The user list shows only people in the mandal. |
| M5a | No escalation | A Mandal Admin cannot give anyone the Mandal Admin role or higher, and cannot reset, block or unlock a Mandal or District Admin. |
| M6 | Escalation alert | A post pending longer than the escalation time (24 hours by default) triggers an alert to the Mandal Admin. |
| M7 | Visible to | The mandal and each of its villages are offered; never the district or the state. |

## District Admin (mobile app)

| # | Check | Expected |
|---|---|---|
| D1 | Post to the whole district | Every village in the district sees it. |
| D2 | Review queue | Everything pending in the district. |
| D3 | Users | Can manage Mandal Admins, Village Admins and Panchayat members in the district only. Cannot give the District Admin role, and cannot act on another District Admin. |
| D4 | Other district | Nothing from the other district is visible: posts, users or queue. |
| D5 | Visible to | The district, its mandals, and a village picked through the mandal are offered; never the state. |

## Super Admin (web console at /console/ and the app)

| # | Check | Expected |
|---|---|---|
| S1 | Console login | Username and password work for the Super Admin, including the forced change at first login. A District, Mandal or Village Admin or a Panchayat member is refused with "This console is for Super Admins only" and stays on the login page. A wrong password, a blocked account and a locked-out account each show their own message. |
| S1a | Temporary passwords | Add user and Reset password accept a typed temporary password or Generate one. A generated one shows once, with Copy, and never again. A weak one is refused with a reason. Settings has no default passwords. |
| S1b | Unlock | A user locked out after 5 wrong passwords shows "locked after failed logins" and an Unlock button in the user panel. Unlocking lets them log in at once. |
| S2 | Session | Reloading the page keeps you logged in. Logging out ends the session. After 30 minutes without activity the console returns to the login page with a short message. Changing your password from the profile menu keeps this session and logs out every other one. |
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
| S13 | Contact messages | Messages from the website show newest first, with New and Handled filters and a search. Marking one handled records who and when; opening it again clears that. Both show in the audit log. |
| S14 | Second roles | A second role for an official is added and removed in the user panel. The role list offers only roles below your own, and an admin may give a role only inside their own area. |
| S15 | Django admin | `/django-admin/` opens from the office network only. From any other network it answers 403. |

## Public website

| # | Check | Expected |
|---|---|---|
| W1 | Pages | Home, How it works, For officials, FAQ, Contact and Admin login open from the menu on phone and laptop. |
| W2 | Language | The EN / తెలుగు switch changes every text on the page. |
| W3 | Store buttons | Open the store pages once the app is published; before that a "coming soon" message shows. |
| W4 | Contact form | Name, 10-digit mobile and message are required. A sent message shows the thank-you panel and appears in the console's Contact messages page. |
| W5 | Office details | Address, phone, email and hours are correct. |
| W6 | Admin login link | Opens the console login. |
| W7 | Look | Matches the approved prototype at 375 px and 1280 px. |
| W8 | Phone picture | The home page's phone shows real published posts: a featured one with a photo and two more. |
| W9 | Page links | Each page has its own address. Opening it directly, or reloading, shows that page. |

## Across the platform

| # | Check | Expected |
|---|---|---|
| X1 | Telugu | Names, posts and CSV exports keep Telugu correctly everywhere. |
| X2 | Speed on 2G/3G | The feed's first page appears within a few seconds; photos are small. |
| X3 | Blocked user | Is logged out on every device and in the console within one request, and cannot log in again. |
| X4 | Data privacy | Passwords, temporary ones included, never appear in logs, the audit log or web addresses, and searches by email or mobile do not appear in the Nginx access log. The audit row for a contact message never shows the sender's name or number. |
