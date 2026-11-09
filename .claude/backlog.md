# Backend backlog

Open work for the Backend team, most important first. Update this file as items close.

1. **Office IPs for Django admin.** Nginx now allows `/django-admin/` only from `OFFICE_IPS` (deploy config) and blocks it when that is empty. Get the office's fixed IP addresses from the owner and set them on staging and production.
2. **Console login over HTTPS.** Set `CSRF_TRUSTED_ORIGINS` on staging, then log in to `/console/` over HTTPS: the session and CSRF cookies are Secure, so this is the first real check of the session login, the 30-minute idle logout and the lockout behind Nginx.
3. **Real push.** `apps.notifications.push.FcmGateway` has never sent to Firebase. Put a service-account file on staging (FCM_CREDENTIALS_FILE, FCM_PROJECT_ID) and confirm a topic push and a device push reach a phone.
4. **Staging deploy.** Run `deploy/setup.sh` once on an Ubuntu VM with MySQL and once with PostgreSQL (the server database is not chosen yet; everyone develops on SQLite). Fix whatever the dry run finds. Then run the backup and restore drill in `deploy/RUNBOOK.md`.
5. **Load test at the real size.** Run `deploy/loadtest/locustfile.py` against staging with Gunicorn once the owner gives [EXPECTED USERS].
6. **Video on a real server.** Upload a real 60-second phone video on staging with ffmpeg installed. Check the poster, seeking over 3G (Nginx range requests on `/media/`) and the 50 MB limit end to end. Without ffmpeg the phone must send `duration_s` and a poster.
7. **Citizen posts on a real phone.** Set `PLAY_INTEGRITY_PACKAGE` and a service account with the Play Integrity API enabled (the Firebase one works once the API is switched on in its Google Cloud project), install the app from a Play testing track, and check that registration passes with `CITIZEN_INTEGRITY_REQUIRED=True`. iPhones need Apple App Attest before integrity can be required for them (contract section 15, CP3).
8. **Video metadata.** Videos are stored as sent, so a phone's GPS tag stays in the file. Ask the owner whether to re-encode with ffmpeg to strip it.