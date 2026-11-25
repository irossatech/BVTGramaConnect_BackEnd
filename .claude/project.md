# BVT Grama Connect: project rules

This file is identical in all three repos. Change it in all three at once, or not at all.

## The product

A village news platform for Andhra Pradesh. Panchayat members and admins post village news; admins review it; citizens read verified news for their village in Telugu or English without logging in.

| Product | Who uses it | Repo |
|---|---|---|
| Mobile app, Android and iOS | Every role, including citizens | BVTGramaConnect_Mobile (Flutter) |
| Public website, no login | Citizens and visitors | BVTGramaConnect (React, Next.js), pages `src/pages/index.tsx` and `src/pages/[page].tsx`, code in `src/website/` |
| Super Admin console | Super Admins only | BVTGramaConnect (React, Next.js), page `src/pages/console/index.tsx`, code in `src/console/` |
| REST API, Django admin, background jobs, deployment | All three clients | BVTGramaConnect_BackEnd (Django) |

Owners: the backend team owns BVTGramaConnect_BackEnd, the mobile team owns BVTGramaConnect_Mobile, and one web developer owns BVTGramaConnect (website and console). On the server everything sits on one domain: the website at `/`, the console at `/console/`, the API at `/api/v1/`, uploaded photos at `/media/`.

## Sources of truth

All in the BackEnd repo:
- `docs/api-contract.md`: every endpoint, object, error code and permission rule. Clients code against it; the backend team changes it first and announces the change.
- `docs/prototype-inventory.md`: what the approved prototypes contain, how the web parts are ported, the mobile design tokens, and the open questions with their defaults (section 8).
- `docs/uat.md`: the role-by-role acceptance checklist.
- `docs/security-review.md`: security status and open items.
- `deploy/`: server setup, deploy, backup and restore.

The approved design is the prototype folder "BVT Grama Connect mobile prototype" (ask the project manager for the zip). The approved mobile file is `Grama Connect Mobile V2.dc.html`. It is read-only reference: never edit it, and do not copy it into a repo. In the usual folder layout it sits two folders above each repo: `../../BVT Grama Connect mobile prototype`.

## Porting rules

- Website and console: ported as is from the prototypes. Keep the look, page structure, CSS, assets, fonts and behaviour; only the mock data is replaced with data from the API. No redesign. They run on React and Next.js since 2026-10-22 (owner decisions below).
- Mobile app: rebuilt in Flutter for Android and iOS, matching the prototype's screens, colours, typography and flows as closely as possible.

## Roles

A user can hold several role assignments, each tied to one area. Areas: State > District > Mandal > Village. Each role is a Django group, and what it may do is a set of Django permissions (BackEnd repo, `docs/authorization.md`).

| Role | Area | Posts | Reviews | Creates and blocks |
|---|---|---|---|---|
| SUPER_ADMIN | Whole platform | While SUPER_ADMIN_CAN_POST is on (the default) | Every post | Other Super Admins (at most MAX_SUPER_ADMINS), District, Mandal, Village Admins, Panchayat members |
| DISTRICT_ADMIN | District | Yes | Posts in the district | Mandal Admins, Village Admins, Panchayat members in the district |
| MANDAL_ADMIN | Mandal | Yes | Posts in the mandal | Village Admins, Panchayat members in the mandal |
| VILLAGE_ADMIN | Village | Yes | Posts in the village | Panchayat members in the village |
| PANCHAYAT_MEMBER | Village | Yes, goes to review | No | No |
| Citizen | Chosen village, no login | Yes, from the app, goes to review | No | No |

Only holders of the console permission, the Super Admin group, can use the web console.

## Posts

States: DRAFT > PENDING > PUBLISHED, RECHECK or DISCARDED; RECHECK > PENDING; PUBLISHED > UNPUBLISHED (shown as "Taken down"). BLOCKED is kept only for citizen posts a block closed from 2026-11-20 to 2026-11-21; a block changes no post since.
- Who will see it: every official picks Everyone, or any mix of districts, mandals and villages anywhere, in one post (1 to POST_MAX_TARGETS, 200 areas). A citizen's post is always for the phone's village.
- Panchayat and citizen posts go to PENDING. District, Mandal and Village Admins' posts publish at once while ADMIN_POSTS_AUTO_PUBLISH is on, when every area they are for lies inside the admin's own area; one area outside it, or Everyone, and the post goes to review (2026-11-25).
- A pending post is reviewed by the admins of every area it is for (and the areas above), and by the Super Admin. A post to Everyone is reviewed by Super Admins only.
- The first reviewer's decision wins. A later reviewer gets 409 `already_decided` naming who decided.
- Only a strictly higher level can reverse a decision, except that a Super Admin may reverse any decision, another Super Admin's and their own included.
- Recheck, discard, take-down and reverse need a comment.
- Every change writes a review record and an audit entry.
- Pending posts older than REVIEW_ESCALATION_HOURS alert the next level up.
- The citizen feed shows published posts for the village, its mandal, its district and the state.

