# Runbook

For the person who runs the BVT Grama Connect server. Commands run on the server unless stated otherwise. `sudo` works for the `deploy` user.

## After the first setup

1. **Store the backup passphrase offline.** Print `/etc/gramaconnect/backup.passphrase` or put it in the team's password manager. Without it no backup can be restored.
2. **Load master data** (at least the state): see [DATA_MOVE.md](DATA_MOVE.md).
3. **Create the first Super Admin** (after the shortcut below is defined):

   ```
   gc create_super_admin --username sita.admin --name Sita
   ```

   It prints a temporary password once. Give it to the person in person or by phone; they log in at `/console/` and must choose their own password first. Every other official is added from the console or the app by an admin above them, who sets or generates the temporary password and passes it on the same way. Nothing is emailed.
4. **Configure push** (Firebase) below. Until then pushes are off.
5. `deploy.sh` stops before migrating if `manage.py check --deploy --tag security` reports any warning (`STRICT_CHECKS=1`, the default). Django sends HSTS for a year on the API and Django admin (`SECURE_HSTS_SECONDS`), Nginx on the web pages; the HTTPS redirect comes from Nginx, and HSTS for subdomains and the preload list stay off until the domain is known, so the production part of `grama/settings.py` (`DEBUG=False`) silences only those three checks. `STRICT_CHECKS=0` in `/etc/gramaconnect/deploy.env` deploys anyway, for emergencies.
6. It also stops before migrating if `pip-audit` finds a known vulnerability in `requirements.txt` (`AUDIT_DEPENDENCIES=1`, the default; pip-audit runs from its own venv in `/srv/gramaconnect/audit-venv`). Upgrade the named package to a fixed version in `requirements.txt`, run the tests, and deploy again. `AUDIT_DEPENDENCIES=0` skips the check, for emergencies.

