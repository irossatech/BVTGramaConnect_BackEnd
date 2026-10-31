# Prototype inventory

Status: written 2026-10-21 from a full read of the prototype folder. Read-only reference; nothing in the prototype folder was changed.

> **Update 2026-10-21 (owner):** officials now log in with a username and a password. There is no OTP, SMS or email login, and roles are Django groups and permissions. The OTP screens and endpoints below are prototype history; the current flow is in [authorization.md](authorization.md).

Prototype folder: `E:\IROSSA_Technologies(2025)\03_PROJECTS\BVT Grama Connect\BVT Grama Connect mobile prototype` (the brief calls it /design/prototype).

Endpoint names below refer to `api-contract.md` in this folder. Line numbers (L123) refer to the prototype file named at the top of each part.

## Summary

| Part | Approved file | Built with | Port approach | Lives in |
|---|---|---|---|---|
| Mobile app, all roles | `Grama Connect Mobile V2.dc.html` | Design Canvas runtime on React 18 | Rebuild in Flutter, per CLAUDE.md | BVTGramaConnect_Mobile |
| Web public | `Public Website.dc.html` | Design Canvas runtime on React 18 | Framework route: keep the files, wire data in the logic class | BVTGramaConnect, `src/`, served at `/` |
| Web admin | `Super Admin Console.dc.html` | Design Canvas runtime on React 18 | Framework route, same as web public | BVTGramaConnect, `console/`, served at `/console/` |

Contents:
1. Shared technology
2. Mobile app
3. Web public
4. Web admin
5. Porting approach for the web parts
6. Mismatches with CLAUDE.md, all parts
7. Decisions taken in this inventory
8. Open questions

---

## 1. Shared technology

All three prototypes are `.dc.html` files exported from a design tool. They are not plain HTML. Each one is a small React application written in the tool's own template language.

| Piece | What it is |
|---|---|
| `<x-dc>` block | The page template. HTML with inline styles, plus `{{ expr }}` bindings, `<sc-if value>` and `<sc-else>`, `<sc-for list as>` loops, event props such as `onClick="{{ handler }}"`, and `<x-import>` to place a React component. `<helmet>` holds head tags. |
| `<script type="text/x-dc" data-dc-script>` | The page logic: `class Component extends DCLogic` with React-style `state`, `setState`, `componentDidMount`, and `renderVals()`, which returns every value the template binds to. `data-props` holds design-tool preview settings. |
| `support.js` (69 KB, 1,911 lines) | The "dc-runtime". Generated file marked "do not edit". It loads React 18.3.1 and ReactDOM 18.3.1 UMD from unpkg with SRI hashes, compiles the template into React elements in the browser, evaluates the logic class with `new Function`, and mounts it. It loads `@babel/standalone` 7.29.0 only when a page imports a `.jsx` file (L1176-1211). |
| `_ds/bvt-grama-connect-design-system-…/` | Design system. `styles.css` imports four token files: `fonts.css` (Google Fonts: Noto Sans Telugu and Poppins, 400-800), `colors.css`, `typography.css`, `spacing.css`. `_ds_bundle.js` (24 KB) is precompiled React and puts seven components on `window.BVTGramaConnectDesignSystem_3ef6e4`: Badge, BottomNav, Button, HeroCard, IconBadge, LocationSelector, ServiceCard. `_ds_manifest.json` and `_adherence.oxlintrc.json` are design-tool metadata. `readme.md` describes the brand; it was inferred from one promotional poster. |
| `android-frame.jsx` | Generic Material 3 phone frame used only to preview the mobile prototype at 390x844. It is the only `.jsx` import, so only the mobile prototype pulls in Babel. |

Holds for all three files:
- No package.json, no bundler, no build step, no npm dependencies. No Tailwind, Bootstrap, Vue or Angular.
- Styling is inline `style` attributes almost everywhere, using design tokens as CSS variables. Each page overrides some tokens in its own `<style>` block (section 2.5).
- Fonts come from Google Fonts at runtime: Rubik (headings), Nunito Sans (body), Noto Sans Telugu. The design-system `fonts.css` also requests Poppins, which no page uses.
- Icons are inline SVG paths in the Lucide style, written into the markup or into a static `ICON` map. Five PNG category discs. No icon font or icon library is loaded.
- The design-system `IconBadge` and `HeroCard` find their images through `document.documentElement.dataset.dsBase`, falling back to `../../assets`. The mobile prototype sets `dsBase='assets'` on mount (V2 L1524). The console does not, so its Settings page shows broken category icons.
- On boot the runtime fetches its own page URL once more and re-reads the raw template (support.js L158-163). This is what keeps `<sc-for>` inside `<tbody>` and `<select>` working; the browser's HTML parser would otherwise move those loops out. The console relies on it in six tables and two selects. The re-read is skipped whenever `window.__resources` is defined.

### Other files in the prototype folder

| File | What it is | Used by |
|---|---|---|
| `Grama Connect Mobile V2.dc.html` | Mobile prototype, current version | Flutter rebuild |
| `Grama Connect Mobile.dc.html` | Mobile prototype, first version; a subset of V2 | Reference only |
| `scraps/v2-before-bars.dc.html`, `scraps/v2-before-ui-fixes.dc.html` | Earlier snapshots of V2, same features | Nothing |
| `scraps/sketch-…napkin` | Empty sketch (`"objects": []`) | Nothing |
| `uploads/pasted-*.png` (3) | Feedback screenshots pasted into the design tool: a post action bar (Listen, Save for later, Share) and a bottom nav with a Videos tab. V2 already has both. | Nothing |
| `.thumbnail` | WebP preview for the design tool | Nothing |
| `assets/logo-lockup.png`, `assets/icon-location.png` | Brand files | No page uses them |

---

## 2. Mobile app: `Grama Connect Mobile V2.dc.html`

2,050 lines, about 342 KB. Template L9-1380, logic L1381-2048.

**Why V2 is the approved file:** V2 contains everything in V1 (748 lines) and adds the Super Admin role, videos, saved posts, share and save-as-image, listen, notification tabs, and the areas, reports, audit and settings screens. Its Super Admin data block says it "mirrors Super Admin Console.dc.html" (L1466). It shows version labels v2.0 and v2.4. Both scraps are named "before" fixes made to V2.

### 2.1 Technology
- Design Canvas page on React 18.3.1. Uses `AndroidDevice` from `android-frame.jsx` as the phone bezel (L47) and two design-system components: `IconBadge` and `Badge`. None of this carries into Flutter.
- One `<style>` block (L18-44): keyframes `gc-fade`, `gc-toast`, `gc-sheet`, `gc-dim`, `gc-spin`, `gc-nudge` and tab slides; press scale 0.97; focus ring 3 px `#E9791E`; a `prefers-reduced-motion` switch.
- Fonts: Rubik 500/600/700, Nunito Sans 400/600/700, Noto Sans Telugu 400-700 (L17). A custom `@font-face "Telugu UI"` (L20-24) loads Noto Sans Telugu at `size-adjust: 88%` with ascent and descent overrides, so Telugu sits level with Latin text.
- Icons: Lucide-style SVG paths in `static ICON` (L1385-1388) and the nav set `NAVI` (L1921). Share-target logos are Simple Icons paths (CC0) in `static BRAND` (L1398).
- Device APIs used: `localStorage` (`gc_saved`, `gc_seen`, `gc_read`, `gc_fs`, `gc_lang`), `speechSynthesis` (te-IN, en-IN), clipboard, `navigator.share` with files, canvas export (1080x1920 share image), video metadata (length check), pointer and wheel events (swipe deck), `window.open` share intents.
- No network calls for data. All data is in memory.
- Designed at 390x844.

