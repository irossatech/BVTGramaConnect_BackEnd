from django.conf import settings

from grama.sitefiles import serve_site


def console(request, path=""):
    """The Super Admin console from WEB_ADMIN_DIR, while DEBUG is on."""
    return serve_site(
        request,
        settings.WEB_ADMIN_DIR,
        path,
        label="Super Admin console",
        setting="WEB_ADMIN_DIR",
    )
