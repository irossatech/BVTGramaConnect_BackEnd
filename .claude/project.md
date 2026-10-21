# BVT Grama Connect: project rules

This file is identical in all three repos. Change it in all three at once, or not at all.

## The product

A village news platform for Andhra Pradesh. Panchayat members and admins post village news; admins review it; citizens read verified news for their village in Telugu or English without logging in.

| Product | Who uses it | Repo |
|---|---|---|
| Mobile app, Android and iOS | Every role, including citizens | BVTGramaConnect_Mobile (Flutter) |
| Public website, no login | Citizens and visitors | BVTGramaConnect, folder `src/` |
| Super Admin console | Super Admins only | BVTGramaConnect, folder `console/` |
| REST API, Django admin, background jobs, deployment | All three clients | BVTGramaConnect_BackEnd (Django) |

Owners: the backend team owns BVTGramaConnect_BackEnd, the mobile team owns BVTGramaConnect_Mobile, and one web developer owns BVTGramaConnect (website and console). On the server everything sits on one domain: the website at `/`, the console at `/console/`, the API at `/api/v1/`, uploaded photos at `/media/`.

## Sources of truth

All in the BackEnd repo:
- `docs/api-contract.md`: every endpoint, object, error code and permission rule. Clients code against it; the backend team changes it first and announces the change.
- `docs/prototype-inventory.md`: what the approved prototypes contain, how the web parts are ported, the mobile design tokens, and the open questions with their defaults (section 8).
- `docs/uat.md`: the role-by-role acceptance checklist.
- `docs/security-review.md`: security status and open items.
- `deploy/`: server setup, deploy, backup and restore.

The approved design is the prototype folder "BVT Grama Connect mobile prototype" (ask the project manager for the zip). The approved mobile file is `Grama Connect Mobile V2.dc.html`. It is read-only reference: never edit it.

## Roles

A user can hold several role assignments, each tied to one area. Areas: State > District > Mandal > Village. Each role is a Django group, and what it may do is a set of Django permissions (BackEnd repo, `docs/authorization.md`).

| Role | Area | Posts | Reviews | Creates and blocks |
|---|---|---|---|---|
| SUPER_ADMIN | Whole platform | Only if SUPER_ADMIN_CAN_POST | Every post | District, Mandal, Village Admins, Panchayat members |
| DISTRICT_ADMIN | District | Yes | Posts in the district | Mandal Admins, Village Admins, Panchayat members in the district |
| MANDAL_ADMIN | Mandal | Yes | Posts in the mandal | Village Admins, Panchayat members in the mandal |
| VILLAGE_ADMIN | Village | Yes | Posts in the village | Panchayat members in the village |
| PANCHAYAT_MEMBER | Village | Yes, to own village, goes to review | No | No |
| Citizen | Chosen village | No | No | No login at all |

Only holders of the console permission, the Super Admin group, can use the web console.

## Posts

States: DRAFT > PENDING > PUBLISHED, RECHECK or DISCARDED; RECHECK > PENDING; PUBLISHED > UNPUBLISHED (shown as "Taken down").
- Panchayat posts go to PENDING. Admin posts publish at once while ADMIN_POSTS_AUTO_PUBLISH is on, and only inside the admin's own area.
- The first reviewer's decision wins. A later reviewer gets 409 `already_decided` naming who decided.
- Only a strictly higher level can reverse a decision.
- Recheck, discard, take-down and reverse need a comment.
- Every change writes a review record and an audit entry.
- Pending posts older than REVIEW_ESCALATION_HOURS alert the next level up.
- The citizen feed shows published posts for the village, its mandal, its district and the state.

## Configurable rules

Django settings, overridable by environment variables:
ADMIN_POSTS_AUTO_PUBLISH=True, REVIEW_FIRST_DECISION_WINS=True, REVIEW_ESCALATION_HOURS=24, SUPER_ADMIN_CAN_POST=False, FORCE_PASSWORD_CHANGE_ON_FIRST_LOGIN=True, MAX_PHOTOS_PER_POST=5. Languages: Telugu (default) and English.

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
- 2026-10-21 · **Web parts keep the prototype's technology.** The website and console are Design Canvas pages (React 18 runtime) served as built files, not rewritten.
- 2026-10-21 · **One web repo.** The website and the console live together in BVTGramaConnect (the console in `console/`), owned by one web developer. The separate Admin repo was removed. One `npm run build` builds both.
- 2026-10-21 · **Server database not chosen yet.** Everyone develops and tests on SQLite until the owner picks MySQL or PostgreSQL.
- 2026-10-21 · **Escalation:** one time for every level (REVIEW_ESCALATION_HOURS, 24 by default).
- 2026-10-21 · **Language:** a post is written in one language, Telugu or English.
- 2026-10-21 · **Visibility:** when posting, the author picks "Visible to": village, mandal, district or state, up to their own level. Village Admins and Panchayat members: village. Mandal Admins: a village in the mandal or the whole mandal. District Admins: a village, a mandal or the whole district. State: Super Admin only, and only when SUPER_ADMIN_CAN_POST is on. One target per post.
- 2026-10-21 · **Event date:** posts may carry an optional event (deadline, camp, distribution, closure or meeting) with a start date and an optional end date.
- 2026-10-21 · **Video posts are in version 1**, at most 60 seconds.
- 2026-10-21 · **Also in version 1:** "mark as urgent" for admins, news search in the citizen feed, and a help-line numbers screen in the app.
- 2026-10-21 · **Second roles:** any admin with the matching manage permission for that area may add lower roles, in the console or through the API. This replaces the earlier console-only rule.
- 2026-10-21 · **Mobile app work for the new permissions is postponed.** The API keeps the role keys and the `capabilities` block in `/me` so the current app keeps working.
- 2026-10-21 · **Master data:** only the Super Admin adds or edits villages and mandals; coordinates come from the console and the CSV import. **Exports** are CSV only.
- 2026-10-21 · **Website:** the home page's phone picture shows live posts, every page has its own URL, and contact messages are read in the console.
- 2026-10-21 · **Sharing a post** sends its text plus a link to the website. There is no public post page.
- 2026-10-21 · **Console:** "Reset password" sits in the user panel, not in the users table.
- 2026-10-21 · **/django-admin/** is reachable on the server only from office IP addresses.

## Working across repos

- Branches in every repo: `dev` (integration), `qa` (UAT), `main` (production). Branch from `dev`, open pull requests into `dev`, promote `dev` to `qa` for UAT and `qa` to `main` for release.
- An API change starts in the BackEnd repo: update `docs/api-contract.md`, the code and the tests in one pull request, and tell the mobile team and the web developer before merging.
- Open questions and their current defaults are listed in `docs/prototype-inventory.md`, section 8. Do not build against a different answer without the owner's decision.
