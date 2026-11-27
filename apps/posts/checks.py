"""System check: the private media folder must never be one a web server serves (BVTGC-192).

Photos of taken-down and discarded posts move to PRIVATE_MEDIA_ROOT. If it sat inside MEDIA_ROOT,
STATIC_ROOT or a web folder, or anywhere under a hosting panel's htdocs (whose nginx serves any file with
a picture extension from the site folder, deploy/qa/README.md), they would stay public. An error stops
`manage.py check`, migrate and the server start, so the deploy stops before anything moves.
"""

from pathlib import Path

from django.conf import settings
from django.core import checks


def _inside(path: Path, folder) -> bool:
    if not folder:
        return False
    try:
        path.relative_to(Path(folder).resolve())
        return True
    except ValueError:
        return False


@checks.register()
def private_media_not_served(app_configs, **kwargs):
    private = Path(settings.PRIVATE_MEDIA_ROOT).resolve()
    served = {
        "MEDIA_ROOT": settings.MEDIA_ROOT,
        "STATIC_ROOT": settings.STATIC_ROOT,
        "WEB_PUBLIC_DIR": getattr(settings, "WEB_PUBLIC_DIR", None),
        "WEB_ADMIN_DIR": getattr(settings, "WEB_ADMIN_DIR", None),
    }
    problem = next((name for name, folder in served.items() if _inside(private, folder)), None)
    if problem is None and not settings.DEBUG and "htdocs" in private.parts:
        problem = "a hosting panel's htdocs"
    if problem is None:
        return []
    return [
        checks.Error(
            f"PRIVATE_MEDIA_ROOT ({private}) is inside {problem}, which a web server serves: photos of "
            "taken-down and discarded posts would stay public.",
            hint="Set PRIVATE_MEDIA_ROOT in .env to a folder outside every served folder, for example "
            "~/.bvt-grama/private-media on a hosting account (the default there).",
            id="posts.E001",
        )
    ]
