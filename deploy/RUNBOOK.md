# Runbook

For the person who runs the BVT Grama Connect server. Commands run on the server unless stated otherwise. `sudo` works for the `deploy` user.

## After the first setup

1. **Store the backup passphrase offline.** Print `/etc/gramaconnect/backup.passphrase` or put it in the team's password manager. Without it no backup can be restored.
2. **Create the first Super Admin.** Django admin is at `https://<domain>/django-admin/`.

   ```
   cd /srv/gramaconnect/backend
   sudo -u deploy bash -c 'set -a; . /etc/gramaconnect/gramaconnect.env; set +a; ../venv/bin/python manage.py createsuperuser'
   ```

   It asks for a username, an email, a name and a password. Log in to Django admin, open the new user, and add a role assignment: role `SUPER_ADMIN`, area level `STATE`, area id of Andhra Pradesh. That account can then log in to `/console/` with the same username and password. Everyone else is invited from the console or the app and gets an email.
3. **Load master data**: see [DATA_MOVE.md](DATA_MOVE.md).
4. **Configure email** (SMTP) and **push** (Firebase) below. Until email works, invites and password resets are not delivered and pushes are off.
5. **Set the default passwords** in the console (Settings, "Default passwords by role"). Each role starts with a random one; new officials receive it by email and must change it at first login.
6. Set `STRICT_CHECKS=1` in `/etc/gramaconnect/deploy.env` once `manage.py check --deploy` is clean.

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
| Health | `curl -s https://<domain>/api/v1/health/` |
| Last backups | `systemctl list-timers gramaconnect-backup.timer` and `journalctl -u gramaconnect-backup -n 50` |
| Banned IPs | `sudo fail2ban-client status sshd`, `sudo fail2ban-client status nginx-limit-req` |
| Unban an IP | `sudo fail2ban-client set nginx-limit-req unbanip <ip>` |
| Firewall | `sudo ufw status verbose` |

Common problems:
- **502 Bad Gateway:** Gunicorn is down or still starting. Check its log; `deploy.sh` prints the cause of a failed start.
- **Telugu shows as `????`:** the database or the connection is not utf8mb4. `setup.sh` creates the database with utf8mb4 and Django adds `charset=utf8mb4`; check `SHOW CREATE DATABASE grama;`.
- **413 Request Entity Too Large:** uploads over 12 MB are refused by Nginx (`client_max_body_size` in `templates/nginx-locations.conf`).
- **429 on login:** Nginx allows 10 login or password-change requests a minute per IP (zone `gc_login`). Django adds its own limits: 5 failed tries per username in 15 minutes and 30 per IP an hour (`LOGIN_*` settings).
- **Someone forgot their password:** an admin above them uses "Reset password" in the console or the app. The person gets their role's default password by email and must change it at first login.

## Deploying a release

```
sudo bash /srv/gramaconnect/backend/deploy/deploy.sh
```

It pulls `GIT_BRANCH` of the backend repo and the web repo (BVTGramaConnect, setting `REPO_WEB`), installs packages, migrates, collects static files, builds the web repo once (`npm ci && npm run build`), copies `dist/` (without `console/`) to `/srv/gramaconnect/www/public` and `dist/console/` to `/srv/gramaconnect/www/console`, restarts the services, and checks the API. To roll back, check out the previous commit in the affected repo and run `deploy.sh` again with `GIT_BRANCH` pointing at a branch or tag that holds it. Migrations are not reversed automatically: restore a backup if a migration has to be undone.

### Servers set up before the web repos were merged

The website and the console used to be two repos, cloned to `/srv/gramaconnect/public` and `/srv/gramaconnect/admin`. They are now one repo, BVTGramaConnect, cloned to `/srv/gramaconnect/web`. Nginx and the server env (`WEB_PUBLIC_DIR`, `WEB_ADMIN_DIR`) do not change. On such a server, once:

1. Add `REPO_WEB=https://github.com/irossatech/BVTGramaConnect.git` to the deploy config (`/etc/gramaconnect/config.env`, or the `config.env` you pass to the scripts). Remove `REPO_PUBLIC` and `REPO_ADMIN`.
2. Run `deploy.sh`. On its first run it clones the web repo to `/srv/gramaconnect/web`.
3. Check `/` and `/console/` in a browser, then delete the old clones: `sudo rm -rf /srv/gramaconnect/public /srv/gramaconnect/admin`.

