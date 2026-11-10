# qa server on a hosting account without root

The team's test server: one Unix account on a hosting panel, no sudo, no Nginx of our own. Django's
own server runs `grama/settings.py` with `DEBUG=False`, which is production mode: secure cookies,
HSTS, the key check, and `MEDIA_ROOT` and `STATIC_ROOT` required. `.env` turns on what a test server
without Nginx needs: Django serves the website, console and photos itself (`SERVE_FILES=True`),
debug builds of the app may post (`CITIZEN_INTEGRITY_REQUIRED=False`) and Swagger is open
(`API_DOCS_PUBLIC=True`). The data lives in the account's MySQL or MariaDB database. Production on a
server with root uses `deploy/setup.sh` and `deploy/deploy.sh` (Nginx, Gunicorn, HTTPS, backups);
production on a hosting panel uses these scripts with the production settings ("Production on a
hosting panel", below).

Current server: Unix user `bvt_dev`, folder `~/htdocs/dev.bvtgramaconnect.com`, port 9501,
`http://147.93.102.247:9501` (website `/`, console `/console/`, API `/api/v1/`, Swagger `/api/docs/`).

## Layout

```
~/htdocs/<site>/
  BVT-start.sh  BVT-stop.sh  BVT-deploy.sh  BVT-backup.sh    launchers (below)
  BVTGramaConnect_BackEnd/        this repo; the real scripts are in deploy/qa/
  BVTGramaConnect/                the web repo (website and console), cloned next to it
~/.bvt-grama/                     port, PID files, logs
~/bvt-backups/                    nightly backups, readable by the account only
```

The scripts find both repos from where they sit, so no path is ever edited. The launchers in the
site folder only call them, so the commands stay `bash BVT-start.sh` and so on, and every change
to the scripts arrives with `git pull`, reviewed like the rest of the code.

## First setup

1. Clone both repos into the site folder (branch `dev`).
2. Create the launchers once, in the site folder:
   ```
   for s in start stop deploy backup; do
     printf '#!/usr/bin/env bash\nexec bash "$(dirname "$(readlink -f "$0")")/BVTGramaConnect_BackEnd/deploy/qa/BVT-%s.sh" "$@"\n' "$s" > "BVT-$s.sh"
   done
   ```
3. The database: see below. Without `DATABASE_URL` the deploy stops, so it never migrates a
   stray SQLite file by mistake.
4. `SERVER_IP=147.93.102.247 PORT=9501 bash BVT-deploy.sh`. It writes `.env` on the first run
   (a new `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS`, the web folders, `MEDIA_ROOT`,
   `STATIC_ROOT`, `SERVE_FILES=True`, `CITIZEN_INTEGRITY_REQUIRED=False`, `API_DOCS_PUBLIC=True`),
   so on a brand-new server run it once, add the database line, and run it again. A `.env` from
   before the single settings file (PR #73) lacks the five lines after the web folders: add them
   by hand, or the server stops with "Set the MEDIA_ROOT environment variable".
5. `bash BVT-backup.sh --install`: a backup now and every night at 02:30.

## The database

Check the login first. If this says "Access denied", the hosting admin must fix the account
(the password, or the user for `localhost`, or its rights on the database); no code can.
```
mariadb -u <user> -p -h 127.0.0.1 -P 3306 <database> -e "SELECT VERSION();"
```
Then add the line to `.env`. The password is asked without being shown or kept in the shell
history, and characters such as `&` or `@` are encoded:
```
cd ~/htdocs/<site>/BVTGramaConnect_BackEnd
read -rsp "Database password: " DBPASS; echo
enc=$(DBPASS="$DBPASS" python3 -c 'import os,urllib.parse; print(urllib.parse.quote(os.environ["DBPASS"], safe=""))')
printf 'DATABASE_URL=mysql://<user>:%s@127.0.0.1:3306/<database>\n' "$enc" >> .env
unset DBPASS enc
```
If only `-h localhost` logs in, write `localhost` instead of `127.0.0.1`. Check before loading
anything: `.venv/bin/python manage.py shell -c "from django.db import connection as c; print(c.vendor, c.settings_dict['NAME'])"`
must print `mysql <database>`.

## Copying another server's data

`deploy/DATA_MOVE.md`, "Copying a whole dev server". Accounts cannot switch into each other
(`su` is blocked), so the old account dumps into a folder in `/tmp` with a random name and mode
711, the new one copies the files by name into its own folder, and the old one deletes the `/tmp`
folder at once.

## Running without a terminal

`BVT-start.sh` starts Django and the worker detached from the terminal (`setsid nohup`), and adds
a crontab line that runs `BVT-start.sh --if-down` every minute. That keep-alive starts both again
when either is not running: after a logout on a host that ends every process of a login, after a
crash, after a reboot. It does nothing while they run, or while a start is under way (a lock).
`BVT-stop.sh` leaves a "stopped" mark that the keep-alive respects, so loading data or a restore
is never interrupted; the next `BVT-start.sh` removes it. What the keep-alive did is in
`~/.bvt-grama/logs/keepalive.log`; `crontab -l` shows the line.

## Every release

```
bash BVT-deploy.sh
```
It pulls both repos, installs packages, migrates, refreshes the background schedules and restarts
on the remembered port. The website and console come from the web repo's committed `dist/`, as
the web developer built them; the server builds them (with Node.js 20.9+) only when the repo has no
`dist/`. A `dist/` built here before is removed so the pull can bring the committed one. It stops
before changing anything if someone edited tracked files on the server, so never run
`npm run build` in the web repo on the server. `SEED=1` is refused once villages exist: the sample
data would write its own places over the real list. Demo content for real villages:
`manage.py load_demo_area --allow-server` (`.claude/operations.md`). Both sample-data commands
refuse on a server without `--allow-server`; never use it on production.

## Restoring a backup

Into an empty database, or after `flush` (which deletes every row: never on the wrong database):
```
bash BVT-stop.sh
cd BVTGramaConnect_BackEnd
.venv/bin/python manage.py migrate
.venv/bin/python manage.py flush --no-input
gunzip -c ~/bvt-backups/<folder>/data.json.gz > /tmp/restore-$$.json
.venv/bin/python manage.py loaddata /tmp/restore-$$.json && rm /tmp/restore-$$.json
tar -xzf ~/bvt-backups/<folder>/media.tgz
.venv/bin/python manage.py ensure_schedules
cd .. && bash BVT-start.sh
```
Sessions and app tokens are not in a backup: everyone logs in again once.

## The domain

When the hosting panel forwards `https://<domain>` to the port, add to `.env` and restart:
```
ALLOWED_HOSTS=<server ip>,<domain>,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://<domain>
TRUST_PROXY_HTTPS=True
SESSION_COOKIE_SECURE=True
CSRF_COOKIE_SECURE=True
```
Without `CSRF_TRUSTED_ORIGINS` the console's login fails through the domain.

For shared post links and WhatsApp previews, also add `PUBLIC_BASE_URL=https://<domain>/`.
`/p/<code>/` and `/app/` reach Django, and preview pictures sit in `media/og/` (served
through the `media` link). `/.well-known/assetlinks.json` ends in `.json`, so the hosting
nginx looks for a file in the site folder instead: App Links do not work on qa, which is
fine until the final domain.

The hosting panel's nginx serves files with static extensions (`.js`, `.css`, `.png`, …)
from the site folder itself and never forwards them to the port. Until the hosting
company points those paths at the build folders, links in the site folder stand in:
```
cd ~/htdocs/<site folder>
ln -s BVTGramaConnect/dist/_next _next
ln -s BVTGramaConnect/dist/_ds _ds
ln -s BVTGramaConnect/dist/assets assets
ln -s BVTGramaConnect/dist/fonts fonts
ln -s BVTGramaConnect/dist/downloads downloads
ln -s BVTGramaConnect_BackEnd/media media
ln -s BVTGramaConnect_BackEnd/staticfiles static
```
`downloads` holds the app's APK (`/downloads/bvt-grama-connect.apk`). `static` holds what every
deploy collects (`collectstatic`): the pictures WhatsApp shows for shared posts without a photo
(`/static/share/og-*.jpg`), and the Django admin and API docs styles.
Remove them (`rm _next _ds assets fonts downloads media`) once nginx serves those folders. This is fine
for qa only: production uses `deploy/setup.sh`, whose nginx serves the website, the
console, `/static/` and `/media/` with their cache rules and sends only `/api/` and
`/django-admin/` to Gunicorn.

## Push notifications

The Firebase key is a secret. Keep it outside `~/htdocs`, because the hosting nginx serves
`.json` files from the site folder to anyone:
```
mkdir -p ~/secrets && chmod 700 ~/secrets
# copy the key there as fcm-service-account.json (scp from your laptop), then:
chmod 600 ~/secrets/fcm-service-account.json
```
Add to `.env` (`<home>` is the output of `echo $HOME`) and restart with `bash BVT-stop.sh && bash BVT-start.sh`:
```
FCM_CREDENTIALS_FILE=<home>/secrets/fcm-service-account.json
FCM_PROJECT_ID=<project id>
FCM_TOPIC_PREFIX=qa_
```
`FCM_TOPIC_PREFIX=qa_` is required on qa: qa and production share the Firebase project, so
without it a test post would reach real users. QA builds of the app follow the `qa_` topics.
The worker sends the pushes, so `BVT-start.sh` must show it running; its log must not say
"FCM disabled".

## Production on a hosting panel

When production has to run on the same kind of account (no sudo, the panel's nginx in front, as
for www.gramaconnect.com on 147.93.102.247), the same scripts run it with the production
settings. Both run `grama/settings.py` with `DEBUG=False`. The scripts tell production from qa by
one line in the backend's `.env`, `DJANGO_SETTINGS_MODULE=grama.settings.prod` (only a marker for
the scripts; the settings go by `DEBUG`), which the first production deploy writes:

| | qa | production on the panel |
|---|---|---|
| Settings | `DEBUG=False`, plus `SERVE_FILES`, `API_DOCS_PUBLIC` and integrity off for test builds | `DEBUG=False`: secure cookies, HSTS, the key and host checks |
| Web server | `runserver` on `0.0.0.0:PORT` | Gunicorn on `127.0.0.1:PORT` (3 workers, `WEB_WORKERS`), no access log |
| Branch | `dev` | `main` (`BRANCH=dev` to override) |
| Deploy also runs | | `check --deploy` before migrating, `collectstatic` |
| Refused | | `SEED=1`, `QA_SQLITE=1` |

A production `.env` written before the single settings file (PR #73) has no `DEBUG` line, because
the old `prod.py` forced it off. Add `DEBUG=False` before deploying that version, or the site runs
in debug mode on a SQLite file.

Its own panel site and Unix user, its own MySQL database, its own port: nothing is shared with qa.

1. **In the panel** (whoever administers it): a reverse-proxy site for `www.gramaconnect.com`
   (and `gramaconnect.com`) to `http://127.0.0.1:9601`, with its own SSH user, Let's Encrypt
   certificate, and a MySQL database and user. With Cloudflare in front, its SSL mode is
   "Full (strict)".
2. **As that site's user**, in its site folder: clone both repos and create the launchers as in
   "First setup", steps 1 and 2.
3. `MODE=production DOMAIN=www.gramaconnect.com EXTRA_DOMAINS=gramaconnect.com PORT=9601 bash BVT-deploy.sh`.
   The first run writes the production `.env` and stops at the database: add `DATABASE_URL` as in
   "The database", and run the same command again. Later releases: `bash BVT-deploy.sh`.
4. **The links** the panel's nginx needs, because it serves every address with a file extension
   from the site folder:
   ```
   cd ~/htdocs/<site folder>
   ln -s BVTGramaConnect/dist/_next _next
   ln -s BVTGramaConnect/dist/_ds _ds
   ln -s BVTGramaConnect/dist/assets assets
   ln -s BVTGramaConnect/dist/fonts fonts
   ln -s BVTGramaConnect/dist/downloads downloads
   ln -s BVTGramaConnect_BackEnd/media media
   ln -s BVTGramaConnect_BackEnd/staticfiles static
   ```
   Every production deploy also writes `.well-known/assetlinks.json` into the site folder
   (`manage.py write_assetlinks`), because the panel's nginx serves `.json` files from there:
   that is what lets shared `/p/` links open the app. Check: `curl -s https://<domain>/.well-known/assetlinks.json`.
   The console's own files (`/console/vendor/…`, `/console/assets/…`) need `console`:
   `ln -s BVTGramaConnect/dist/console console` only if `https://<domain>/console/support.js`
   answers 404 without it.
5. **Push:** a new key for the `fcm-sender` account (Google Cloud → IAM → Service accounts →
   fcm-sender → Keys), copied to `~/secrets/fcm-service-account.json` (mode 600, never in
   `~/htdocs`), then in `.env`: `FCM_CREDENTIALS_FILE=/home/<user>/secrets/fcm-service-account.json`.
   `FCM_PROJECT_ID=bvt-grama-connect` and an empty `FCM_TOPIC_PREFIX` are written already.
6. **Data:** geography, categories, numbers (`deploy/DATA_MOVE.md`), then the first Super Admin:
   `.venv/bin/python manage.py create_super_admin`.
7. **Real visitor addresses.** Lockouts and limits count per address, so Django must see the
   visitor's, not the proxy's or Cloudflare's. Try the console login once with a wrong password
   (failed logins are recorded with their address), then:
   ```
   .venv/bin/python manage.py shell -c "from axes.models import AccessFailureLog; print(list(AccessFailureLog.objects.order_by('-attempt_time').values_list('ip_address', flat=True)[:3]))"
   ```
   It must show your own public address (search "what is my IP"). `127.0.0.1` or a Cloudflare
   address means the panel does not pass the real one: stop and fix that before launch.
8. `bash BVT-backup.sh --install`.

`.env` also carries the owner's decision of 2026-11-04, `CITIZEN_INTEGRITY_REQUIRED=False`:
citizens may post from the website's APK until the app is on Google Play. Remove the line and
restart on the day it is.

## Stopping for good

`bash BVT-stop.sh --disable` stops both processes and removes the keep-alive line;
`bash BVT-backup.sh --uninstall` removes the nightly backup.