Django admin is **off on every server** (lead's decision of 2026-11-11): `/django-admin/` answers 404. Officials, roles and settings are managed in the console; anything else goes through `gc shell`. `gc sync_role_permissions` puts the role matrix back. Only if ever needed, `DJANGO_ADMIN_ENABLED=True` in the server's settings file turns it back on in Django; on this setup nginx still answers `/django-admin/` with 404 itself, so that `location` block in `nginx-locations.conf` would have to change too (a lead's decision).

## Shortcut used below

Commands that need Django's settings load the environment file first:

```
gc() { sudo -u deploy bash -c "set -a; . /etc/gramaconnect/gramaconnect.env; set +a; cd /srv/gramaconnect/backend && ../venv/bin/python manage.py $*"; }
gc check --deploy
gc showmigrations
```

## Services and logs

| Task | Command |
|---|---|
| Status | `systemctl status gramaconnect-gunicorn gramaconnect-qcluster nginx` |
| API log | `journalctl -u gramaconnect-gunicorn -f` |
| Background jobs log | `journalctl -u gramaconnect-qcluster -f` |
| Nginx logs | `/var/log/nginx/gramaconnect.access.log`, `gramaconnect.error.log` |
| Restart the app | `sudo systemctl restart gramaconnect-gunicorn gramaconnect-qcluster` |
| Health | `curl -s https://<domain>/api/v1/health/` (`"worker": "late"` means the background jobs stopped: restart qcluster; `failed_jobs_24h` above 0: read the background jobs log for `Failed`) |
| Uptime alert | Point an uptime monitor (any service that checks a URL and alerts by email or SMS) at `https://<domain>/api/v1/health/?strict=1`. It answers 503 when the database does not answer (`database_down`) or the background jobs stopped (`worker_down`), so one check covers both (BE-OBS-201). Never give the deploy scripts this address: they call health before the worker starts. |
| Last backups | `systemctl list-timers gramaconnect-backup.timer` and `journalctl -u gramaconnect-backup -n 50` |
| Banned IPs (SSH) | `sudo fail2ban-client status sshd` |
| Unban an IP | `sudo fail2ban-client set sshd unbanip <ip>` |
| Firewall | `sudo ufw status verbose` |

Common problems:
- **502 Bad Gateway:** Gunicorn is down or still starting. Check its log; `deploy.sh` prints the cause of a failed start.
- **Telugu shows as `????`:** the database or the connection is not utf8mb4. `setup.sh` creates the database with utf8mb4 and Django adds `charset=utf8mb4`; check `SHOW CREATE DATABASE grama;`.
- **MySQL or MariaDB error 1055, 1292 or 1365 after a deploy:** since audit BE-DB-010 every connection uses `sql_mode` `TRADITIONAL,ONLY_FULL_GROUP_BY` (`DB_SQL_MODE`), so the database refuses an unclear GROUP BY (1055), a bad date (1292) or a division by zero in a write (1365) instead of quietly accepting it. Check the mode with `gc shell -c "from django.db import connection; c=connection.cursor(); c.execute('SELECT @@SESSION.sql_mode'); print(c.fetchone()[0])"`. To go back at once, put `DB_SQL_MODE=STRICT_TRANS_TABLES` in the server's `.env` and restart, then send the error line to the backend team: it points at a real bug. PostgreSQL has no `sql_mode` and is not affected.
- **413 Request Entity Too Large:** requests over 60 MB are refused by Nginx (`client_max_body_size` in `templates/nginx-locations.conf`), except the three upload addresses, which allow 70 MB for one video of up to 60 MB plus its form fields: officials' post media (`/api/v1/posts/{id}/media/`), citizen posts (`/api/v1/citizen/posts/`, up to 3 photos or one video) and banner ads (`/api/v1/admin/ads/`). Django then limits photos to `MAX_IMAGE_UPLOAD_MB` (10) and videos to `MAX_VIDEO_UPLOAD_MB` and `ADS_VIDEO_MAX_MB` (60 each). On the hosting panel, its nginx must allow 70 MB on those addresses too.
- **504 Gateway Timeout:** Nginx gives every API request 60 seconds, except uploads (`/api/v1/citizen/posts/`, `/api/v1/posts/{id}/media/`, `/api/v1/admin/ads/`) and the CSV imports (`/api/v1/geo/villages/import/`, `/api/v1/admin/contacts/import/`), which get 120; Gunicorn's `--timeout` is 120 to match (`templates/nginx-locations.conf`, `templates/gramaconnect-gunicorn.service`; `GUNICORN_TIMEOUT` on the hosting panel). Django stops checking uploaded files after `MEDIA_PROCESSING_BUDGET_S` (90 s) and answers 400 or 415, so an upload never reaches the limit. A whole state's villages import in about 10 seconds. On the hosting panel, its nginx must allow these routes at least 120 seconds too. These templates are installed by `setup.sh`, not `deploy.sh`: after changing them, run `sudo bash deploy/setup.sh` again.
- **404 on `/django-admin/`:** expected; Django admin is off on servers (`DJANGO_ADMIN_ENABLED`).

- **Video uploads ask for `duration_s` or a `poster`:** ffmpeg is missing, so the server cannot measure the video or make its poster. `sudo apt-get install ffmpeg`, then restart Gunicorn.
- **429 on login:** Django (django-axes) locks a username for 15 minutes after 5 failed tries from one IP (`locked_out`). An admin above the person can unlock them early ("Unlock" in the console or the app), or from the shell: `gc shell -c "from apps.accounts.lockout import unlock; from apps.accounts.models import User; print(unlock(User.objects.get(username='<username>')))"` (axes stores a hash of the username, so `axes_reset_username` does not find it).
- **Someone forgot their password:** an admin above them uses "Reset password" in the console or the app, which shows a new temporary password once (or lets the admin type one). The person must change it at first login; their other sessions end.
- **Role permissions look wrong:** `gc sync_role_permissions --check` lists differences from `apps/accounts/role_matrix.py`; `gc sync_role_permissions` fixes them. `migrate` runs the same sync on every deploy.

## Deploying a release

```
sudo bash /srv/gramaconnect/backend/deploy/deploy.sh
```

It pulls `GIT_BRANCH` of the backend repo and the web repo (BVTGramaConnect, setting `REPO_WEB`), installs packages, migrates, collects static files, uses the web repo's committed `dist/` (or builds it with `npm ci && npm run build` when `dist/` is not committed), copies `dist/` (without `console/`) to `/srv/gramaconnect/www/public` and `dist/console/` to `/srv/gramaconnect/www/console`, restarts the services, and checks the API. Before it changes anything, it takes a backup with `backup.sh` (only when started as root, as above; `BACKUP_BEFORE_DEPLOY=0` in `/etc/gramaconnect/deploy.env` skips it for an emergency), so a failed migration can be undone with `restore.sh` from minutes before, not from last night. Each deploy that passes the health check adds one line to `/srv/gramaconnect/deploy-history.log`: `deployed`, the time (UTC), the backend commit and the web commit that went live together.

### Rolling back a release

```
sudo bash /srv/gramaconnect/backend/deploy/deploy.sh --rollback
```

It puts back the previous recorded **pair** (backend and web together, so the console always matches its API): the backend code and its Python packages, the static files, the website and console build; then restarts the services and checks the API, and records a `rolled-back` line. After a deploy that failed its health check (not recorded), "previous" is the last good deploy; run it again to go one more step back. To choose the versions yourself: `deploy.sh --rollback <backend commit> <web commit>` (from the history log or `git log`).

Migrations are never reversed. Before changing anything, the rollback compares the migrations applied to the database with the ones the older backend has; if the database is ahead, it lists them and stops. Then restore the backup taken before that deploy (`restore.sh`, below) and run `deploy.sh --rollback` again. The next normal `deploy.sh` brings the branch's latest code back, so fix or revert the bad commit on the branch first.

### When a migration fails part-way

On MySQL each schema change is saved at once, so a migration that fails in the middle leaves the database half changed; on PostgreSQL the failing migration is undone, but the ones before it in the same deploy stay. Either way the database no longer matches any release (audit finding BE-DB-013, BVTGC-109). `deploy.sh` stops with the migration's error and the app may not start. Then:

1. Do not run `deploy.sh` again: it would try the same migration on a half-changed database.
2. Find the backup taken just before this deploy: the newest `/var/backups/gramaconnect/gramaconnect-<time>.tar.gpg`, from minutes ago (the deploy log says "Backup before deploying"). If the deploy ran with `BACKUP_BEFORE_DEPLOY=0` or without `sudo`, there is none: use last night's backup and say so to the lead, since changes made since then are lost.
3. Restore only the database (a migration does not touch `/var/media`): `sudo bash /srv/gramaconnect/backend/deploy/restore.sh --db-only <file>`.
4. Put the previous release back: `sudo bash /srv/gramaconnect/backend/deploy/deploy.sh --rollback`. It now finds the database in step with the older backend.
5. Check: `gc showmigrations` lists no migration the older backend lacks, the API health check answers `ok`, and the console signs in.
6. Fix or revert the failing migration on the branch, run it on qa first (the qa deploy also takes a backup before pulling, `deploy/qa/BVT-deploy.sh`), then deploy again.

Writing migrations so this stays rare: keep schema changes and data steps in separate migrations, and give data steps a reverse step (`.claude/conventions.md`, "Database").

### The citizen wallet's first release

The full checklist, with who does what and the daily watch during the pilot, is in the BackEnd repo's `docs/features/citizen-wallet/technical-implementation.md`, section 13. For the server team:

1. Before: email works on this server ("Email for citizen wallet codes" below), and `CITIZEN_CONSENT_VERSION` in the environment file is the date of the published privacy policy and terms.
2. Deploy as usual with `deploy.sh`. It runs `migrate` (the wallet's migrations are additive) and `ensure_schedules`. Leave `WALLET_ENABLED` out of the environment file: the Super Admin switches the wallet on in the console.
3. Check: the health check answers `ok`; `gc check` shows no `citizens.W002`; `curl -s https://<domain>/api/v1/app-config/` says `"wallet_enabled": false` until the Super Admin switches it on.
4. After the app build with the wallet screens is on the website, set `APP_LATEST_VERSION` to it and restart.
5. To stop the wallet in an emergency, the Super Admin switches it off in the console; nothing is lost. Do not roll the code back past the wallet's migrations without the lead (see "Rolling back a release").

### Servers set up before the web repos were merged

The website and the console used to be two repos, cloned to `/srv/gramaconnect/public` and `/srv/gramaconnect/admin`. They are now one repo, BVTGramaConnect, cloned to `/srv/gramaconnect/web`. Nginx and the server env (`WEB_PUBLIC_DIR`, `WEB_ADMIN_DIR`) do not change. On such a server, once:

1. Add `REPO_WEB=https://github.com/irossatech/BVTGramaConnect.git` to the deploy config (`/etc/gramaconnect/deploy.env`, or the config file you pass to the scripts). Remove `REPO_PUBLIC` and `REPO_ADMIN`.
2. Run `deploy.sh`. On its first run it clones the web repo to `/srv/gramaconnect/web`.
3. Check `/` and `/console/` in a browser, then delete the old clones: `sudo rm -rf /srv/gramaconnect/public /srv/gramaconnect/admin`.

## Backups

- Nightly at 02:30 India time, `backup.sh` dumps the database (`mysqldump --single-transaction` with utf8mb4, or `pg_dump` in custom format), archives `/var/media` and `/var/media-private` (photos of taken-down and discarded posts, BVTGC-192), encrypts them with AES-256 (gpg, passphrase file) and writes `/var/backups/gramaconnect/gramaconnect-<time>.tar.gpg`.
- It copies the file to `BACKUP_TARGET` (`rclone:`, `rsync:` or `local:`) and deletes copies older than 14 days in both places.
- Run one now: `sudo bash /srv/gramaconnect/backend/deploy/backup.sh`.
- For an rclone target, set up the remote once as root: `sudo rclone config`. For an rsync target, root needs an SSH key accepted by the backup host.

### Restore

```
sudo bash /srv/gramaconnect/backend/deploy/restore.sh gramaconnect-20261021-023000.tar.gpg
sudo bash /srv/gramaconnect/backend/deploy/restore.sh --db-only <file>
sudo bash /srv/gramaconnect/backend/deploy/restore.sh --media-only <file>
```

The script decrypts, checks checksums, asks you to type the domain, stops the app, replaces the database and/or `/var/media` and `/var/media-private` (the old folders are kept as `<folder>.before-restore-<time>`), starts the app and checks the API. A backup made before BVTGC-192 has no private folder: the hidden posts' files are then in `/var/media`, and the daily housekeeping moves them out again.

### Monthly restore drill

Proves the backups work. Do it on a spare server, never on production.

1. Create a small Ubuntu VPS and run `setup.sh` with the same `DB_ENGINE` and a test domain (`CERTBOT_STAGING=1`).
2. Copy `/etc/gramaconnect/backup.passphrase` from production.
3. Copy last night's backup across, or let `restore.sh` fetch it from `BACKUP_TARGET`.
4. Run `restore.sh --yes <file>`.
5. Check: the API health answers; the console logs in; a Telugu post title displays correctly; a photo under `/media/` loads; post and user counts match production (`gc shell -c "from apps.posts.models import Post; print(Post.objects.count())"`).
6. Record the date and result. Delete the spare server.

## Rotating secrets

| Secret | How | Effect |
|---|---|---|
| `SECRET_KEY` | First, unless `PHONE_HASH_KEY=` is already in `/etc/gramaconnect/gramaconnect.env`, add that line with the current `SECRET_KEY` value (copy it exactly; never change it afterwards). Then delete the `SECRET_KEY=` line, run `setup.sh` (it generates a new one), then `deploy.sh` | Every JWT is signed with it: all officials must log in again, and current login lockouts are cleared. Citizen phones keep their daily limits and blocks through `PHONE_HASH_KEY` (BE-SEC-109). Skip that first step and every phone looks new: blocked citizens can post again after reinstalling the app |
| Database password | `sudo rm /etc/gramaconnect/db.password`, run `setup.sh`, then restart the app | `setup.sh` sets a new password in the database, `DATABASE_URL` and the backup credentials |
| Backup passphrase | Replace `/etc/gramaconnect/backup.passphrase` | Old backups still need the old passphrase: keep it until they expire |
| Firebase key | Replace the JSON file (below), restart | Revoke the old key in the Firebase console |
| SMTP password (citizen wallet email) | Make new SMTP credentials at the email service, replace `EMAIL_HOST_USER` and `EMAIL_HOST_PASSWORD` in the environment file, restart both services, send a test code ("Email for citizen wallet codes", step 6), then delete the old credentials at the service | None for users; a citizen who asks for a code during the restart may get "try again later" (`503 email_unavailable`) |

## Security notes

- **Page headers (`templates/nginx-security.conf`):** the website and console pages carry a Content-Security-Policy with no `data:` fonts or pictures, `Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=()` and `Cross-Origin-Opener-Policy: same-origin` (since 2026-11-16, web audit WEB-SEC-003/WEB-HDR-001). After changing the template run `sudo bash deploy/setup.sh` (templates are installed by `setup.sh`, not `deploy.sh`). On the hosting panel, add the same three headers to its nginx for `/` and `/console/`. If a picture or font stops loading, the browser console says "Refused to load … Content Security Policy": tell the web developer rather than adding `data:` back.
- **Website 404 page:** an address under `/` that names no file gets the website's own `404.html` (from the BVTGramaConnect build) with status 404, through `error_page 404 /404.html` in `templates/nginx-locations.conf`. If visitors see Nginx's plain "404 Not Found" page instead, check that `404.html` is in the web root's `public` folder and that `setup.sh` ran after this line was added (templates are installed by `setup.sh`, not `deploy.sh`). On the hosting panel, set its nginx's 404 page to `/404.html` too.
- **Per-IP request limits in Nginx** (removed on 2026-11-20, back before going live, BVTGC-105): the template's `gc_login` zone allows 10 requests a minute per address on the login and password-change paths, and `gc_api` 20 a second (bursts of 60) on the rest of the API, the feed and the shared links; over that Nginx answers `429`. Django's own limits come on top (lockout per username and IP, 20 an hour per username, and the cap per address, which is off unless `.env` sets `LOGIN_IP_MAX_FAILURES`). Set that only after BVTGC-39 confirms the server sees real visitor addresses, and start high, such as 200. Templates are installed by `setup.sh`, not `deploy.sh`: after this change run `sudo bash deploy/setup.sh` once. On the hosting panel's own Nginx (production and qa), add the same `limit_req_zone` lines and `limit_req` blocks by hand, or tell the lead they are missing there.
- **Console sessions:** the session cookie is HttpOnly, Secure and SameSite=Lax, every write needs the CSRF token, and a session ends after `CONSOLE_SESSION_IDLE_MINUTES` (30) idle minutes. Blocking a user or resetting their password ends their sessions at once.
- **No personal data in access logs:** the site logs with the `gc_noquery` format (`templates/nginx-site-*.conf`), which records the path without the query string. Searches such as `/api/v1/users/?q=<email or mobile>` or `/api/v1/audit/?q=…` therefore never reach `/var/log/nginx`. Keep this format if you change the logging.
- **Temporary passwords** appear once, in the API answer that created or reset them (shown to the admin on screen). They are never emailed, logged or written to the audit log.

## Push notifications (Firebase Cloud Messaging)

1. In Google Cloud (the Firebase project), create a service account `fcm-sender` with only the role **Firebase Cloud Messaging API Admin**, and a JSON key for it. It is a secret: hand it over in person, by USB or through a password manager, never in git, chat or email. The app's `google-services.json` must come from the same project, or every send fails with `SENDER_ID_MISMATCH`.
2. Copy it to `/etc/gramaconnect/fcm-service-account.json`, then:

   ```
   sudo chown root:deploy /etc/gramaconnect/fcm-service-account.json
   sudo chmod 640 /etc/gramaconnect/fcm-service-account.json
   ```

3. In `/etc/gramaconnect/gramaconnect.env`:

   ```
   FCM_CREDENTIALS_FILE=/etc/gramaconnect/fcm-service-account.json
   FCM_PROJECT_ID=<project id>
   FCM_TOPIC_PREFIX=
   ```

   `FCM_TOPIC_PREFIX` stays **empty in production**; the qa server uses `qa_` (`deploy/qa/README.md`). A prefix on production would send every post to topics no real phone follows.
4. Restart both services. `systemctl status gramaconnect-qcluster` must be running (only the worker sends pushes), and `journalctl -u gramaconnect-qcluster -n 50` must not show "FCM disabled".
5. Every post goes out at high priority by default (`PUSH_ALL_POSTS_HIGH_PRIORITY`). `PUSH_TTL_SECONDS` (one day) rarely needs changing.
6. Rotate the key yearly, and at once if it leaks: a new key, install it as above, restart, then delete the old key in Google Cloud. Failed pushes are in the audit log as `push.failed`.

## Email for citizen wallet codes (BVTGC-143)

The server sends email only to citizens' wallet accounts: the sign-up, forgotten-password and website-deletion codes, and three notices (password changed, email changed, account deleted). Officials never get email. Until email is set up, `gc check` warns `citizens.W002`, and a citizen who asks for a code gets `503 email_unavailable`; nothing else is affected. Do it on **qa first**, then on production before the wallet is switched on. The wallet itself (`WALLET_ENABLED`) stays off: the Super Admin switches it on in the console (Settings).

1. **Service and sender (client, C7).** An email-sending service, for example Amazon SES in Mumbai (`ap-south-1`) or Zoho ZeptoMail, and a sender address on the client's domain, for example `no-reply@<client domain>`. Replies are not read; the emails say so and give the help line. On SES: verify the domain, then ask for production access ("request to leave the sandbox"); until then SES delivers only to verified addresses.
2. **DNS on the sender's domain** (the client's domain admin, with the server team). Without these, Gmail puts the codes in spam:
   - **SPF:** the service's include in the domain's one SPF record, for example `v=spf1 include:amazonses.com ~all`. A domain has one SPF record: merge with the existing one, never add a second.
   - **DKIM:** the records the service gives (SES "Easy DKIM": three CNAME records).
   - **DMARC:** a TXT record at `_dmarc.<domain>`, starting with `v=DMARC1; p=none; rua=mailto:<a mailbox the client reads>`; after two clean weeks, `p=quarantine`.
3. **The SMTP port.** From the server: `timeout 5 bash -c '</dev/tcp/email-smtp.ap-south-1.amazonaws.com/587' && echo open`. If it is not open, the host blocks outgoing mail ports: ask the hosting provider (BVTGC-39).
4. **SMTP credentials.** For SES, the "SMTP credentials" made in the SES console, not an IAM access key. A secret: hand it over in person or through a password manager, never in git, chat or email.
5. **The server's environment file** (`/etc/gramaconnect/gramaconnect.env`; on qa the `.env` next to the backend, `deploy/qa/README.md`):

   ```
   EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
   EMAIL_HOST=email-smtp.ap-south-1.amazonaws.com
   EMAIL_PORT=587
   EMAIL_HOST_USER=<SMTP user>
   EMAIL_HOST_PASSWORD=<SMTP password>
   EMAIL_USE_TLS=True
   EMAIL_TIMEOUT=10
   DEFAULT_FROM_EMAIL=Grama Connect <no-reply@<client domain>>
   CITIZEN_CONSENT_VERSION=2026-11-25
   ```

   `CITIZEN_CONSENT_VERSION` is the date of the privacy policy and terms the sign-up names; change it only together with new legal texts (BVTGC-141). Restart both services, then `gc check` must not list `citizens.W002`.
6. **A test code, in Telugu and in English**, to a team mailbox (never a citizen's: the address stays in the shell history). Use a Gmail address and one other provider:

   ```
   gc shell -c "from apps.citizens import emails; print(emails.send_code('<team mailbox>', purpose='SIGNUP', code='482913', language='te', name='పరీక్ష'))"
   gc shell -c "from apps.citizens import emails; print(emails.send_code('<team mailbox>', purpose='SIGNUP', code='482913', language='en', name='Test'))"
   ```

   Each prints the message's Message-ID. Expected: the email is in the **inbox, not spam**, within a minute; the subject reads "గ్రామ కనెక్ట్ కోడ్: 482913" (then "Grama Connect code: 482913"); the Telugu shows without boxes; and Gmail's "Show original" says `SPF: PASS`, `DKIM: PASS` and `DMARC: PASS`. On qa, run the same through `.venv/bin/python manage.py shell -c "…"`. An error instead of a Message-ID: see step 8.
7. **A daily cap and a bounce alert** at the service. The app already limits codes (5 a day per account or email address, 10 per phone), but a cap at the service stops a runaway bot: during the pilot about 2,000 emails a day is plenty. Alert the team when bounces pass 5 % or complaints 0.1 % (on SES, CloudWatch alarms on `Reputation.BounceRate` and `Reputation.ComplaintRate`; on ZeptoMail, its bounce notification email). A high bounce rate gets the account paused by the service.
8. **What the logs show.** A send that fails is logged as `Citizen email 'code' not sent: <error type>`, with the kind and the error type only, never the address or the code (`journalctl -u gramaconnect-gunicorn | grep "Citizen email"`). Every code sent is in the audit log as "sent a wallet code by email", with the address masked. Common error types:
   - `SMTPAuthenticationError`: wrong SMTP user or password (or an IAM key used instead of SMTP credentials);
   - `SMTPSenderRefused` or "Email address is not verified": the sender or domain is not verified, or SES is still in the sandbox;
   - `TimeoutError` or `ConnectionRefusedError`: port 587 is blocked (step 3);
   - `SMTPRecipientsRefused`: the citizen's address does not exist; nothing to fix on the server.
9. **The wallet's schedules.** `deploy.sh` (and `deploy/qa/BVT-deploy.sh`) runs `ensure_schedules` after `migrate`, which registers the hourly referral decisions and the daily wallet check. Check once after the first wallet deploy: `gc shell -c "from django_q.models import Schedule; print(sorted(Schedule.objects.values_list('name', flat=True)))"` lists `check-wallet-caches` and `decide-due-referrals`.

## Shared links and WhatsApp previews

`setup.sh` sets `PUBLIC_BASE_URL=https://<DOMAIN>/`; every `share_url`, `invite_url` and preview is built from it. Nginx sends `/p/`, `/app/` and `/.well-known/assetlinks.json` to Django; preview pictures are made once into `MEDIA_ROOT/og/` and served from `/media/og/`.

- **When the app is on Google Play:** set `PLAY_STORE_URL`, and add Play's app signing key to `ANDROID_CERT_SHA256` (Play Console → Setup → App integrity → SHA-256), comma separated after the upload key. Restart. Without it, App Links fail for installs from Play.
- **Check a preview:** send a `/p/<code>/` link on WhatsApp, or paste it into the Facebook Sharing Debugger: it should show the title and the photo. WhatsApp keeps a preview for days, so test with a post that was never shared before.
- **Check App Links:** `curl -s https://<domain>/.well-known/assetlinks.json` must answer JSON with no redirect, and on a phone `adb shell pm get-app-links com.bvt.app` must say `verified`.
- **Pictures for text posts:** put 1200 × 630 JPEGs as `apps/share/static/share/og-<category key>.jpg` (and `og-default.jpg`), then deploy (`collectstatic` copies them).

## Certificates

- Let's Encrypt certificates renew on their own (`certbot.timer`); Nginx reloads after each renewal.
- Check: `sudo certbot renew --dry-run`.
- Moving from staging to real certificates: set `CERTBOT_STAGING=0`, run `sudo certbot delete --cert-name <domain>`, then `setup.sh` again.
- Adding a domain name: add it to `EXTRA_DOMAINS`, point its DNS here, delete the certificate as above, run `setup.sh`.

## Security updates and reboots

Security updates install automatically. Reboots are manual so the app is never restarted by surprise: check `cat /var/run/reboot-required 2>/dev/null` weekly and reboot in a quiet hour (`sudo reboot`). The services start on their own.

## Private repositories

If the repos are private, the deploy user needs read access. GitHub allows one deploy key per repository, so use one key per repo with SSH host aliases:

```
sudo -u deploy ssh-keygen -t ed25519 -N "" -f /home/deploy/.ssh/gc_backend
sudo -u deploy ssh-keygen -t ed25519 -N "" -f /home/deploy/.ssh/gc_web
```

Add each `.pub` file as a read-only deploy key on its repo, then create `/home/deploy/.ssh/config`:

```
Host github-backend
  HostName github.com
  IdentityFile ~/.ssh/gc_backend
Host github-web
  HostName github.com
  IdentityFile ~/.ssh/gc_web
```

and set the URLs in `config.env`, for example `REPO_BACKEND=git@github-backend:irossatech/BVTGramaConnect_BackEnd.git` and `REPO_WEB=git@github-web:irossatech/BVTGramaConnect.git`.