### 2.2 Screens

> **Update 2026-10-21 (owner):** officials now log in with a username and a password. There is no OTP, SMS or email login, and roles are Django groups and permissions. The OTP screens and endpoints below are prototype history; the current flow is in [authorization.md](authorization.md).

The `screen` state key picks the view. `go()` pushes onto a back stack, `nav()` resets it for tab switches, and `homeOf(role)` maps each role to its home.

| Key | Title (te / en) | Roles | What it does | Lines |
|---|---|---|---|---|
| `splash` | BVT GRAMA CONNECT | all | Logo and tagline; moves on after 1.6 s or a tap | L71-80 |
| `language` | భాష ఎంచుకోండి / Choose your language | first run | Two big buttons; then straight to the citizen home with a hard-coded village | L82-96 |
| `welcome` | స్వాగతం! / Welcome! | first run | "I'm a Citizen (no login)" or "Official Login"; language pill | L98-119 |
| `login` | అధికారుల లాగిన్ / Official login | officials | +91 mobile field, Send OTP; a demo-account list that must not ship | L121-157 |
| `otp` | OTP నమోదు చేయండి / Enter OTP | officials | 6 boxes over a hidden input, Verify, Change number, "fill demo OTP" | L159-184 |
| `pickLocation` | గ్రామం మార్చండి / Change village | citizen | Four-step drill-down: State, District, Mandal, Village, with step bar and crumbs | L186-206 |
| `cHome` | BVT Grama Connect | citizen | Greeting and update count; highlights carousel; "Today in {village}" with Listen-all; "Don't miss" dates; 60-second video strip; topic tiles with counts; invite card | L208-341 |
| `cVideos` | 60 సెకన్ల వీడియోలు / 60-second videos | citizen | Full-screen vertical reels: swipe, muted autoplay, tap to pause, Save, Share | L343-372 |
| `cNews` | వార్తలు / News | citizen | Category chips; text size; swipeable card deck with "Read full post"; Listen, Save, Share | L374-439 |
| `cSaved` | సేవ్ చేసిన పోస్టులు / Saved posts | citizen | Posts saved on this phone only; Listen, save as image, Share | L441-474 |
| `post` | — | all | Post detail: photo, gallery or video; category, date, area; text size; body; source; Listen, Save, Share | L476-515 |
| `notifications` | నోటిఫికేషన్లు / Notifications | all | All and Unread tabs, Mark all read, grouped Today and Earlier; content depends on role | L517-557 |
| `profile` | ప్రొఫైల్ / Profile | all | Citizen stats and rows (language, change village, saved, notifications, invite); Official Login or Logout | L559-601 |
| `pHome` | నా పోస్టులు / My posts | panchayat | Create Post; own posts with status; Recheck shows the comment and "Edit & resubmit"; Discarded shows the reason | L603-628 |
| `compose` | పోస్ట్ సృష్టించండి / Create Post | panchayat, admins, super | Title, description, category chips, target (district and mandal admins: whole area or selected areas), photos (3 slots) or one video up to 60 s; "Submit for review" or "Publish now" | L630-703 |
| `aHome` | డాష్‌బోర్డ్ / Dashboard | district, mandal, village | Area card; 3 stats; Create Post; queue preview | L705-731 |
| `queue` | సమీక్ష క్యూ / Review Queue | district, mandal, village | Pending posts in the area | L733-751 |
| `sDash` | రాష్ట్ర కన్సోల్ / State console | super | Area counts; 4 KPI tiles; escalation banner; 14-day chart; pending by district; recent activity | L753-811 |
| `sPosts` | అన్ని పోస్టులు / All posts | super | Status tabs with counts; district and category filters; "New post"; "Escalated" tag | L813-847 |
| `sAreas` | ప్రాంతాలు & భౌగోళికం / Areas & geography | super | Drill-down District, Mandal, Village with counts; add and edit | L849-917 |
| `sMore` | మరిన్ని / More | super | Reports, Audit log, Settings, Language, Profile, Logout | L919-941 |
| `sReports` | నివేదికలు / Reports | super | Totals; posts by district and mandal; review time by level against limit; admin activity; CSV export | L943-1008 |
| `sAudit` | ఆడిట్ లాగ్ / Audit log | super | Search; type tabs (Posts, Users, Areas, Logins, System); grouped by day | L1010-1041 |
| `sSettings` | సెట్టింగ్‌లు / Settings | super | Categories on and off; languages on and off; escalation hours 6-168 in steps of 6; notification templates | L1043-1109 |
| `review` | సమీక్ష / Review | admins, super | Full post and author; Pending: Publish, Send for Recheck, Discard; Published: Take down | L1111-1137 |
| `published` | ప్రచురించిన పోస్టులు / Published posts | district, mandal, village | Published posts in the area with Take down | L1139-1153 |
| `users` | వినియోగదారుల నిర్వహణ / Manage Users | admins, super | Summary counts; role tabs; search; grouped list; Block and Unblock | L1155-1204 |
| `addUser` | వినియోగదారుని జోడించండి / Add User | admins, super | Name, mobile, role (only roles this admin may create), area | L1206-1226 |
| Sheet: area add or edit | — | super | English name, Telugu name, PIN, "Visible to citizens"; rename warning | L1269-1303 |
| Sheet: template edit | — | super | Telugu and English text with variable chips | L1305-1327 |
| Sheet: share | — | all | Save as image, WhatsApp, Facebook, Telegram, X, Email, SMS, Copy, native share | L1329-1355 |
| Sheet: save as image | — | all | 1080x1920 branded image; download or share | L1357-1377 |
| Confirm modal | — | admins | Optional required text: recheck comment, discard reason | L1250-1267 |
| Toast | — | all | 2.2 s | L1245-1248 |

**Header** (L50-68): back button or logo, title and subtitle, bell with unread dot for citizens and panchayat members, language pill on every screen.

**Bottom nav** (L1228-1243), a floating pill bar:
- Citizen: Home, News, Videos, Saved, Profile
- Panchayat member: Home, Create, Alerts, Profile
- District, Mandal, Village Admin: Home, Review, Posts, Users, Profile
- Super Admin: Home, Posts, Users, Areas, More

Badges show the unread count (Alerts) or the pending count (Review, Posts), capped at "9+".

**Named but not built:** a location prompt and "we found your village" screen exist only as titles and strings (`locTitle`, `locBody`, `allow`, `skip`, `foundTitle`, L1916). There are strings with no screen for help-line numbers, news search and "Mark as urgent".

