# UAT checklist

For the QA engineer and the client's testers during user acceptance testing. Work through each role in order. Mark each line Pass or Fail, and for a failure write what happened and attach a screenshot.

## Before you start

1. Load the sample data on the UAT server: `python manage.py seed_sample_data --allow-server` (on a server the command refuses without the flag; never on production). It creates 2 districts with 2 mandals each and 3 villages per mandal, one official per role, posts in every state, four posts with upcoming event dates, and one urgent post for Tenali mandal. It also prints a media check: whether uploads can be stored and whether ffmpeg is installed for video posts.
2. Note the usernames and temporary passwords it prints for each role; they are shown only once (`seed_sample_data --reset-passwords --allow-server` prints new ones). Each official must choose a new password at first login. Nothing is emailed or texted: an admin hands over every temporary password in person.
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
| C13 | Video post | A video post shows its poster picture. It plays on tap and can be paused and moved forward or back. A video of up to 60 seconds and 60 MB plays. |
| C14 | "Don't miss" | Posts with an event date from today on show soonest first: at most 10. Events that ended yesterday are gone. |
| C15 | Urgent banner | An urgent post from the last 7 days shows at the top. Its push alert arrives even when the phone is idle. |
| C16 | Help lines | The help-line screen lists 112, 108, 1930, 181, 1098 and 1912 with Telugu names. Tapping one opens the dialler. |
| C17 | Contacts tab | "For you" lists the numbers of your village, your mandal and your district, grouped by type (VRO, Mee Seva, Sachivalayam …) with the village's first; "Government helplines" lists the state's numbers. Nothing from a neighbouring village, mandal or district. Telugu and English both work. Tapping a number opens the dialler. A number an official removed is gone within 5 minutes. With no signal the last list still shows, and 112 and 108 always work. |
| C18 | Post news | Without logging in, a citizen sends a post with up to 3 photos, or one video (up to 60 MB and 60 seconds), or neither; a fourth photo, photos and a video together, or a video longer than 60 seconds are refused with a message. The post goes to review, for the phone's village only: there is no "Who will see it" choice, and the post never reaches another village's review queue. After the day's limit set in the console (Settings, 5 by default) the app says when they can post again. |
| C19 | Cancel a service request | In Profile → My service requests, a New, Approved or In progress request shows **Cancel request**. Cancelling asks for an optional reason, then the request shows as Cancelled and the button is gone. A Completed, Rejected or Cancelled request has no button. Another phone cannot cancel it. A cancelled request still counts towards the day's limit of requests. |

## Panchayat Member (mobile app)