## Configurable rules

Django settings, overridable by environment variables:
ADMIN_POSTS_AUTO_PUBLISH=True, REVIEW_FIRST_DECISION_WINS=True, REVIEW_ESCALATION_HOURS=24, SUPER_ADMIN_CAN_POST=True, MAX_PHOTOS_PER_POST=3 (or one video instead). Languages: Telugu (default) and English. A changed password at first login is always required; it is not a setting.

## Non-negotiables

- Every permission check runs on the server: the role allows the action AND the target sits inside the actor's area. Clients only hide buttons. Posting is the one exception: officials may choose any area, and a post for any area outside their own is reviewed instead of going live at once (2026-11-25).
- Blocking a user ends all their sessions at once.
- Built for budget Android phones on 2G/3G: small paged responses, photos stored as WebP (1280 px and a 320 px thumbnail), cached feed in the app.
- Telugu displays and stores correctly everywhere: API, database, emails, CSV exports, app, website.
- Tests for every permission rule.
- No personal data in URLs or logs; never log passwords or tokens.

## Owner decisions since the original brief

- 2026-10-21 · **Username and password login** with Django's built-in auth. Nothing is sent by text message or email for login or passwords.
- 2026-10-21 · **Temporary passwords, handed over in person.** The admin who creates an account or resets a password sets a temporary password or has the server generate one, shown once. The person must change it at first login. Email invites and default passwords per role are gone.
- 2026-10-21 · **Email is optional** contact information only. The username, unique and case-insensitive, is the only login name.
- 2026-10-21 · **Groups and permissions.** Roles are Django groups, actions are Django permissions, and each role assignment limits them to one area and the areas below it. The mapping is one dict in `apps/accounts/role_matrix.py`; `python manage.py sync_role_permissions` applies it. See the BackEnd repo's `docs/authorization.md`.
- 2026-10-21 · **Lockout:** 5 failed logins for one username from one IP address lock it for 15 minutes (django-axes), and since 2026-11-25 also 20 an hour from any addresses (see below). An admin ranked above the user can unlock early. Lockouts and unlocks are audited.
- 2026-10-21 · **Console login** uses a Django session with CSRF and a 30-minute idle timeout. The app keeps JWT. The API stays under `/api/v1/`.
- 2026-10-21 · **Tests run on SQLite**, Django's default database. The server uses MySQL (utf8mb4) or PostgreSQL through DATABASE_URL.
- 2026-10-21 · ~~**Web parts keep the prototype's technology.**~~ Replaced on 2026-10-22, see below.
- 2026-10-21 · **One web repo.** The website and the console live together in BVTGramaConnect (the console at `/console/`), owned by one web developer. The separate Admin repo was removed. One `npm run build` builds both.
- 2026-10-21 · **Server database not chosen yet.** Everyone develops and tests on SQLite until the owner picks MySQL or PostgreSQL.
- 2026-10-21 · **Escalation:** one time for every level (REVIEW_ESCALATION_HOURS, 24 by default).
- 2026-10-21 · **Language:** a post is written in one language, Telugu or English.
- 2026-10-21 · ~~**Visibility:** "Visible to" up to the author's own level, one target per post.~~ Replaced on 2026-10-28, see below.
- 2026-10-21 · **Event date:** posts may carry an optional event (deadline, camp, distribution, closure or meeting) with a start date and an optional end date.
- 2026-10-21 · **Video posts are in version 1**, at most 60 seconds (any length from 2026-11-13, at most 60 seconds again since 2026-11-14, see below).
- 2026-10-21 · **Also in version 1:** "mark as urgent" for admins, news search in the citizen feed, and a help-line numbers screen in the app.
- 2026-10-21 · **Second roles:** any admin with the matching manage permission for that area may add lower roles, in the console or through the API. This replaces the earlier console-only rule.
- 2026-10-21 · **Mobile app work for the new permissions is postponed.** The API keeps the role keys and the `capabilities` block in `/me` so the current app keeps working.
- 2026-10-21 · **Master data:** only the Super Admin adds or edits villages and mandals; coordinates come from the console and the CSV import. **Exports** are CSV only.
- 2026-10-21 · **Website:** the home page's phone picture shows live posts, every page has its own URL, and contact messages are read in the console.
- 2026-10-21 · **Sharing a post** sends its text plus a link to the website. There is no public post page.
- 2026-10-21 · **Console:** "Reset password" sits in the user panel, not in the users table.
- 2026-10-21 · ~~**/django-admin/** is reachable on the server only from office IP addresses.~~ Replaced on 2026-11-11, see below.
- 2026-10-22 · **Web is React and Next.js; mobile is Flutter.** The website and the console are one Next.js app (React, Pages Router) in BVTGramaConnect, exported as static files and served by Nginx; the mobile app stays Flutter. The conversion kept the approved design pixel for pixel.
- 2026-10-27 · **Citizens post from the app** without logging in. Each install gets a posting token after Google Play Integrity confirms the genuine app. Every citizen post goes to review; 5 posts per phone a day by default, which the Super Admin can change. BackEnd repo, `docs/api-contract.md`, section 15.
- 2026-10-28 · **Who will see it:** every poster, ~~citizens included,~~ picks Everyone or any districts, mandals and villages anywhere, in any mix, in one post. ~~Admins' posts still publish at once wherever they go;~~ (replaced on 2026-11-25, see below) the admins of every chosen area review the others. This replaces the 2026-10-21 visibility rule. BackEnd repo, `docs/api-contract.md`, section 5. Citizens: replaced on 2026-11-19, see below.
- 2026-11-04 · **The Super Admin may post.** SUPER_ADMIN_CAN_POST is True by default; setting it to False removes "New post" again.
- 2026-11-11 · ~~**No limit on photos or videos per post.**~~ Replaced on 2026-11-13, see below.
- 2026-11-11 · **Django admin is off on every server** (lead's decision), qa included: `/django-admin/` answers 404 there. It stays on developers' laptops. Officials, roles and settings are managed in the console. This replaces the 2026-10-21 office-IP rule.
- 2026-11-13 · **Up to 3 photos or one video per post** (lead's decision), never both, for everyone who posts: citizens, Panchayat members, admins and the Super Admin. Photos are optional, at most 10 MB each; the video at most 60 MB, ~~of any length~~ (at most 60 seconds since 2026-11-14, see below). MAX_PHOTOS_PER_POST is 3. Citizen posts per day stay the Super Admin's setting in the console (Settings). Banner ads are unchanged.
- 2026-11-14 · **Post videos at most 60 seconds** (lead's decision), for everyone who posts: citizens, Panchayat members, admins and the Super Admin; still at most 60 MB, one video or up to 3 photos, never both. VIDEO_MAX_SECONDS is 60; the server answers `400 video_too_long`, and the console and the app refuse a longer video before sending it. **Banner ad videos have no time limit**, only the 60 MB size limit (ADS_VIDEO_MAX_SECONDS 0). This replaces "any length" of 2026-11-13 for posts.
- 2026-11-14 · **Telugu name suggestions stay on Google** (lead's decision, web audit WEB-DP-002). Names typed in the app's and the console's forms (users, important numbers, places, services) keep going from this server to Google Input Tools for their Telugu spelling (`TRANSLITERATION_PROVIDER`). Names are not treated as sensitive like mobile numbers; mobile numbers, emails and addresses never go there.
- 2026-11-14 · **Data is kept permanently** (lead's decision, web audit WEB-DP-003). Contact messages, service requests, citizen phone records, posts and the audit log are never deleted; ~~screens show only recent ones~~ screens show all of them, however old, since 2026-11-20 (see below). Only expired sessions and tokens, failed background jobs, old failed-login records and read notifications are cleaned up.
- 2026-11-17 · **Usernames may have spaces, and admins can change them** (owner's request). A username may hold single spaces between words, such as `ravi kumar`; login ignores capitals and extra spaces. An admin who manages an official can change their username in the console's Edit details; it is audited, the official stays logged in and uses the new name next time. Officials cannot change their own username.
- 2026-11-18 · **Citizens can cancel their own service request** (owner's decision), from the phone that sent it, while it is New, Approved or In progress, with an optional reason. Cancelled is a final status: officials cannot change it, and they cannot cancel requests themselves. The request is kept, never deleted, and still counts towards the daily limit; the official it was assigned to is told.
- 2026-11-19 · **A citizen's post is for their own village only** (owner's decision). Citizens no longer pick "Who will see it": every citizen post goes to the village set on the phone and is reviewed by that village's admins (and the levels above). Areas sent by an older app are ignored. Officials still pick any areas. This replaces "citizens included" of 2026-10-28.
- 2026-11-19 · **Passwords: at least 12 characters for every official**, Panchayat members included; it was 8 for them since G12 of 2026-11-03. Still with a capital letter, a small letter, a number and a special character. A member whose password is shorter logs in once more and must choose a new one at once; nobody is locked out.
- 2026-11-19 · ~~**Audit log: the last 180 days by default, any older days by date**~~ (lead's decision). The audit screens show the last 180 days, in pages; an admin who picks dates or a month sees exactly those days, however old, read from the database. Exports still stop at 50,000 entries ("Choose a shorter date range"). This replaces the 2026-11-11 window (90 days by default, never more than 180 days back). Replaced on 2026-11-20, see below.
- 2026-11-19 · ~~**Contact messages: unhandled ones stay until handled.**~~ The console lists every unhandled website contact message, however old, until someone marks it handled; a handled one is listed for 90 days after it was handled, then kept but not shown. The same rule service requests follow (open ones always). Before, every message dropped off 90 days after it arrived, even unanswered. Replaced on 2026-11-20, see below.
- 2026-11-20 · **Nothing is hidden by age** (owner's decision). The audit log, website contact messages and service requests list every record, however old, page by page: the Super Admin sees all of them, and every other official sees all of their own area. Dates and filters only narrow a list. The audit export still stops at 50,000 entries ("Choose a shorter date range"). The website contact form has no limit per sender any more (it was 5 an hour). This replaces the windows of 2026-11-11 and 2026-11-19.
- 2026-11-20 · ~~**Blocked citizen posts have their own status** (owner's decision). When the Super Admin blocks a citizen from a post, every pending post of that phone becomes Blocked (BLOCKED), no longer Discarded, and the console and the Super Admin's app list them under a Blocked tab beside Rejected. Published posts of the phone stay published; "Discard all" still discards. The citizen's app still shows these posts as discarded, with the block comment as the reason.~~ Replaced on 2026-11-21, see below.
- 2026-11-21 · **A block only stops new posts** (owner's decision). Blocking a citizen's phone stops its new posts until the block ends; it changes no post: waiting posts stay Pending and are reviewed as usual, published ones stay published. "Discard all" stays a separate action. The posts earlier blocks had closed (Blocked, or Discarded before 2026-11-20) went back to Pending once. The Blocked tab lists blocked phones, one row per phone, with every past block and how it ended (BackEnd repo, `docs/api-contract.md`, section 15). This replaces the 2026-11-20 decision.
- 2026-11-21 · **A Super Admin may change any decision** (owner's decision), another Super Admin's and their own included; a comment is still required, and recheck is still refused for a citizen post. Everyone else keeps the rule that only a strictly higher level can reverse.
- 2026-11-25 · **An admin's post outside their own area is reviewed** (Admin's decision, BVTGC-26). A District, Mandal or Village Admin's post still goes live at once when every area it is for lies inside the admin's own area. One area outside it, or Everyone, and the whole post goes to review: the admins of those areas (and above) review it, and the Super Admin reviews a post for Everyone. The Super Admin is not affected. The app tells the admin before sending (BVTGC-104; BackEnd `POST /posts/targets/check/`). This replaces "Admins' posts still publish at once wherever they go" of 2026-10-28.
- 2026-11-25 · **Login limit per username** (lead's decision, WEB-SEC-019, BVTGC-110). 20 failed logins for one username within an hour, from any IP addresses, lock that username from every address for 15 minutes after the last of them. Tries during the lock do not extend it; an unlock, a password reset or a successful login clears it. Same 429 `locked_out` answer, so no client change. The 5-per-address lockout of 2026-10-21 stays.

## Working across repos

- Branches in every repo: `dev` (integration), `qa` (UAT), `main` (production). Branch from `dev`, open pull requests into `dev`, promote `dev` to `qa` for UAT and `qa` to `main` for release.
- An API change starts in the BackEnd repo: update `docs/api-contract.md`, the code and the tests in one pull request, and tell the mobile team and the web developer before merging.
- Open questions and their current defaults are listed in `docs/prototype-inventory.md`, section 8. Do not build against a different answer without the owner's decision.