### 2.3 Mock data and the API each needs

| Data | Where | Content | Needs |
|---|---|---|---|
| `AREA` | L1383 | 16 English to Telugu area names | `name` on every geography object |
| `GEO` | L1384 | 1 state, 1 district, 2 mandals, 6 villages | `GET /geo/states`, `/geo/districts`, `/geo/mandals`, `/geo/villages`, `/geo/villages/{id}`, `/geo/villages/nearest` |
| `DEMO` | L1444 | 5 demo officials; login picks the role from the mobile's last digit | `POST /auth/otp/request`, `POST /auth/otp/verify`, `POST /auth/refresh`, `POST /auth/logout`, `GET /me`. The demo list is removed. |
| `POSTS` | L1445-1465 | 19 posts: 12 published, 5 pending, 1 recheck, 1 discarded; 14 village, 3 mandal, 2 district; 3 videos, 7 photos, 1 gallery; 5 with event dates; every post in Telugu and English | Citizens: `GET /feed`, `/feed/posts/{id}`, `/feed/posts?ids=`, `/feed/summary`, `GET /categories`. Panchayat: `GET /posts?mine=1`, `POST /posts`, `PATCH /posts/{id}`, `POST /posts/{id}/submit`, `POST /posts/{id}/media`. Admins: `GET /review/queue`, `POST /posts/{id}/review`, `GET /posts?status=PUBLISHED`, `GET /posts/{id}/history`. |
| `USERS` | L1497-1513 | 15 users: 2 district, 2 mandal, 5 village, 6 panchayat; 2 blocked; no mobile numbers | `GET /users` with `counts`, `POST /users`, `POST /users/{id}/block`, `POST /users/{id}/unblock` |
| Dashboard stats | computed, L1768 | Pending, published today, active users in the area | `GET /stats` |
| Notifications | computed, L1951-1965 | Panchayat: own post status changes. Admins: pending posts in the area. Citizens: published posts for their area. Read state on the device. | Officials: `GET /notifications`, `POST /notifications/{id}/read`, `POST /notifications/read-all`. Citizens: built from `/feed` on the device. All: `POST /devices`. |
| `SA_GEO` | L1467-1471 | 3 districts, 4 mandals, 8 villages with PIN and active flag | `GET /geo/*?include_inactive=1&with_counts=1`, geography writes |
| `SA_AUDIT` | L1472-1486 | 13 audit rows | `GET /audit` with `counts_by_type` |
| `SA_CHART`, `SA_ESC`, `SA_OLDEST` | L1487-1489 | 14-day chart, escalated post ids, oldest pending per district | `GET /admin/dashboard` |
| `SA_TEMPLATES` | L1491-1496 | 4 notification templates with Telugu and English text and `{variables}` | `GET /admin/notification-templates`, `PATCH …/{key}` |
| Settings state | L1515 (`cats`, `langs`, `esc: 24`) | Category switches, language switches, escalation hours | `GET /admin/settings`, `PATCH /admin/settings`, `/admin/categories` |
| `reviewTime`, `adminActivity` | L1885-1887 | Hard-coded report numbers | `GET /admin/reports/review-time`, `/admin/reports/admin-activity`, `/admin/reports/summary`, `/admin/reports/posts-by-area` |
| `SAMPLE_VIDS`, `GAL` | L1399-1400 | Sample video picker and fake gallery crops | Prototype only; removed |
| Default location | L1515 | Guntur, Tenali, Kolakaluru | The citizen's own choice, stored on the device |
| Presentation maps | `STATUS`, `ROLEBG`, `CATC`, `TINT`, `CATT`, `WHEN`, `RTINT`, `FS`, `ICON`, `NAVI`, `BRAND` | Colours, icons, sizes | Stay in the app as constants |
| UI dictionary `T` | L1404-1443 | 490 Telugu keys, 489 English keys (`imgHint` missing in English) | Stays in the app; maps to Flutter ARB files with Telugu as the template locale |

### 2.4 Navigation and interactions
- **First run:** splash, then language, then the citizen home with a hard-coded village. No GPS step. Tapping the splash goes to welcome instead.
- **Back:** header back button and Escape walk the back stack; tab switches clear it.
- **Language:** header pill on every screen switches the UI and post text at once. Not saved between launches.
- **News deck:** drag with an 8 px threshold; commit at 18% of the height or 0.45 px/ms; rubber-band at the ends; settle in 0.42 s with `cubic-bezier(.2,.8,.2,1)`; first-card nudge; progress segments; "Read full post · n min" when the body overflows, at 120 words a minute. Swiping marks a post seen, which drives the "New" tag.
- **Reels:** one muted looping video plays at a time; tap pauses; progress bar.
- **Highlights carousel:** horizontal snap with dots; up to 5 cards chosen by rule: top photo post, nearest deadline, next event, a scheme, a news or update item (L1726-1741).
- **Text size:** cycles Normal, Large, Extra large (body 17, 19, 21.5 px); saved on the device.
- **Listen:** reads a post aloud, or all of today's posts in order; toggles to Stop.
- **Save:** bookmark per post, kept on the device only.
- **Share:** text is title, body, video link, source, date and "— BVT Grama Connect". Targets: WhatsApp, Facebook, Telegram, X (240 characters), Email, SMS, Copy, native share. Save-as-image draws a 1080x1920 branded picture.
- **Confirm modal:** recheck needs a comment and discard needs a reason; Confirm stays disabled until filled. Publish, take down, block and unblock need no text.
- **Not in the prototype:** pull-to-refresh, offline banner, loading skeletons, paging, error states. Flutter must add them in the prototype's visual style.

Client-side validation, exactly as coded:
- Login mobile: non-digits stripped, 10 digits enables Send OTP. No check on the first digit.
- OTP: digits only, 6 enables Verify.
- Compose: title and description required after trimming; no length limit; category defaults to announcements; 0 to 3 photos; video must be `video/*`, at most 100 MB and 60.5 s, with four specific error messages (L1519-1523); photos and video exclude each other.
- Target: only District and Mandal Admins choose; "Selected areas" with nothing picked falls back to the whole area (L1651). District Admins can pick mandals but not villages.
- Add user: name required; mobile 10 digits; role and area required (L1980).
- Area sheet: English name required and unique among siblings, ignoring case; PIN optional, exactly 6 digits.
- Template sheet: both languages required.
- Escalation hours: 6 to 168 in steps of 6.
- Languages: the last one switched on cannot be switched off.

### 2.5 Visual system for the Flutter rebuild

V2 overrides the design-system tokens (L19). Use the V2 values below, not the design-system readme.

| Token | Value |
|---|---|
| Primary blue | `#0D3B7A` (design system had `#00407e`) |
| Navy, headings and nav | `#0A2550` |
| Green | `#1F8A3B` (design system had `#359227`) |
| Orange accent | `#E9791E` (design system had `#ff8401`) |
| Highlight yellow | `#F2DD3D` |
| Destructive | `#B3261E` on `#FDE7E5` |
| App background | `#F6F8F3` |
| Card | `#FFFFFF`, border `#EEF2F6` |
| Input border | `#CFD8E3` |
| Text: ink, body, muted, disabled | `#17212E`, `#3B4A5C`, `#6B7A8C`, `#9AA6B4` |

