"""Listen to posts.signals for posts' own follow-up work. Like every receiver, never raise:
the change it reports has already been saved."""

import logging

from django.dispatch import receiver

from .signals import post_published
from .tasks import queue_translation

logger = logging.getLogger("grama.translation")


@receiver(post_published, dispatch_uid="posts.translate_on_publish")
def translate_on_publish(sender, post, **kwargs):
    try:
        queue_translation(post.pk)
    except Exception:
        logger.exception("Could not queue the translation of post %s", post.pk)
