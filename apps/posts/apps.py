from django.apps import AppConfig


class PostsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.posts"
    label = "posts"

    def ready(self):
        from . import checks, receivers  # noqa: F401  (private media check; translation on publish)