| Category | Disc | Card tint | Text on white |
|---|---|---|---|
| announce | `#1F8A3B` | `#E4F4DF` | `#1D6B2F` |
| schemes | `#E9791E` | `#FFF0DC` | `#9A4A08` |
| news | `#1A75C4` | `#E3F0FB` | `#0D3B7A` |
| updates | `#8F4BB4` | `#F1E6F7` | `#6E3590` |

| Status chip | Background | Text |
|---|---|---|
| Pending | `#FFF0DC` | `#B35A0E` |
| Published | `#E4F4DF` | `#1D7A33` |
| Recheck | `#F1E6F7` | `#8F4BB4` |
| Discarded | `#EEF2F6` | `#3B4A5C` |
| Taken down | `#FDE7E5` | `#B3261E` |

Role colours: super `#0A2550`, district `#0D3B7A`, mandal `#07818E`, village `#1F8A3B`, panchayat `#E9791E`, citizen and system `#6B7A8C`.

- **Type:** headings and numbers in Rubik 600/700; body in Nunito Sans 400/600/700; Telugu in Noto Sans Telugu scaled to 88%. Common sizes 12 to 22 px. Header title 17/700, screen hero 22/700.
- **Shape:** pills (999) for buttons, chips, nav and tags; 12, 14 and 16 px card radii; 20 px top corners on sheets; circles for discs and avatars.
- **Shadows:** card `0 2px 8px rgba(0,64,126,.08)`; raised `0 10px 28px rgba(0,64,126,.14)`; header `0 2px 10px rgba(10,37,80,.25)`; nav `0 4px 12px rgba(10,37,80,.18)`.
- **Layout:** 16 px gutters; 60 px blue header with logo disc; floating pill nav inset 14 px at the sides and 12 px at the bottom, 48 px items; the active item is a white pill with icon and label, inactive items are icons at 78% white; orange count badges with a navy ring.
- **Buttons:** primary pill in blue or green, 54 px high, white bold text with a trailing chevron; secondary pill with a 1.5 px blue border.
- **Motion:** 200 ms fades, sheets slide up, toast rises, press scale 0.97, deck 0.42 s, reduced motion respected. Focus ring 3 px orange.
- **Tricolour rule:** orange, white and green bar under the brand on splash, language and the share image.

### 2.6 Assets and language
- **Used:** `assets/logo-mark.png`, `assets/village-landscape.jpg` (welcome hero and every mock post photo), `assets/icon-announce|schemes|news|updates.png`, and three silent 25-second videos with posters in `assets/videos/`.
- **Real post media:** none. Every photo is the same sample image cropped differently. Flutter loads `media[].url` from the API.
- **External:** Google Fonts, unpkg React and Babel, share intents (wa.me, facebook.com/sharer, t.me/share, twitter.com/intent, mailto:, sms:).
- **Telugu:** throughout. Telugu is the default (`lang: 'te'`, L1515). The dictionary is flat key-to-string per language, with `{n}`, `{village}`, `{x}`, `{h}` placeholders and `key` / `key1` plural pairs. English-only: the brand name, the English tagline, "+91", version labels, CSV headers, share-target names.

---

## 3. Web public: `Public Website.dc.html`

310 lines, about 62 KB. Template L9-238, logic L240-308.

### 3.1 Technology
- Design Canvas page on React 18.3.1. No Babel: it imports no `.jsx` file.
- Loads `_ds_bundle.js` (L16) but uses none of its components. Loads all five design-system CSS files (L11-15).
- A `<style>` block (L18-24) overrides tokens: `--blue-900:#0D3B7A`, `--green-600:#1F8A3B`, `--orange-500:#E9791E`, `--text-heading:#0A2550`.
- Fonts: Rubik 500-800, Nunito Sans 400/600/700, Noto Sans Telugu 400-800 (L17).
- Icons: inline SVG (menu, close, check, plus, map pin, phone, Google Play, Apple) and the five PNG category discs.
- No localStorage, no fetch. The chosen language is not saved.
- Responsive: a ResizeObserver on the root switches to the mobile layout below 820 px (L279, L286). The editor-only `viewport` prop (`auto`, `desktop`, `mobile`) defaults to `auto`.

### 3.2 Pages
Pages switch in state (`state.page`, L278). There are no URLs or hash routes.

| `page` | Title (te / en) | What it does | Lines |
|---|---|---|---|
| header | — | Sticky: logo (home), 5 nav buttons on desktop, language toggle (`EN` / `తెలుగు`), "Get the app" (goes to contact), menu button below 820 px | L29-50 |
| `home` | హోమ్ / Home | Hero with store buttons and a phone mockup; five services; three steps; who it is for | L52-130 |
| `how` | ఇది ఎలా పనిచేస్తుంది / How It Works | The four levels and each level's duties; the three steps again | L132-146 |
| `officials` | అధికారుల కోసం / For Officials | How to post, review, and get a login; contact and admin-login buttons | L148-159 |
| `faq` | ప్రశ్నలు / FAQ | Seven-question accordion, the first one open | L161-173 |
| `contact` | సంప్రదించండి / Contact | Office address, phone, email, hours; contact form with a thank-you state | L175-202 |
| `admin` | అడ్మిన్ లాగిన్ / Admin Login | Placeholder card: mobile field, "Send OTP" (demo toast), link to the console file, back to site; no footer | L204-218 |
| footer | — | Tricolour bar, logo, Telugu tagline, page links, store links, admin login, copyright, privacy and terms as plain text | L221-234 |
| toast | — | Bottom toast, 2.6 s | L236, L282 |

### 3.3 Mock data and the API each needs

| Data | Where | Today | Needs |
|---|---|---|---|
| UI text, Telugu and English | `Component.T` L243-277, about 190 keys each | Static | Nothing; stays static |
| Phone mockup location, featured post and two mini cards | Template L71-73, `miniCards` L297 | Hard-coded; the featured post stays Telugu in both languages | Optional `GET /public/showcase` (Q16) |
| Services, steps, levels, audiences, officials cards, FAQs | L291-296 | Built from the dictionary | Nothing |
| Office address, phone, email, hours | L182-183, `t.hours` | Hard-coded | `GET /public/site-config` |
| Store links | L62-63, L229, `storeClick` L301 | "Coming soon" toast | `play_store_url`, `app_store_url` from site config; the toast stays while they are empty |
| Contact form | L190-199, L303-304 | Sets `sent` locally | `POST /public/contact-messages` |
| Admin login card | L204-218, `adminOtp` L301 | Mobile field unbound; demo toast | No API; button and link go to `/console/` |
| "© 2026" | L231 | Hard-coded | Nothing |

