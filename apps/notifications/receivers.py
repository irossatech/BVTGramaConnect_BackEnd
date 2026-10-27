"""Listen to posts.signals. posts sends them after commit; a receiver must never
raise, because the post change it reports has already been saved."""

import logging

from django.conf import settings
from django.dispatch import receiver

from apps.posts.signals import post_decided, post_escalated, post_published, post_submitted

from . import services
from .models import NotificationKind

logger = logging.getLogger("grama.notifications")


def _safely(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
    except Exception:
        logger.exception("Notification step %s failed", func.__name__)


@receiver(post_published, dispatch_uid="notifications.post_published")
def on_post_published(sender, post, **kwargs):
    _safely(services.announce_to_citizens, post)


@receiver(post_submitted, dispatch_uid="notifications.post_submitted")
def on_post_submitted(sender, post, **kwargs):
    def notify():
        services.notify_users(
            services.reviewers_for(post),
            post=post,
            kind=NotificationKind.REVIEW_PENDING,
            template_key="review_pending",
        )

    _safely(notify)


@receiver(post_decided, dispatch_uid="notifications.post_decided")
def on_post_decided(sender, post, action=None, actor=None, **kwargs):
    _safely(services.tell_author, post, actor)
    _safely(services.tell_citizen, post)


def _escalation_hours() -> int:
    try:
        from apps.posts.services import runtime

        return int(runtime("REVIEW_ESCALATION_HOURS"))
    except Exception:
        return int(settings.REVIEW_ESCALATION_HOURS)


@receiver(post_escalated, dispatch_uid="notifications.post_escalated")
def on_post_escalated(sender, post, level=None, **kwargs):
    _safely(services.alert_escalation, post, level, _escalation_hours())
