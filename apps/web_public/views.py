from django.conf import settings

from grama.sitefiles import serve_site


def site(request, path=""):
    """The public website from WEB_PUBLIC_DIR, while DEBUG is on."""
    return serve_site(
        request,
        settings.WEB_PUBLIC_DIR,
        path,
        label="Public website",
        setting="WEB_PUBLIC_DIR",
    )
