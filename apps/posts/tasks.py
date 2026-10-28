"""Background jobs for posts, run by django-q2."""

import logging
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import translation
from .models import Post, PostStatus, PostTranslation
from .services import escalate_overdue

ESCALATION_SCHEDULE = "escalate-overdue-posts"
TRANSLATE_TASK = "apps.posts.tasks.translate_post"
logger = logging.getLogger("grama.translation")


def translate_post(post_id: str, attempt: int = 1) -> bool:
    """Translate a published post into the language it is not written in (contract section 6).

    Queued when a post is published. Skips a post that is no longer published or whose
    translation is current. A failed try is retried after TRANSLATION_RETRY_MINUTES (1, 5,
    15) through a one-off schedule, so no worker sleeps; after the last one the post simply
    has no translation. Publishing never waits for this.
    """
    translator = translation.get_translator()
    post = Post.objects.filter(pk=post_id, status=PostStatus.PUBLISHED).first()
    if translator is None or post is None:
        return False
    target, source_hash = post.other_language, post.source_hash
    current = PostTranslation.objects.filter(post=post, language=target).first()
    if current is not None and current.source_hash == source_hash:
        return True
    try:
        title, body = translation.translate_post_text(translator, post.title, post.body, post.language, target)
    except Exception as error:
        _retry_later(post_id, attempt, error)
        return False
    if not title.strip() or not body.strip():
        logger.warning("Translation of post %s came back empty; not saved.", post_id)
        return False
    PostTranslation.objects.update_or_create(
        post=post, language=target,
        defaults={"title": title, "body": body, "provider": translator.name, "source_hash": source_hash},
    )
    return True


def _retry_later(post_id: str, attempt: int, error: Exception) -> None:
    waits = settings.TRANSLATION_RETRY_MINUTES
    # The error's type only: its message may hold the post's text.
    if attempt > len(waits):
        logger.warning("Translating post %s failed %d times (%s); it stays untranslated.",
                       post_id, attempt, type(error).__name__)
        return
    from django_q.models import Schedule

    logger.info("Translating post %s failed (%s); trying again in %d min.", post_id, type(error).__name__,
                waits[attempt - 1])
    Schedule.objects.create(
        name=f"translate-{post_id}-{attempt + 1}",
        func=TRANSLATE_TASK,
        args=repr((str(post_id), attempt + 1)),
        schedule_type=Schedule.ONCE,
        next_run=timezone.now() + timedelta(minutes=waits[attempt - 1]),
    )


def queue_translation(post_id) -> bool:
    """Queue translate_post, unless TRANSLATION_PROVIDER is off."""
    if not translation.enabled():
        return False
    from django_q.tasks import async_task

    async_task(TRANSLATE_TASK, str(post_id), group="translate")
    return True


def run_escalation() -> int:
    """Scheduled every ESCALATION_CHECK_MINUTES by `manage.py ensure_schedules`."""
    return escalate_overdue()


def ensure_escalation_schedule() -> None:
    from django_q.models import Schedule

    Schedule.objects.update_or_create(
        name=ESCALATION_SCHEDULE,
        defaults={
            "func": "apps.posts.tasks.run_escalation",
            "schedule_type": Schedule.MINUTES,
            "minutes": settings.ESCALATION_CHECK_MINUTES,
            "repeats": -1,
        },
    )