### 3.4 Navigation and interactions
- Nav buttons, logo, footer links and CTAs call `go(page)`, which closes the menu and scrolls to the top (L280).
- Language toggle swaps the dictionary; Telugu by default.
- FAQ accordion: one open at a time.
- Contact form: name, mobile, village or mandal (free text, optional), message.
  - Mobile keeps digits only, at most 10 (L303).
  - Submit fails with `t.fError` when the trimmed name is empty, the mobile has fewer than 10 digits, or the trimmed message is empty (L304). Any edit clears the error.
  - Success shows a thank-you panel with the name and mobile; "Send another message" resets the form.

### 3.5 Assets and language
- Used: `assets/logo-mark.png`, `assets/village-landscape.jpg`, the five `icon-*.png` discs.
- Telugu: throughout, and the default. The Telugu hero subtitle is an English sentence and the English one starts in Telugu, on purpose (L244, L261). The footer tagline is always Telugu.

---

## 4. Web admin: `Super Admin Console.dc.html`

577 lines, about 108 KB. Template L9-397, logic L398-575 (`state` L464-468, helpers L470-502, `renderVals` L504-573).

### 4.1 Technology
- Design Canvas page on React 18.3.1. Uses two design-system components: `Badge` (L222, L324) and `IconBadge` (L279). No `.jsx` import, so no Babel.
- Token overrides (L19): same blue, green, orange and heading colours as the website; `--font-latin: Rubik`, `--font-telugu: Nunito Sans`. Body `#F6F8F3`, 14 px.
- Fonts: Rubik 500/600/700, Nunito Sans 400/600/700, Noto Sans Telugu 400-700 (L17).
- Icons: Lucide-style SVG; nav icons in `static ICON` (L401).
- Charts: none from a library. "Posts per day" is a flex bar chart of divs (L113-121); review time uses div progress bars (L246).
- No fetch, localStorage or sessionStorage. A reload resets everything.
- Layout: fixed 1440 px canvas, `min-height: 900px`, `overflow: hidden` (L28). Not responsive. Sidebar 248 px; side panels 520 and 460 px; modal 460 px. Toasts top right, 2.6 s.
- Category icons on the Settings page are broken today: the page never sets `data-ds-base` (section 1).

### 4.2 Pages

> **Update 2026-10-21 (owner):** officials now log in with a username and a password. There is no OTP, SMS or email login, and roles are Django groups and permissions. The OTP screens and endpoints below are prototype history; the current flow is in [authorization.md](authorization.md).

`state.screen` is `login`, `otp` or `app`; inside the app, `state.page` picks the page.

| View | Title | What it does | Lines |
|---|---|---|---|
| Login | Super Admin sign in | Photo panel with tagline; +91 mobile; Send OTP; "Demo login as Super Admin" (demo only) | L31-54 |
| OTP | Enter OTP | 6 boxes over a hidden input; Verify; Change number; "Fill demo OTP" (demo only) | L55-62 |
| Shell | — | Dark sidebar with 7 items, Posts with a pending badge; header with title, search, bell, profile menu; version "v2.4 · Andhra Pradesh" | L68-95 |
| Dashboard | Dashboard | 5 KPI cards that drill down; 14-day posts chart; pending by district (click filters Posts); recent activity | L100-139 |
| Geography | Geography · Master Data | Expandable State, District, Mandal, Village tree with Telugu names, inactive chips and counts; add, edit, deactivate, reactivate; side form with English and Telugu names and, for villages, latitude, longitude, pincode | L142-175 |
| Users | Users | Filters: role, district, status, search; "+ Add User"; table with name, mobile, role, area, status, last login; View, Reassign area, Block or Unblock | L178-201 |
| Posts & Moderation | Posts & Moderation | Status tabs with counts: All, Pending (default), Published, Recheck, Discarded, Taken down; district and category filters; row opens the review panel | L204-231 |
| Reports | Reports | Posts by area with CSV export; average review time by level against a limit; admin activity for 30 days with CSV and PDF export | L234-257 |
| Audit Log | Audit Log | Free-text search; entry count; CSV export; Time, User, Role, Action, Target, Area | L260-270 |
| Settings | Settings | Categories (on and off, add); languages (Telugu locked on); escalation hours; four notification templates | L273-311 |
| Post review panel | Post review | Badge, status, area and date; Telugu and English title and body; up to 3 photos; author card; history timeline; actions by status | L318-338, L348-352 |
| User panel | User details | Six tiles; recent posts by the user; Reassign area; Block or Unblock | L339-346, L353-355 |
| Add User drawer | Add User | Name, Telugu name, mobile, role, area; note about the SMS invite | L360-374 |
| Confirm modal | varies | Optional select and optional required text | L377-387 |
| Toasts | — | Top right, 2.6 s | L391-395 |

### 4.3 Mock data and the API each needs

> **Update 2026-10-21 (owner):** officials now log in with a username and a password. There is no OTP, SMS or email login, and roles are Django groups and permissions. The OTP screens and endpoints below are prototype history; the current flow is in [authorization.md](authorization.md).

| Data | Where | Content | Needs |
|---|---|---|---|
| `AREA` | L400 | 15 English to Telugu names | `name_te` on geography objects |
| `ICON`, `STATUS`, `ROLE` | L401-403 | Nav icons, status and role colours | Stay. An adapter maps API values: `UNPUBLISHED` to `TakenDown`, `SUPER_ADMIN` to `super`, and so on. |
| `CATL` | L404 | 4 category labels | `GET /categories` |
| `CHART` | L462 | 14 days of submitted and published counts | `GET /admin/dashboard` |
| Identity | L86, L472, L484, L557 | "Sita Mahalakshmi", Super Admin, +91 98480 00001 | `GET /me` |
| Login | L52, L60, L559-560 | Any 10 digits, any 6-digit OTP, demo buttons | `POST /auth/otp/request`, `POST /auth/otp/verify` with `client: "web_admin"`, `POST /auth/refresh`, `POST /auth/logout`. Demo buttons removed. |
| `GEO` | L405-411 | 3 districts, 4 mandals, 8 villages with lat, lng, pin and active flag; the state name is in the template (L146) | `GET /geo/tree`, geography writes |
| `USERS` | L412-428 | 15 users with one role each; 2 blocked | `GET /users`, `GET /users/{id}`, `POST /users`, block, unblock, `PATCH /users/{id}/role-assignments/{ra_id}`; recent posts via `GET /posts?author=` |
| `POSTS` | L429-445 | 15 posts: 7 published, 5 pending, 1 recheck, 1 discarded, 1 taken down; 11 village, 2 mandal, 2 district; Telugu and English text on every post; photos are the sample image | `GET /posts` with `status_counts`, `GET /posts/{id}`, `GET /posts/{id}/history`, `POST /posts/{id}/review` |
| `AUDIT` | L446-461 | 14 audit rows | `GET /audit`, `GET /audit/export` |
| KPI texts, oldest pending | L517, L520 | Hard-coded "26 h", "Oldest 26 h · 1 escalated" | `GET /admin/dashboard` |
| `reviewTime`, `adminActivity` | L540-541 | Hard-coded numbers | `GET /admin/reports/review-time`, `/admin/reports/admin-activity`, `/admin/reports/posts-by-area`, exports |
| Settings | L466-467 | Escalation 24 h; 4 categories; languages; 4 templates | `/admin/settings`, `/admin/categories`, `/admin/notification-templates` |
| "Today" | L472, L484, L516 | 14 Oct 2026 | Server time |
| Hard-coded options | L180, L182, L212, L368 | Role, status, category and add-user role lists | The category list must come from `GET /categories` |

