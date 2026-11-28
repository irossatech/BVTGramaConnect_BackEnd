from django.apps import AppConfig


class BillsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.bills"
    label = "bills"
    verbose_name = "Bills and recharges"
