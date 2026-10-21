# Security review before UAT

Date: 2026-10-21. Scope: backend API, ported public website and console, deploy configuration. Checked by reading the code, the automated test suite, and an end-to-end run against a live development server.

## Results

| Area | Status | What was checked |
|---|---|---|
| Login rate limits | Pass | 5 failed logins per username per 15 minutes and 30 per IP per hour, counted in the database so all server processes share them. Nginx adds a 10-per-minute limit on the login and password-change paths. Unknown usernames get the same answer as wrong passwords. |
| Other rate limits | Pass | Contact form: 5 per IP per hour, plus a hidden honeypot field. Nginx limits the whole API to 20 requests per second per IP with a burst of 60. |
| Passwords | Pass with a note | Django's password validators apply to every new password. Invited users must change the role default at first login, and nothing else works until they do. The role default passwords are stored in plain text so they can be emailed; only Super Admins can read them, and they never appear in logs, audit entries or background-job arguments. |
| Token revocation on block | Pass | Blocking bumps the user's token version and blacklists every refresh token in one transaction. The next request with an old token gets 401 session_revoked. Verified by tests and by the end-to-end run. |
| Console CSRF | Pass | The console keeps its access token in memory only. The refresh token sits in an HttpOnly, SameSite=Strict cookie scoped to /api/v1/auth/, and the server refuses cookie requests whose Origin is another site. Every other API call uses the Authorization header, which a browser never sends on its own. |
| Console access | Pass | Only Super Admins can log in to the console; others get not_console_user and no tokens. |
| Area permissions | Pass | Every role and area combination is covered by the permission-matrix tests, including cross-area denials, on SQLite. |
| Uploads | Pass | Photos: only JPEG, PNG and WebP; size limit; decompression-bomb guard; metadata stripped by re-encoding to WebP; at most 5 per post. Videos (owner decision Q8): MP4 or MOV only, checked by extension and by the file's own header; at most 50 MB and 60 seconds, measured on the server with ffprobe; one per post and never with photos; the poster is re-encoded to WebP like a photo. The video file itself is stored as sent, not re-encoded, so its metadata stays. Nginx caps request bodies at 60 MB and serves /media/ with a sandboxing Content-Security-Policy and nosniff. |
| Personal data in logs | Pass | The development server log from the end-to-end run holds no passwords or tokens. Failed email and push deliveries are audited with the address masked. Nginx logs URLs without query strings, so searches by email or mobile number are not recorded. |
| Personal data in URLs | Pass with a note | No endpoint puts a password or token in the URL. The users and audit searches take a q parameter that can contain an email or mobile number; it stays out of Nginx logs, but browsers keep it in their history on the Super Admin's own machine. |
| Public data | Pass | The citizen feed, the upcoming and urgent lists and the website showcase show posts, areas and the publishing role only: no names, mobile numbers or emails of officials. |
| Contact messages | Pass | Only Super Admins can list them or mark them handled (owner decision Q16); everyone else gets 403. Marking one handled is audited with the message number only, never the sender's name or mobile number. |
| Django admin | Pass by config | Nginx allows /django-admin/ only from `OFFICE_IPS` in the deploy config and blocks it for everyone when that is empty (owner decision). |
| Production settings | Pass | Production refuses to start without SECRET_KEY, ALLOWED_HOSTS, DATABASE_URL, MEDIA_ROOT and STATIC_ROOT. `manage.py check --deploy` fails while the email backend is a development one, because invite emails carry passwords. |
| Content-Security-Policy | Accepted risk | The website and console need 'unsafe-eval' and inline styles because of the Design Canvas runtime. All scripts are served from our own origin; no third-party scripts load in production. |

## Open items

1. **Django admin: set the office IPs.** Handled by config: `setup.sh` writes an allow line for each address in `OFFICE_IPS` and `deny all` after them, so with no addresses Django admin is closed. Before go-live, put the office's fixed IP addresses in `OFFICE_IPS` and re-run `setup.sh`. A dynamic office IP would lock the office out.
2. **SMTP credentials.** Invite emails need a real SMTP account in the server env file. Use an account dedicated to this system.
3. **Default role passwords.** They are random on first use. The Super Admin should review them in the console before inviting real officials.
4. **Not tested on a real server.** The deploy scripts pass syntax and shellcheck checks, but Nginx, certificates, firewall and backups have not run on Ubuntu yet.
5. **Video metadata.** Uploaded videos are kept as sent, so a phone's GPS tag in the file stays in it. Stripping it needs a re-encode with ffmpeg; decide before go-live whether that cost is wanted.