Review actions in the panel (L484-489), each through the confirm modal:
- Pending: **Publish** (no comment), **Recheck** (comment required), **Discard** (reason required).
- Published: **Take down** (reason required).
- Published, Recheck, Discarded or Taken down: **Override previous decision** to Published, Pending, Recheck or Discarded with a required reason. Not in CLAUDE.md's state machine (Q2).

### 4.4 Navigation and interactions
- **Sidebar:** 7 pages; Posts shows the live pending count; changing page closes menus, panels and the drawer.
- **Header:** global search jumps to Posts with All and filters by title and body only, although its placeholder promises users and villages. The bell only opens the Audit page; its dot is static.
- **Dashboard drill-downs:** KPI cards open Posts or Users with a preset filter; district rows open Posts filtered to Pending and that district.
- **Panels and drawer:** open over a dark scrim; scrim or close button dismisses.
- **No pagination, no sorting, no bulk actions** anywhere. Lists render every row.

Client-side validation, exactly as coded:
- Login mobile: digits only, 10 enables Send. OTP: digits only, 6 enables Verify.
- Geography form: Save disabled until the name is non-blank. Latitude, longitude and pincode have placeholders only.
- Add User: name non-blank and 10-digit mobile; Telugu name defaults to the English name.
- Recheck, Discard, Take down, Override: comment required.
- New category: at least one name, otherwise a toast.
- Escalation hours: HTML `min=1 max=168` only.

### 4.5 Assets and language
- Used: `assets/village-landscape.jpg` (login, mock post photos), `assets/logo-mark.png`, the four category discs through IconBadge.
- Telugu: mostly in data. Titles, buttons, table headers, modals and toasts are English only. Telugu appears in the tagline, the state name, Telugu-name field labels, and heavily in mock data. No language toggle and no dictionary in the console.

---

## 5. Porting approach for the web parts

### 5.1 Decision: framework route for both
Both web parts use a framework: the Design Canvas runtime on React 18. CLAUDE.md's framework rule applies to both.

The plain-HTML route does not fit. The markup is a template language only the runtime understands. All behaviour lives in the logic class: page switching, the language toggle, accordions, forms, modals, filters, panels. Turning it into Django templates would mean rewriting that behaviour, which breaks "port as is".

### 5.2 Where the code lives

| Part | Folder in the brief | Repo | Served at |
|---|---|---|---|
| Web public | /web-public | BVTGramaConnect, folder `src/` | `/` |
| Web admin | /web-admin | BVTGramaConnect, folder `console/` | `/console/` |
| API, Django admin, media | /backend | BVTGramaConnect_BackEnd | `/api/`, `/django-admin/`, `/media/` |

Everything sits on one origin, so the console's refresh cookie needs no CORS and the website calls the API with relative URLs. The `web_public` and `web_admin` Django apps serve the web repo's build in development. Until 2026-10-21 the website and the console were two repos; they are now one, BVTGramaConnect, owned by one web developer.

### 5.3 Layout of the web repo

```
BVTGramaConnect/
  src/              website: index.html (the .dc.html page, renamed), gc-api.js, assets/icon-location.png
  console/src/      console: index.html (generated by console/tools/port.py), gc-api.js
  console/tools/    logic.js, port.py, visual-check scripts
  shared/           identical for both apps, never edited by hand:
    support.js      dc-runtime, byte-for-byte copy
    vendor/         React 18.3.1 and ReactDOM 18.3.1 UMD, the same files the runtime would fetch
    _ds/…/          design system, unchanged
    assets/         common images, unchanged
    config.js       window.GC_CONFIG = { apiBase, consoleUrl }
  build.mjs         one build: dist/ for the website, dist/console/ for the console
```

The build has no dependencies. It copies `shared/` and each app's own files into `dist/` and `dist/console/` byte for byte and writes `config.js` from `GC_API_BASE` and `GC_CONSOLE_URL`. The pages use relative paths, so nothing in the markup changed when the two apps moved into one repo.

### 5.4 What may change during the port
Only these changes are allowed. Anything else is a design change and needs sign-off.
1. The page file is renamed to `index.html`. The runtime takes its root name from the file name, so any name works.
2. In `<head>`, before `support.js`: script tags for `vendor/react…`, `vendor/react-dom…`, `config.js` and `gc-api.js`. The runtime skips its unpkg download when `window.React` and `window.ReactDOM` already exist (support.js L1838-1846). Production then loads no third-party script.
3. `window.__resources` must **not** be defined. Defining it switches off the runtime's re-read of the raw page, and the console's tables and selects would break.
4. `data-ds-base="assets"` on `<html>`, so design-system icons resolve. This fixes the console's broken Settings icons and changes nothing on the website.
5. In the logic class only: mock constants become `state` filled from the API in `componentDidMount` and in handlers. An adapter turns API objects into the shapes `renderVals()` already returns, so the template does not change. Loading and error states reuse what the page has: toasts, empty lists, disabled buttons.
6. Hard-coded values inside the template become `{{ }}` bindings: the website's contact details and phone mockup; the console's post photo `src` (L328) and category filter options (L212).
7. Demo-only controls are removed: the console's demo login and demo OTP buttons, the website's demo toasts once real URLs exist.
8. Markup, inline styles, CSS files, fonts and assets stay as they are.

### 5.5 How it is served
- **Development:** Django serves both folders when `DEBUG` is on. `web_public` maps `/` to `WEB_PUBLIC_DIR` (default `../BVTGramaConnect/dist`); `web_admin` maps `/console/` to `WEB_ADMIN_DIR` (default `../BVTGramaConnect/dist/console`).
- **Production:** the deploy script builds the web repo once and copies `dist/` to the folder Nginx serves at `/` and `dist/console/` to the folder it serves at `/console/`. Nginx proxies `/api/` and `/django-admin/` to Gunicorn and serves `/media/` from `/var/media`.
- Files are served byte for byte. No HTML minifier, because the runtime re-reads the raw page.
- Nginx sends `charset utf-8`, gzip for HTML, JS, CSS and JSON, long cache headers for `vendor/`, `_ds/` and `assets/`, a year (`immutable`) for the website's `_next/static/`, and `no-cache` with an ETag for every `.html` page and `support.js`.

