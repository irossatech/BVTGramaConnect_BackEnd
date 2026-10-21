"""The console has no tables of its own. ConsoleAccess only carries its permissions."""

from django.db import models


class ConsoleAccess(models.Model):
    """No table (managed = False): a place for the console permissions to live.

    web_admin.access_web_console lets a user open a console session
    (POST /api/v1/auth/session/login/). The others guard the dashboard and
    reports, which the app's state console uses too.
    """

    class Meta:
        managed = False
        default_permissions = ()
        verbose_name = "console access"
        permissions = [
            ("access_web_console", "Log in to the web console"),
            ("view_dashboard", "See the platform dashboard"),
            ("view_reports", "See and download platform reports"),
        ]
