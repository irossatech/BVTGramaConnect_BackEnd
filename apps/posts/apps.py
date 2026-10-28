from django.apps import AppConfig


class PostsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.posts"
    label = "posts"

    def ready(self):
        from . import receivers  # noqa: F401  (translation on publish)