### 5.6 Constraints the port inherits
- **Content Security Policy:** the runtime uses `new Function` and the markup uses inline styles. These pages need `script-src 'self' 'unsafe-eval'`, `style-src 'self' 'unsafe-inline' https://fonts.googleapis.com`, `font-src https://fonts.gstatic.com`, `connect-src 'self'`. This is why the console keeps its refresh token in an HttpOnly cookie and its access token in memory only.
- **Generated runtime:** `support.js` is never edited. When the designer re-exports a page, the new file replaces the old one whole and the data wiring is carried over by hand.
- **No URL per page:** browser Back leaves the site and pages cannot be linked to (Q16).
- **Search engines** see the raw template with `{{ }}` placeholders until JavaScript runs. This weakens the public site's search ranking.
- **Console width:** the console is a fixed 1440 px canvas and clips on narrower screens.
- **Page weight:** about 69 KB of runtime, about 140 KB of React (about 45 KB compressed), 24 KB of design-system code, the page itself (62 KB website, 108 KB console), web fonts and images. `logo-mark.png` is 107 KB but shown at 44 px. Shrinking images changes file names in the markup and needs sign-off.

---

## 6. Mismatches with CLAUDE.md, all parts

"Follow" says what the backend contract does today. Items marked Q are open questions in section 8.

| # | Topic | CLAUDE.md | Prototypes | Follow |
|---|---|---|---|---|
| 1 | Post states | DRAFT, PENDING, PUBLISHED, RECHECK, DISCARDED, UNPUBLISHED | No DRAFT anywhere; panchayat posts go straight to Pending (mobile L1645-1647). Unpublished is called "Taken down". The mobile Super Admin "Discarded" tab mixes discarded and taken down. | Keep DRAFT, but `POST /posts` submits by default, so the prototype flow works (Q4). Show UNPUBLISHED as "Taken down". |
| 2 | Review model | Every admin in the area reviews; first decision wins; one escalation time of 24 h | Website copy describes tiers: the Village Admin reviews within 24 h, then it escalates to the Mandal Admin, and District Admins review escalated posts. The console shows per-level limits of 24, 24 and 48 h and an instant escalation when the area's only reviewer is blocked. | Shared queue, first decision wins; escalation flags the post and alerts the next level up without hiding it (Q1). |
| 3 | Override | Not in the state machine | Console lets the Super Admin move any decided post to any status, including Discarded to Published. The mobile app has no override. | `override` action, Super Admin only, behind a decision (Q2). |
| 4 | Post language | Not defined; Telugu default | Every mock post has Telugu and English text and the console shows both. Mobile compose takes one text and copies it into both. The website FAQ says posts appear in the language they were written in. | One `language` plus an optional `translation` (Q3). |
| 5 | Super Admin posting | `SUPER_ADMIN_CAN_POST=False` | Mobile has "New post" for the Super Admin and creates state-level posts. The console has no compose screen. | The app hides "New post" unless `rules.super_admin_can_post`; the server refuses it. |
| 6 | Post target | One `target_level` and `target_area_id` | Mobile "Selected areas" creates one post per chosen mandal or village. District Admins cannot pick single villages. | One post per area, created by separate calls (Q5). |
| 7 | Roles per user | Several assignments per user | Every user has exactly one role and area in both mobile and console. No screen adds a second assignment or switches roles. | Data model supports several; UI is Q9. |
| 8 | Blocking | Revokes all sessions at once | Mobile copy says only "They will not be able to post". The console says "They will be signed out". | Revoke all sessions through the token version. The Flutter copy follows the console. |
| 9 | Settings | Django settings overridable by env | Mobile and console edit escalation hours, category switches, language switches and notification templates at runtime. The website says the Super Admin sets them. | Database values override the env defaults (Q6). SMS templates stay read-only because DLT fixes their text. |
| 10 | Citizen location | Nearest village by GPS | Mobile has no GPS step. First run uses a hard-coded village; only unused strings exist. | Flutter adds a location step: GPS, then the manual picker. Built from the existing strings and design system. |
| 11 | Village coordinates | Every village has latitude and longitude | The console form has them. The mobile area sheet does not. | Coordinates required on create (Q10). |
| 12 | Geography extras | Not listed | Villages have a PIN code; every level can be deactivated and reactivated. | Accepted as additions. |
| 13 | Feed rule | Village, its mandal, its district | Mobile `relevant()` (L1677) also shows every district-level post from other districts. | Follow CLAUDE.md. The prototype behaviour is a bug. |
| 14 | Event dates | Not listed | Mobile posts carry a date or date range with a kind; it drives "Don't miss", highlights and the date chip. Compose has no field for it. | Optional `event` on posts (Q7). |
| 15 | Video | Images only, resized to WebP | Mobile has 60-second videos, reels, and video upload up to 100 MB. | Contract reserves `kind: video`; scope is Q8. |
| 16 | Photo limit | Not listed | Mobile and website say at most 3 photos. | Setting `MAX_PHOTOS_PER_POST = 3`. |
| 17 | Review comments | Not listed | All three parts require a comment for recheck and discard; the console also for take down and override. | Required for recheck, discard, unpublish and override. |
| 18 | Who may take down | Not specified | Mobile lets any admin take down any published post from the review screen. The console shows a District Admin taking down a Mandal Admin's post. | Any reviewer whose area contains the post (Q18). |
| 19 | Master data | Super Admin | Website FAQ says the District Admin adds missing villages. | Super Admin only (Q13). |
| 20 | Audit scope | Every post transition | Prototypes also log logins, users, areas, categories, settings, templates and exports. | Log all of them. |
| 21 | Super Admin accounts | Not specified | No screen creates, lists or blocks Super Admins. | `createsuperuser` or a management command (Q12). |
| 22 | Contact messages | No model | The website form asks for village requests, login requests and problem reports. No console page reads them. | Stored by `web_public`, read in Django admin (Q16). |
| 23 | Console access | Super Admin only | The website's admin-login card invites all officials and links to the console. | The console refuses others with a clear message (`not_console_user`). |
| 24 | OTP wording | SMS OTP | Website says "OTP login link by SMS"; the console's Add User note says the same. | OTP code. Copy fix only. |
| 25 | Citizen alerts | Notifications app plus FCM | Citizen alerts are built on the device from feed posts; read state is per device. | Matches: citizens use FCM topics and device-side lists. |
| 26 | Duplicate console | Super Admin uses web admin and app | The app carries a full state console: dashboard, posts, areas, reports, audit, settings. | Matches. Both clients use the same endpoints. |

Consistent with CLAUDE.md: citizens read without login; officials log in by OTP; admin posts auto-publish; District and Mandal Admins choose a target area; who may create whom; Telugu as the locked default; Super Admin uses both the app and the console.

---

## 7. Decisions taken in this inventory

These are not open questions. Change them by editing this file and CLAUDE.md.
1. `Grama Connect Mobile V2.dc.html` is the approved mobile prototype. V1 and the scraps are reference only.
2. Both web parts take the framework route described in section 5.
3. Where a prototype disagrees with CLAUDE.md, the backend follows CLAUDE.md. The table in section 6 records each case.
4. Authentication uses simplejwt with a per-user token version so blocking revokes sessions at once. Details are in the contract, section 1.
5. The Flutter app adds a location step (GPS, then the picker) that the prototype only names.
6. Flutter adds pull-to-refresh, loading, offline and error states in the prototype's visual style; the prototype has none.
7. **Decision, 2026-10-21 (owner): no SMS.** OTP login and every SMS were dropped. Officials log in with a username (or email) and password. An admin invites them; based on the role they get an email with their username and the role's default password, and must change it at first login. The Super Admin can change each role's default password. The prototypes' mobile-number and OTP screens become username/password and change-password screens in the same visual style. Questions about SMS and DLT no longer apply. *Changed later the same day: no email invites and no default passwords; see decision 9.*

