# qa server on a hosting account without root

The team's test server: one Unix account on a hosting panel, no sudo, no Nginx of our own. Django's
own server runs the dev settings with `DEBUG=False` and serves the website, console and photos itself
(`SERVE_FILES`); the data lives in the account's MySQL or MariaDB database. Production uses
`deploy/setup.sh` and `deploy/deploy.sh` instead (root, Nginx, Gunicorn, HTTPS, backups).

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
   (a new `SECRET_KEY`, `DEBUG=False`, `ALLOWED_HOSTS`, the web folders), so on a brand-new server
   run it once, add the database line, and run it again.
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

## Every release

```
bash BVT-deploy.sh
```
It pulls both repos, builds the website and console, installs packages, migrates, refreshes the
background schedules and restarts on the remembered port. It stops before changing anything if
someone edited tracked files on the server. `SEED=1` is refused once villages exist: the sample
data would write its own places over the real list. Demo content for real villages:
`manage.py load_demo_area` (`.claude/operations.md`).

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

## Stopping for good

`bash BVT-stop.sh --disable` stops both processes and removes the autostart;
`bash BVT-backup.sh --uninstall` removes the nightly backup.
