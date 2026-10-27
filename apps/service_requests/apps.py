from django.apps import AppConfig


class ServiceRequestsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.service_requests"
    label = "service_requests"
    verbose_name = "Service requests"
