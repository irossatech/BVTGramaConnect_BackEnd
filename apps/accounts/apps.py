from django.apps import AppConfig
from django.db.models.signals import post_migrate


class AccountsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.accounts"
    label = "accounts"
    verbose_name = "Accounts and roles"

    def ready(self):
        from . import lockout, schema, signals  # noqa: F401  (connect receivers, OpenAPI extensions)
        from .sync import sync_after_migrate

        # Every migrate ends by applying role_matrix.py, so deploys pick up changes.
        post_migrate.connect(sync_after_migrate, sender=self, dispatch_uid="sync_role_permissions")
