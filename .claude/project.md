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

States: DRAFT > PENDING > PUBLISHED, RECHECK or DISCARDED; RECHECK > PENDING; PUBLISHED > UNPUBLISHED (shown as "Taken down").
- Who will see it: every poster picks Everyone, or any mix of districts, mandals and villages anywhere, in one post (1 to POST_MAX_TARGETS, 200 areas).
- Panchayat and citizen posts go to PENDING. District, Mandal and Village Admins' posts publish at once while ADMIN_POSTS_AUTO_PUBLISH is on, wherever they go.
- A pending post is reviewed by the admins of every area it is for (and the areas above), and by the Super Admin. A post to Everyone is reviewed by Super Admins only.
- The first reviewer's decision wins. A later reviewer gets 409 `already_decided` naming who decided.
- Only a strictly higher level can reverse a decision.
- Recheck, discard, take-down and reverse need a comment.
- Every change writes a review record and an audit entry.
- Pending posts older than REVIEW_ESCALATION_HOURS alert the next level up.
- The citizen feed shows published posts for the village, its mandal, its district and the state.

## Configurable rules

Django settings, overridable by environment variables:
ADMIN_POSTS_AUTO_PUBLISH=True, REVIEW_FIRST_DECISION_WINS=True, REVIEW_ESCALATION_HOURS=24, SUPER_ADMIN_CAN_POST=True, MAX_PHOTOS_PER_POST=3 (or one video instead). Languages: Telugu (default) and English. A changed password at first login is always required; it is not a setting.

## Non-negotiables

- Every permission check runs on the server: the role allows the action AND the target sits inside the actor's area. Clients only hide buttons.
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
- 2026-10-21 · **Lockout:** 5 failed logins for one username from one IP address lock it for 15 minutes (django-axes). An admin ranked above the user can unlock early. Lockouts and unlocks are audited.
- 2026-10-21 · **Console login** uses a Django session with CSRF and a 30-minute idle timeout. The app keeps JWT. The API stays under `/api/v1/`.
- 2026-10-21 · **Tests run on SQLite**, Django's default database. The server uses MySQL (utf8mb4) or PostgreSQL through DATABASE_URL.
- 2026-10-21 · ~~**Web parts keep the prototype's technology.**~~ Replaced on 2026-10-22, see below.
- 2026-10-21 · **One web repo.** The website and the console live together in BVTGramaConnect (the console at `/console/`), owned by one web developer. The separate Admin repo was removed. One `npm run build` builds both.
- 2026-10-21 · **Server database not chosen yet.** Everyone develops and tests on SQLite until the owner picks MySQL or PostgreSQL.
- 2026-10-21 · **Escalation:** one time for every level (REVIEW_ESCALATION_HOURS, 24 by default).
- 2026-10-21 · **Language:** a post is written in one language, Telugu or English.
- 2026-10-21 · ~~**Visibility:** "Visible to" up to the author's own level, one target per post.~~ Replaced on 2026-10-28, see below.
- 2026-10-21 · **Event date:** posts may carry an optional event (deadline, camp, distribution, closure or meeting) with a start date and an optional end date.
- 2026-10-21 · **Video posts are in version 1**, ~~at most 60 seconds~~ (any length since 2026-11-13, see below).
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
- 2026-10-28 · **Who will see it:** every poster, citizens included, picks Everyone or any districts, mandals and villages anywhere, in any mix, in one post. Admins' posts still publish at once wherever they go; the admins of every chosen area review the others. This replaces the 2026-10-21 visibility rule. BackEnd repo, `docs/api-contract.md`, section 5.
- 2026-11-04 · **The Super Admin may post.** SUPER_ADMIN_CAN_POST is True by default; setting it to False removes "New post" again.
- 2026-11-11 · ~~**No limit on photos or videos per post.**~~ Replaced on 2026-11-13, see below.
- 2026-11-11 · **Django admin is off on every server** (lead's decision), qa included: `/django-admin/` answers 404 there. It stays on developers' laptops. Officials, roles and settings are managed in the console. This replaces the 2026-10-21 office-IP rule.
- 2026-11-13 · **Up to 3 photos or one video per post** (lead's decision), never both, for everyone who posts: citizens, Panchayat members, admins and the Super Admin. Photos are optional, at most 10 MB each; the video at most 60 MB, of any length, like banner videos. MAX_PHOTOS_PER_POST is 3. Citizen posts per day stay the Super Admin's setting in the console (Settings). Banner ads are unchanged.

## Working across repos

- Branches in every repo: `dev` (integration), `qa` (UAT), `main` (production). Branch from `dev`, open pull requests into `dev`, promote `dev` to `qa` for UAT and `qa` to `main` for release.
- An API change starts in the BackEnd repo: update `docs/api-contract.md`, the code and the tests in one pull request, and tell the mobile team and the web developer before merging.
- Open questions and their current defaults are listed in `docs/prototype-inventory.md`, section 8. Do not build against a different answer without the owner's decision.