8. **Decision, 2026-10-21 (owner): one web repo.** The website and the Super Admin console were merged into one repo, BVTGramaConnect, owned by one web developer: the website in `src/`, the console in `console/`, files identical for both in `shared/`, one `npm run build`. The separate Admin repo was removed. Nothing visible changed: both built outputs match the old builds byte for byte except the generated `config.js`.

9. **Decision, 2026-10-21 (owner): Django auth, groups and permissions.** Login is a username and a password only; email is an optional contact field. The admin who adds or resets an official sets a temporary password or has one generated and shown once; nothing is emailed. django-axes locks a username and IP pair after 5 failures for 15 minutes, and a higher admin can unlock it. The app keeps JWT; the console uses a Django session with CSRF. Roles are Django groups and every check asks for a Django permission inside the assignment's area. Full detail: [authorization.md](authorization.md).

---

## 8. Open questions

Numbers match the "Pending Qn" marks in `api-contract.md`. The owner decided Q1, Q3, Q5, Q7, Q8, Q9, Q10, Q13, Q14, Q16, Q17 and Q19 on 2026-10-21 (see `.claude/project.md`, "Owner decisions"). Q2, Q4, Q6, Q11, Q12, Q15 and Q18 are still open and run on their defaults. Login and permissions changed later on 2026-10-21; see [authorization.md](authorization.md). On 2026-10-28 the owner changed Q3: published posts are machine-translated.

| Q | Question | Answer, or the default until there is one |
|---|---|---|
| Q1 | Do all admins in the area see a pending post at once with the first decision winning, as CLAUDE.md says? Or is review tiered, village first and then escalated, as the website and console suggest? Is there one escalation time, or per-level limits of 24, 24 and 48 hours? | **Decided 2026-10-21:** shared queue, first decision wins, one escalation time for every level (`REVIEW_ESCALATION_HOURS`, 24). |
| Q2 | May the Super Admin override a decision, for example republish a discarded or taken-down post? Should other higher admins be able to? | Not built until confirmed |
| Q3 | Is a post written in one language, or must it have both Telugu and English? | **Decided 2026-10-21:** one language per post, Telugu or English. **Changed 2026-10-28:** the author still writes in one language, and every published post is machine-translated once into the other; the original always stays available (`translation`, contract section 1). |
| Q4 | Should panchayat members be able to save drafts, or submit straight to review as the prototype does? | Submit straight away; drafts supported by the API |
| Q5 | "Selected areas": one post per area, or one post with several targets? May a District Admin target single villages? | **Decided 2026-10-21:** one target per post, "Visible to" up to the author's own level. District Admins: the district, a mandal or a village. Mandal Admins: the mandal or a village in it. Village Admins and Panchayat members: their village. State: Super Admin only, with `SUPER_ADMIN_CAN_POST`. Choices come from `GET /posts/targets/`. **Changed 2026-10-28:** one post for several areas. Every poster, citizens included, chooses Everyone or any districts, mandals and villages anywhere; admins' posts still publish at once and the admins of every chosen area review the others (contract section 3, "Posts"). |
| Q6 | May the Super Admin change escalation hours, switch categories and languages, add categories, and edit notification texts at runtime? SMS texts would stay fixed because DLT registration requires it. | Yes, stored in the database over env defaults; SMS read-only |
| Q7 | Add an event date field (deadline, camp, distribution, closure, meeting) to compose, so "Don't miss" works? | **Decided 2026-10-21:** yes, optional: kind, start date, optional end date. "Don't miss" reads `GET /feed/upcoming/`. |
| Q8 | Keep 60-second video posts in version 1? If yes, what size and quality limits suit 2G/3G? | **Decided 2026-10-21:** in version 1. MP4 or MOV, at most 60 s and 50 MB, one video per post and never with photos. `VIDEO_POSTS_ENABLED` switches it off. |
| Q9 | How do users with several role assignments switch between them, and is there a screen to add one? | **Decided 2026-10-21:** second roles are added and removed in the console only. The app has no screen for it. **Changed later on 2026-10-21:** any admin with the group's manage permission over the area may give a lower second role (`authorization.md`, section 6). |
| Q10 | Do village coordinates come only from the master-data import, or should the app's village form get latitude and longitude fields? | **Decided 2026-10-21:** coordinates come from the console's village form and the CSV import. |
| Q11 | May the console get a small pager or "Load more" on Users, Posts and Audit? The design has none. | Yes, minimal pager |
| Q12 | How are Super Admin accounts created? | `createsuperuser` and a management command |
| Q13 | Does anyone besides the Super Admin maintain master data? The website FAQ says District Admins add villages. | **Decided 2026-10-21:** Super Admin only. |
| Q14 | Is PDF export needed in version 1, or CSV only? | **Decided 2026-10-21:** CSV only. |
| Q15 | Should the console get a CSV import for villages? The delivery plan asks for an import tool with validation. | Yes, in the console Geography page |
| Q16 | Public website: should the phone mockup show live posts? Where should staff read contact messages? Should each page get its own URL, for example `#faq`? | **Decided 2026-10-21:** live posts in the phone picture (`GET /public/showcase/`), a URL per page, contact messages read in the console (`GET /admin/contact-messages/`). |
| Q17 | Which public URL do shared posts and app invites link to? The website has no post page. | **Decided 2026-10-21:** sharing sends the post text plus the website link (`website_url` in site-config). There is no public post page. |
| Q18 | Who may take down a published post? | Any reviewer whose area contains the post |
| Q19 | Are the help-line numbers, news search and "Mark as urgent" strings in scope for version 1? They have no screens. | **Decided 2026-10-21:** all three in version 1. Help lines come from site-config, search is `q` on the feed, urgent is `POST /posts/{id}/urgent/` and `GET /feed/urgent/`. |

### Important numbers (Contacts tab)

The app's Contacts tab moved onto the server on 2026-10-26 (`api-contract.md`, section 14). Built on these defaults until the owner decides:

| # | Question | Default |
|---|---|---|
| C1 | May Panchayat members add and edit numbers for their village? | Yes, published at once. The alternative is a review step like posts. |
| C2 | May officials' personal mobile numbers be published? | Yes, but only with the consent tick for 10-digit mobiles (`CONTACTS_REQUIRE_MOBILE_CONSENT`). Office, landline and CUG numbers are preferred. |
| C3 | Who signs off the starter helpline list? | The owner, before production. Until then `seed_helplines` loads only the six numbers of `SITE_HELPLINES`. |
| C4 | "Other" types: is free text acceptable? | Yes, up to 40 characters per number. The Super Admin can turn a frequent one into a real type. |
| C5 | Should district or mandal admins get their own helplines section? | No. Helplines are statewide and the Super Admin's; a district control room is an area number at district level. |
