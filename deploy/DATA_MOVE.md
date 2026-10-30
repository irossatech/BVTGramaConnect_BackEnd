# Moving data from local SQLite to the server database

Development runs on SQLite; the server runs MySQL or PostgreSQL. Only **master and seed data** moves between them:

| Moves | Never moves |
|---|---|
| Geography: states, districts, mandals, villages | Users, role assignments, lockout records (`axes`) |
| Post categories | JWT and blacklist tables (`token_blacklist`) |
| Notification templates | django-q tasks and schedules (`django_q`) |
| | Posts, media, audit log, sessions, Django admin log |

Officials are created on the server, through the console or Django admin. Test posts stay on laptops.

## Preferred: geography from the CSV file

The village list is master data with its own importer. It validates the hierarchy, matches existing areas by LGD code or name, never deletes anything, and can be run again safely. Use it for the first load and for every later correction.

```
# On your machine: copy the file up
scp villages.csv deploy@<server>:/tmp/villages.csv

# On the server: check, then import
gc import_geography /tmp/villages.csv --dry-run
gc import_geography /tmp/villages.csv --missing-report /tmp/missing-coordinates.csv
```

`gc` is the shortcut defined in [RUNBOOK.md](RUNBOOK.md). Run `gc import_geography --help` for the columns.

## Other seed data: dumpdata and loaddata

For categories and notification templates edited locally, or a geography table built on a laptop when no CSV exists.

### 1. Dump on your machine (Windows or Linux)

Run from `BVTGramaConnect_BackEnd` with the virtual environment active. `-X utf8` makes Python write the file as UTF-8 on Windows; without it Telugu names are lost.

```
python -X utf8 manage.py dumpdata geography posts.Category notifications.NotificationTemplate --natural-foreign --natural-primary --indent 2 -o seed.json
```

- Check the model names first with `python manage.py showmigrations`. Leave out any model that does not exist in your version.
- When you dump whole apps instead of named models, also exclude Django's own tables: `--exclude contenttypes --exclude auth.permission --exclude admin.logentry --exclude sessions`.
- Never include `accounts`, `token_blacklist`, `django_q` or `audit`.

Open `seed.json` in an editor and check that a Telugu name reads correctly, for example `"name_te": "కొలకలూరు"`.

### 2. Copy and load on the server

```
scp seed.json deploy@<server>:/tmp/seed.json
gc migrate
gc loaddata /tmp/seed.json
```

`loaddata` keeps the primary keys from the file:
- **Empty server:** load as above.
- **Server already has geography:** do not loaddata geography; the ids would clash or overwrite rows. Export the laptop's villages to the CSV layout and use `import_geography` instead.
- Categories and templates are matched by their key when the models define natural keys; otherwise check the server's rows first with Django admin.

### 3. Check

```
gc shell -c "from apps.geography.models import Village; print(Village.objects.count(), Village.objects.filter(latitude__isnull=True).count())"
```

The first number should match the laptop; the second is the number of villages still without coordinates.

Then take a backup: `sudo bash /srv/gramaconnect/backend/deploy/backup.sh`.

## Copying a whole dev server to a new database (qa)

Everything above moves only master data. When a test server moves to a new account or database
and its testers should keep their logins, posts and settings, copy all of it instead. Rehearsed
SQLite to SQLite: every data table arrives with the same rows, passwords still work, Telugu intact.

1. **Old server, read only:** dump everything except what is rebuilt or must not move:
   ```
   python -X utf8 manage.py dumpdata --natural-foreign --natural-primary \
     --exclude contenttypes --exclude auth.permission --exclude sessions --exclude admin.logentry \
     --exclude django_q --exclude token_blacklist --exclude axes --exclude web_admin.ConsoleAccess \
     -o full.json
   tar -czf media.tgz media
   ```
   `full.json` holds password hashes and personal data: copy it straight to the new server and delete it.
2. **New server**, `DATABASE_URL` set in `.env`: create the tables, empty the rows the migrations
   seeded (categories, contact types, groups …), then load:
   ```
   python manage.py migrate
   python manage.py flush --no-input
   python manage.py loaddata full.json
   tar -xzf media.tgz
   python manage.py ensure_schedules
   ```
   `flush` deletes every row, so run it only on the new, empty database.
3. Sessions and app tokens do not move: everyone logs in again once.