## Backups

- Nightly at 02:30 India time, `backup.sh` dumps the database (`mysqldump --single-transaction` with utf8mb4, or `pg_dump` in custom format), archives `/var/media`, encrypts both with AES-256 (gpg, passphrase file) and writes `/var/backups/gramaconnect/gramaconnect-<time>.tar.gpg`.
- It copies the file to `BACKUP_TARGET` (`rclone:`, `rsync:` or `local:`) and deletes copies older than 14 days in both places.
- Run one now: `sudo bash /srv/gramaconnect/backend/deploy/backup.sh`.
- For an rclone target, set up the remote once as root: `sudo rclone config`. For an rsync target, root needs an SSH key accepted by the backup host.

### Restore

```
sudo bash /srv/gramaconnect/backend/deploy/restore.sh gramaconnect-20261021-023000.tar.gpg
sudo bash /srv/gramaconnect/backend/deploy/restore.sh --db-only <file>
sudo bash /srv/gramaconnect/backend/deploy/restore.sh --media-only <file>
```

The script decrypts, checks checksums, asks you to type the domain, stops the app, replaces the database and/or `/var/media` (the old media folder is kept as `/var/media.before-restore-<time>`), starts the app and checks the API.

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
| `SECRET_KEY` | Delete the `SECRET_KEY=` line from `/etc/gramaconnect/gramaconnect.env`, run `setup.sh` (it generates a new one), then `deploy.sh` | Every JWT is signed with it: all officials must log in again |
| Database password | `sudo rm /etc/gramaconnect/db.password`, run `setup.sh`, then restart the app | `setup.sh` sets a new password in the database, `DATABASE_URL` and the backup credentials |
| Backup passphrase | Replace `/etc/gramaconnect/backup.passphrase` | Old backups still need the old passphrase: keep it until they expire |
| SMTP password | Change it at the mail provider, update `EMAIL_HOST_PASSWORD` in the env file, restart both services | The old password stops working at the provider |
| Firebase key | Replace the JSON file (below), restart | Revoke the old key in the Firebase console |

## Email (SMTP)

Invites and password resets go out by email; there is no SMS.
1. Get SMTP credentials from the mail provider (any provider with SMTP and TLS on port 587 works). Use a sender address on the site's domain and set up SPF and DKIM for it, or the mail will land in spam.
2. Put the values in `config.env` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_USE_TLS`, `MAIL_FROM`) and re-run `setup.sh`, or edit `EMAIL_*` in `/etc/gramaconnect/gramaconnect.env` directly. Set `EMAIL_HOST_PASSWORD` by hand in the env file so the password is not kept in `config.env`.
3. Restart both services: `sudo systemctl restart gramaconnect-gunicorn gramaconnect-qcluster`.
4. `gc check --deploy` no longer reports `accounts.E001`. Invite a test official and check the email arrives; a failed send shows in the audit log as `email.failed`.

## Security notes

- **Login rate limit in Nginx:** `templates/nginx-locations.conf` limits `/api/v1/auth/login/` and `/api/v1/auth/password/change/` to 10 requests a minute per IP (zone `gc_login`, burst 5), before Django's own per-username and per-IP limits.
- **No personal data in access logs:** the site logs with the `gc_noquery` format (`templates/nginx-site-*.conf`), which records the path without the query string. Searches such as `/api/v1/users/?q=<email or mobile>` or `/api/v1/audit/?q=…` therefore never reach `/var/log/nginx`. Keep this format if you change the logging.
- **Temporary passwords** appear only in the invite or reset email and in the console's Settings page for Super Admins. They are never logged or written to the audit log.

## Push notifications (Firebase Cloud Messaging)

1. In the Firebase console, create a service account key for the project (JSON).
2. Copy it to `/etc/gramaconnect/fcm-service-account.json`, then:

   ```
   sudo chown root:deploy /etc/gramaconnect/fcm-service-account.json
   sudo chmod 640 /etc/gramaconnect/fcm-service-account.json
   ```

3. Set `FCM_PROJECT_ID` in `/etc/gramaconnect/gramaconnect.env` if the backend needs it, and restart both services. The qcluster log shows whether pushes are enabled.

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