| # | Check | Expected |
|---|---|---|
| P1 | New account | The Village Admin adds the member with a username, which may have spaces between words (`ravi kumar`), and either types a temporary password or taps Generate. A generated password shows once, with Copy; it cannot be seen again. No email or SMS is sent. |
| P2 | First login | The username, in any letter case and with extra spaces before, after or between its words, and the temporary password work, and the app goes straight to "Change password". Nothing else opens until a new password is set. A weak password such as 12345678 or a 7-letter one is refused with a reason. A password without a capital letter, a small letter, a number or a special character (for example `gram2026`) is refused, and the message names what is missing; `Gram@2026` is accepted. The email address does not work as a login name. |
| P3 | Wrong password | A wrong password and an unknown username give the same message. After 5 wrong tries from the same phone the account is locked for 15 minutes, even with the right password, and the message says how long. |
| P3a | Unlock | While the member is locked out, the Village Admin sees "Unlock" for them and unlocking lets them log in at once. The audit log shows the lockout and the unlock. |
| P3b | Forgot password | The Village Admin resets the password: typed or generated, shown once. The member is logged out everywhere at once, the old password stops working, any lockout is cleared, and the member must change the password again. |
| P3c | Change password | From the profile, the member changes their own password with the current one. Their other phones are logged out. |
| P4 | Create post | Title, description, category, and up to 3 photos (optional; lead's decision of 2026-11-13). A fourth cannot be added. Photos upload quickly on mobile data. |
| P5 | After submitting | The post shows as Pending in "My posts". It is not in the citizen feed. |
| P6 | Sent back | When an admin sends it back, the comment shows and "Edit & resubmit" works. The post returns to Pending. |
| P7 | Discarded | The reason shows. The post cannot be edited. |
| P8 | Other villages | The member can post only to their own village. |
| P9 | No admin screens | No review queue, no user management. |
| P10 | Event date and video | An optional event (deadline, camp, distribution, closure or meeting) with a start and optional end date can be added. One video of up to 60 seconds and 60 MB can be attached instead of photos, never both; a longer video is refused with a message. |
| P11 | No "urgent" | A Panchayat member cannot mark a post as urgent; the option is not offered. |
| P12 | Important numbers | The member adds, edits and removes numbers for their own village only, and citizens of the village see them. "Who sees it" offers only the village. Helplines can be read but not changed. |

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
| V12 | Important numbers | The Village Admin adds, edits, verifies and removes numbers for their own village; citizens see the change. Numbers of other villages, the mandal or the district cannot be seen or changed. "0863-223 4567" is saved as 08632234567; "12", "108" and "*#06#" are refused. A 10-digit mobile needs the consent tick. The same number twice for the same type shows "already listed". |

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
| S6a | Change a username | In the user panel, Edit details changes the official's username, also to one with spaces (`ravi kumar`). A name another official has, in any letter case, is refused with "This username is taken." The official stays logged in, and next time logs in with the new name only. The audit log shows the change with the old and new username. |
| S7 | Posts | Filter by status, district and category. Publish, recheck, discard, take down and reverse work and show in the history. |
| S8 | Already decided | If another admin decided first, the console shows "Already decided" and refreshes the post. |
| S9 | Audit log | Every action above appears with who, when and where. Filters work. CSV export opens in Excel with Telugu intact. |
| S10 | Reports | Numbers match the posts. CSV export works. |
| S11 | Settings | Escalation hours, the English switch and push texts save. |
| S12 | Look | Every screen looks like the approved prototype. |
| S13 | Contact messages | Messages from the website show newest first, with New and Handled filters and a search. Marking one handled records who and when; opening it again clears that. Both show in the audit log. |
| S14 | Second roles | A second role for an official is added and removed in the user panel. The role list offers only roles below your own, and an admin may give a role only inside their own area. |
| S15 | Django admin | `/django-admin/` opens from the office network only. From any other network it answers 403. |
| S16 | Important numbers | Area and Helpline tabs. The Super Admin adds, edits and removes statewide helplines (short codes such as 108 accepted) and any area's numbers. Numbers not checked for 180 days show "needs re-check"; Verify clears it. The website's help lines follow the statewide list. |
| S17 | Numbers CSV import | A dry run lists every bad row with its row number and field and saves nothing. One bad row means nothing is imported. A real import of a district's numbers works; importing it again changes nothing. A file saved from Excel with Telugu names imports correctly. |
| S18 | Contact types | Add a type, rename it in Telugu and English, and switch it off; a switched-off type's numbers disappear from the app. A type in use cannot be deleted. |
| S19 | More Super Admins | Users → Add user → role Super Admin asks for your own password; a wrong one shows "Your password is not correct." and creates nobody. The new Super Admin must change the password at first login and then sees the console. The other Super Admins get a notification. You can block, unblock and reset another Super Admin, never yourself. With 5 active Super Admins a sixth is refused with "The limit of Super Admins is reached." A District Admin never sees the Super Admin choice. |
| S20 | Cancelled service requests | After a citizen cancels a request, the official it was assigned to gets a notification ("… cancelled their … request"). Service requests has a Cancelled tab with a count; the request's history shows "Cancelled by the citizen" and the reason. Nobody can change a cancelled request any more: no buttons, and the console says it was cancelled if tried. |
| S21 | Blocked citizens | The Super Admin opens a citizen post and blocks the citizen for some days with a comment. The phone's posts keep their status: pending ones stay in Waiting for review and can be published or discarded; published ones stay published. The citizen is listed under the **Blocked** tab (one row per phone, also when blocked from a published post), with the end date, reason and who blocked; **Unblock** there lets the phone post again at once. When the time is up the citizen moves to the ended blocks, keeping the reason. A new post from the blocked phone is refused with the reason and the end date. "Discard all" still puts posts under Rejected. (Owner's request of 2026-11-21.) |
| S22 | Super Admin changes any decision | A Super Admin reverses their own discard and another Super Admin's take-down, with a comment; both show in the history. A District, Mandal or Village Admin cannot reverse a Super Admin's decision. (Owner's decision of 2026-11-21.) |

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

## Citizen wallet: before you start (BVTGC-145)

The wallet is an optional citizen account for points and referrals (BackEnd `docs/features/citizen-wallet/requirements.md`). The test plan is `docs/features/citizen-wallet/test-plan.md`. Run these on the qa server.

1. **Email works on qa** (BVTGC-143): a test code reaches a Gmail inbox, not spam. Have at least two Gmail addresses and one address at another provider, all yours.
2. **Phones:** at least two budget Android phones with the new app build, one with two SIM cards. For the family checks, one phone is shared by three test accounts.
3. **Console settings** (Settings → Citizen wallet), as the Super Admin:
   - switch the wallet **on**;
   - add the mandal of your test village to **Pilot mandals** (leave one other village outside it);
   - set the **holding period to 1 day** and the **active days to 2**, so a referral is decided the next day: confirmed when the friend opened the app on both days, not confirmed when they did not. Put the suggested values (7 and 2) back after testing.
4. **Language:** do each flow once in Telugu (the default) and once in English. Every Telugu word, in the app, the emails and the console, must show correctly.
5. Write down each test account's mobile number and email; delete the accounts at the end (CW25, CW26).

## Citizen wallet: citizen (mobile app)

| # | Check | Expected |
|---|---|---|
| CW1 | No account | A citizen who never signs up reads news, posts and asks for services exactly as before; nothing asks them to log in. Home shows a **Sign in to earn points** card only. |
| CW2 | Switched off, or outside the pilot | With the wallet off in the console, the card is gone within 5 minutes (after reopening the app). In a village outside the pilot mandals the card does not show, and an invite link says the wallet is coming to that area soon. |
| CW3 | Create account form | Name, mobile number, email, password, the password again, a friend's code (optional), **I am 18 or older** and **I agree**. On Android, tapping the mobile field offers the phone's SIM numbers; picking one fills the 10 digits. Different passwords are caught by the app. A 7-character password, `12345678`, or the citizen's own name or number as the password is refused with a reason; 8 characters of any kind otherwise work. Without the 18+ tick the account is refused. |
| CW4 | Check your email | A code email arrives within a minute, in the citizen's language, with the code in the subject; the screen shows the address half hidden (`r***@gmail.com`) and reminds about the spam folder. A wrong code says how many tries are left; after 5 wrong tries the code is dead and a new one is needed. **Send again** works only after 60 seconds; a sixth code in one day is refused. **Change email** goes back to the form. |
| CW5 | Account created | After the right code the citizen is logged in. The wallet shows 0 points and the citizen's own referral code. Closing and reopening the app keeps them logged in. |
| CW6 | Already registered | Signing up again with the same mobile number, or the same email, is refused with **Log in** and **Forgot password?**. A Gmail address with dots or a `+` part (`lakshmi.devi+x@gmail.com`) counts as the same email as `lakshmidevi@gmail.com`. |
| CW7 | Log in | **Log in** works with the email and with the mobile number (also typed with `+91` or spaces), and the password. A wrong password and an unknown login give the same message. After 5 wrong passwords from one phone the account waits 15 minutes, whichever of the two was typed, and the message says how long. |
| CW8 | Forgot password | **Forgot password?** gives the same message whether or not the email or number is registered. The code arrives; a new password typed twice works; the citizen's other phone is logged out; an email says the password was changed. |
| CW9 | Officials' login | The account's email or number and password at the officials' login (app or console) are refused as a wrong password. |
| CW10 | Change password | From Profile, with the current password: other phones are logged out; an email notice arrives. A wrong current password is refused. |
| CW11 | Change email | The new email and the password: a code goes to the new address; after it, the account uses the new email and the old address gets an "email changed" notice. An email another account uses is refused, pointing to the help line. |
| CW12 | Change mobile number | The new number and the password. A number another account uses is refused, pointing to the help line. Login with the new number works; the old one no longer does. |
| CW13 | Change village | Choosing another village in the app moves the account with it; the wallet and points stay. |
| CW14 | Log out | **Log out** returns to the app without an account; village and saved news stay. Wallet pushes stop reaching the phone. |
| CW15 | Invite | The wallet's **Invite** shares a message with a link (WhatsApp first). On another phone the link opens the website's download section with the code and **Copy**; the app, opened from the link, fills the code into Create account. A code can only be given at sign-up. A citizen's own code, or the code of a blocked or deleted account, is refused. |
| CW16 | Points on hold | When the friend signs up with the code, both wallets show the points **on hold** (50 and 20 with the suggested amounts) with the date they are expected, and the inviter gets a push "… joined Grama Connect with your invite code". |
| CW17 | Confirmed | With the qa settings of "Before you start", the friend opens the app on both days: within an hour after the hold, both points are **confirmed**, the balances grow, and both phones get a push. |
| CW18 | Not confirmed | A second friend signs up with the code but does not open the app the next day: after the hold, both entries show **not confirmed** with the reason ("the new citizen did not use the app on enough days"), and both get a push. |
| CW19 | Family phones | Two family members sign up on one shared phone, each with their own number and email: each gets the welcome points; a referral between the two pays no inviter reward (the history says why). A fourth account made on that phone gets no welcome points, with the reason shown. |
| CW20 | No second welcome bonus | After deleting an account (CW25), a new account with the same mobile number or email gets no welcome points again. |
| CW21 | Wallet screen | The confirmed balance, the points on hold with their date, and the history (date, reason, points, status) in the citizen's language. "Points have no cash value" shows. **Questions about points?** opens the help-line numbers, where the support number is listed. |
| CW22 | Number removed by the Super Admin | After the Super Admin removes the number (CW31), the app asks for a mobile number before anything else; adding one with the password works. |
| CW23 | Blocked | When the Super Admin blocks the account (CW32), the phone is logged out at once; logging in says the account is blocked. |
| CW24 | Slow network | Sign-up, login and the wallet work on 3G; a lost connection shows "no internet", never a crash, and the form keeps what was typed. |
| CW25 | Delete in the app | **Delete my account** asks for the password and an "I understand" tick and says the points will be lost. After it: the app is a plain citizen app again, an "account deleted" email arrives, logging in fails, and the same number and email can sign up again. |

## Citizen wallet: website

| # | Check | Expected |
|---|---|---|
| CW26 | Delete my wallet account | The website's deletion page asks for the email or mobile number, then sends a code to the account's email; the code and "I understand" delete the account, and the confirmation and the "account deleted" email follow. An unknown or blocked login gets the same message as a registered one, and no email. A code from **Forgot password** does not work here; a wrong code says how many tries are left. |
| CW27 | Friend's code | The invite link opens the download section with the friend's code and **Copy**. A link with a code that is not 8 letters and digits shows no box. |
| CW28 | Legal texts | The privacy policy and terms describe the account, the data collected (with the email), the 18+ rule, that points have no cash value, and link to the deletion page, in Telugu and English. |

## Citizen wallet: Super Admin (console and app)

| # | Check | Expected |
|---|---|---|
| CW29 | Citizens & wallet | The page lists the accounts newest first; search by name (Telugu or English), the last 4 digits or a referral code; filters by village and by status (active, blocked, deleted). No screen shows a full email or mobile number: the last 4 digits and `r***@gmail.com` only. |
| CW30 | Find a caller's account | **Find by mobile or email** with the full number or email a caller gives shows only an exact match; the audit log records the search with the last 4 digits or the masked email only. |
| CW31 | Help after a call-back | **Set new email** and **Remove mobile number** work only after ticking "I called the number back" and typing a reason. After a new email the citizen uses **Forgot password**, and its code goes to the new address; the old address gets a notice. After removing the number, another account can use it. |
| CW32 | Block, unblock, delete | Each needs a reason. Block logs the citizen out at once; Unblock lets them log in; Delete removes the name, email and number ("Deleted citizen", no actions left) and emails the notice. |
| CW33 | Referrals | Pending, Confirmed and Rejected tabs with counts; search by either citizen. **Confirm** and **Reject** need a comment; a rejection's comment shows in both citizens' wallets. Deciding one the hourly job or another Super Admin already decided says so and refreshes the row. |
| CW34 | Adjust points | Add or take away whole points with a reason, which the citizen sees in the wallet. Taking away more than the confirmed balance is refused ("Only … confirmed points"). |
| CW35 | Wallet entries and CSV | The Wallet entries tab lists every entry with filters (kind, status, dates). **Export CSV** for referrals and for entries opens in Excel with Telugu names correct and only last 4 digits and masked emails. |
| CW36 | Settings | The wallet card saves the switch, the pilot mandals and the amounts and limits. A value out of range, an unknown mandal, or more active days than the holding period is refused under its field. Phones follow a change within about 5 minutes. |
| CW37 | Push texts | Notification texts lists three wallet texts (a friend joined, points confirmed, points not confirmed) for "The citizen's wallet account"; an edit shows on the next push. |
| CW38 | Audit log | Every action above shows, with its label in Telugu and English, and never a full email, number, password or code. |
| CW39 | Works while off | With the wallet switched off, the Citizens & wallet page, exports and deletions still work. |

## Citizen wallet: other officials

| # | Check | Expected |
|---|---|---|
| CW40 | No citizens for officials | District, Mandal and Village Admins and Panchayat members see no citizen accounts in Users, no wallet pages, and dashboard counts of officials only. Asking the wallet's admin addresses with their login is refused. |

## Citizen wallet: emails and pushes

| # | Check | Expected |
|---|---|---|
| CW41 | Emails | Every email (the code for sign-up, forgotten password, new email and deletion; the password-changed, email-changed and account-deleted notices) comes from the client's sender address, in the citizen's language, lands in the inbox, has no links, and says replies are not read. |
| CW42 | Pushes | Wallet pushes come in the citizen's language and open the wallet when tapped. None reaches a blocked or deleted account, or a phone that logged out. |

## Across the platform

| # | Check | Expected |
|---|---|---|
| X1 | Telugu | Names, posts and CSV exports keep Telugu correctly everywhere. |
| X2 | Speed on 2G/3G | The feed's first page appears within a few seconds; photos are small. |
| X3 | Blocked user | Is logged out on every device and in the console within one request, and cannot log in again. |
| X4 | Data privacy | Passwords, temporary ones included, never appear in logs, the audit log or web addresses, and searches by email or mobile do not appear in the Nginx access log. The audit row for a contact message never shows the sender's name or number. |
| X5 | Wallet data privacy | A citizen wallet account's full email, mobile number, password and codes never appear in the console, CSV exports, the audit log or web addresses; only the last 4 digits and a masked email (`r***@gmail.com`). |
