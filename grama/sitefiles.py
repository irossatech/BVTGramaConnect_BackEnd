"""Serve the ported web prototypes, uploaded files and static files while SERVE_FILES is on.

The public website and the Super Admin console live in their own repos
(see docs/prototype-inventory.md, section 5). In development, and on the qa
server, which has no Nginx, Django serves those folders, /media/ and /static/
so everything runs from one `runserver`, on one origin, with no CORS. That
works with DEBUG off too. On a production server Nginx serves them and these
views answer 404.

Files go out byte for byte. The Design Canvas runtime re-reads its own page
on boot, so the HTML must never be rewritten on the way out.
"""

import mimetypes
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles.views import serve as staticfiles_serve
from django.http import Http404, HttpResponse
from django.utils.html import escape
from django.views.static import serve

# Windows can map .js to text/plain in the registry; browsers then refuse
# to run the script. Pin the types the prototypes use.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".jsx")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("text/html", ".html")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("video/mp4", ".mp4")
mimetypes.add_type("application/json", ".json")

_PLACEHOLDER = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{label}: not ported yet</title></head>
<body style="font-family:system-ui,sans-serif;max-width:640px;margin:48px auto;padding:0 16px;line-height:1.5">
<h1 style="font-size:22px">{label} is not ported yet</h1>
<p>Django is running. It looked for <code>index.html</code> in:</p>
<p><code>{root}</code></p>
<p>Port the prototype into that folder, or point <code>{setting}</code> in <code>.env</code> at it.</p>
<p>API: <a href="/api/v1/health/">/api/v1/health/</a> · Schema: <a href="/api/docs/">/api/docs/</a></p>
</body></html>
"""


def _check_serving() -> None:
    if not settings.SERVE_FILES:
        raise Http404("Served by Nginx on this server.")


def serve_media(request, path: str):
    """Uploaded files: post photos and videos, logos, banners."""
    _check_serving()
    return serve(request, path, document_root=str(settings.MEDIA_ROOT))


def serve_static(request, path: str):
    """Static files of Django's apps (admin, Swagger), found as runserver finds them."""
    _check_serving()
    return staticfiles_serve(request, path, insecure=True)


def serve_site(request, root: Path, path: str, *, label: str, setting: str):
    _check_serving()
    if not path or path.endswith("/"):
        path = f"{path}index.html"
    if path == "index.html" and not (Path(root) / "index.html").is_file():
        html = _PLACEHOLDER.format(label=escape(label), root=escape(str(root)), setting=setting)
        return HttpResponse(html, content_type="text/html; charset=utf-8")
    return serve(request, path, document_root=str(root))
