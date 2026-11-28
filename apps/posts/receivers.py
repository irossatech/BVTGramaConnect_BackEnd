"""Listen to posts.signals for posts' own follow-up work. Like every receiver, never raise:
the change it reports has already been saved."""

import logging

from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import SettingOverride
from .services import runtime_cache_key
from .signals import post_published
from .tasks import queue_translation

logger = logging.getLogger("grama.translation")


@receiver([post_save, post_delete], sender=SettingOverride, dispatch_uid="posts.forget_runtime_setting")
def forget_runtime_setting(sender, instance, **kwargs):
    """A saved or removed runtime setting is read again at once in this process (services.runtime)."""
    cache.delete(runtime_cache_key(instance.key))


@receiver(post_published, dispatch_uid="posts.translate_on_publish")
def translate_on_publish(sender, post, **kwargs):
    try:
        queue_translation(post.pk)
    except Exception:
        logger.exception("Could not queue the translation of post %s", post.pk)
