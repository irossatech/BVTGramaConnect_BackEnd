# Backend backlog

Open work for the Backend team, most important first. Update this file as items close.

1. **Restrict Django admin.** `/django-admin/` is proxied with no restriction (`deploy/templates/nginx-locations.conf`). Add an IP allowlist for the office before go-live, or remove the location.
2. **Real email.** Invite and reset emails have only been sent to the console and test outbox. Configure SMTP on staging (EMAIL_* variables) and send one invite and one reset end to end, in Telugu and English.
3. **Real push.** `apps.notifications.push.FcmGateway` has never sent to Firebase. Put a service-account file on staging (FCM_CREDENTIALS_FILE, FCM_PROJECT_ID) and confirm a topic push and a device push reach a phone.
4. **Staging deploy.** Run `deploy/setup.sh` once on an Ubuntu VM with MySQL and once with PostgreSQL. Fix whatever the dry run finds. Then run the backup and restore drill in `deploy/RUNBOOK.md`.
5. **Load test at the real size.** Run `deploy/loadtest/locustfile.py` against staging with Gunicorn once the owner gives [EXPECTED USERS].
6. **Admin activity report.** `report_admin_activity` in `apps/web_admin/api.py` returns no average review time per admin, so the console shows "—". Add `avg_review_hours` from ReviewAction timestamps.
7. **Fields waiting on decisions.** Posts return `translation` and `event` as `null` (`apps/posts/serializers.py`). Add them when the owner answers Q3 (second language) and Q7 (event dates) in `docs/prototype-inventory.md`, section 8.
8. **Website showcase.** `GET /public/showcase` is not built; the website's phone mockup stays static until Q16 is answered.
9. **PDF export.** Only CSV exists. Add PDF only if the owner asks (Q14).
10. **Housekeeping.** LoginAttempt and django-q task rows grow forever; add a scheduled cleanup job.
