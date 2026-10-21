# Deploying BVT Grama Connect

Scripts for one Ubuntu LTS server (22.04 or 24.04) in India. The database is MySQL 8 or PostgreSQL, chosen by one setting. Everything is safe to run again.

| File | What it does |
|---|---|
| `config.env.example` | Every setting: domain, database engine, repos, backup target |
| `setup.sh` | Prepares the server from scratch, then runs the first deploy |
| `deploy.sh` | Pulls the code, installs, migrates, builds the web parts, restarts |
| `backup.sh` | Encrypted database + media backup; runs nightly from a systemd timer |
| `restore.sh` | Restores a backup made by `backup.sh` |
| `templates/` | systemd units, Nginx and fail2ban files that `setup.sh` fills in |
| [RUNBOOK.md](RUNBOOK.md) | Day-to-day operation, restore drill, secrets, push, security notes, certificates |
| [DATA_MOVE.md](DATA_MOVE.md) | Moving master and seed data from a laptop's SQLite to the server |

## First setup

1. Point the domain's DNS A record (and any extra names) at the server.
2. Log in as root with an SSH key. Copy the repo's `deploy/` folder to the server, or clone the backend repo to `/root`.
3. Copy `config.env.example` to `config.env` and edit it: domain, email, `DB_ENGINE=mysql` or `postgresql`, repo URLs, `BACKUP_TARGET`.
4. Run:

   ```
   sudo bash deploy/setup.sh deploy/config.env
   ```

5. Follow "After the first setup" in [RUNBOOK.md](RUNBOOK.md): store the backup passphrase offline, load the master data, create the first Super Admin with `create_super_admin`, configure push.

## Every release

```
sudo bash /srv/gramaconnect/backend/deploy/deploy.sh
```

## What ends up where

| Path | Contents |
|---|---|
| `/srv/gramaconnect/backend` | Django code (BVTGramaConnect_BackEnd) |
| `/srv/gramaconnect/web` | Web repo (BVTGramaConnect: website and console) |
| `/srv/gramaconnect/www/public`, `/srv/gramaconnect/www/console` | Published web files, served by Nginx at `/` and `/console/` |
| `/srv/gramaconnect/venv` | Python virtual environment |
| `/srv/gramaconnect/static` | Django static files, served at `/static/` |
| `/var/media` | Uploaded photos, served at `/media/` |
| `/etc/gramaconnect/gramaconnect.env` | Django environment: secrets, `DATABASE_URL` |
| `/etc/gramaconnect/deploy.env` | Copy of `config.env` |
| `/var/backups/gramaconnect` | Encrypted backups, 14 days |
