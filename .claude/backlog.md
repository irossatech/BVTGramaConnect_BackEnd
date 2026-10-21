# Backend backlog

Open work for the Backend team, most important first. Update this file as items close.

1. **Office IPs for Django admin.** Nginx now allows `/django-admin/` only from `OFFICE_IPS` (deploy config) and blocks it when that is empty. Get the office's fixed IP addresses from the owner and set them on staging and production.
2. **Real email.** Invite and reset emails have only been sent to the console and test outbox. Configure SMTP on staging (EMAIL_* variables) and send one invite and one reset end to end, in Telugu and English.
3. **Real push.** `apps.notifications.push.FcmGateway` has never sent to Firebase. Put a service-account file on staging (FCM_CREDENTIALS_FILE, FCM_PROJECT_ID) and confirm a topic push and a device push reach a phone.
4. **Staging deploy.** Run `deploy/setup.sh` once on an Ubuntu VM with MySQL and once with PostgreSQL (the server database is not chosen yet; everyone develops on SQLite). Fix whatever the dry run finds. Then run the backup and restore drill in `deploy/RUNBOOK.md`.
5. **Load test at the real size.** Run `deploy/loadtest/locustfile.py` against staging with Gunicorn once the owner gives [EXPECTED USERS].
6. **Admin activity report.** `report_admin_activity` in `apps/web_admin/api.py` returns no average review time per admin, so the console shows "—". Add `avg_review_hours` from ReviewAction timestamps.
7. **Video on a real server.** Upload a real 60-second phone video on staging with ffmpeg installed. Check the poster, seeking over 3G (Nginx range requests on `/media/`) and the 50 MB limit end to end. Without ffmpeg the phone must send `duration_s` and a poster.
8. **Video metadata.** Videos are stored as sent, so a phone's GPS tag stays in the file. Ask the owner whether to re-encode with ffmpeg to strip it.
9. **Second roles, console only (Q9).** The role-assignment API still lets area admins add and remove assignments; only the console has a screen for it. Restrict it to Super Admins if the owner wants the server to enforce Q9.
10. **Housekeeping.** LoginAttempt and django-q task rows grow forever; add a scheduled cleanup job.
