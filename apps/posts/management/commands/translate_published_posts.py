"""Queue a translation for published posts that have none yet (contract section 6). Run once after
switching TRANSLATION_PROVIDER on; posts published from then on are translated as they publish.

    python manage.py translate_published_posts --since 2026-10-01 [--limit 500]

The worker (qcluster) does the work. Running it again queues only what is still missing.
"""

from datetime import datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.posts import translation
from apps.posts.models import Post, PostStatus, PostTranslation
from apps.posts.tasks import queue_translation


class Command(BaseCommand):
    help = "Queue a translation for every published post without one (needs TRANSLATION_PROVIDER)."

    def add_arguments(self, parser):
        parser.add_argument("--since", metavar="YYYY-MM-DD", help="Only posts published on or after this date.")
        parser.add_argument("--limit", type=int, metavar="N", help="At most N posts, newest first.")

    def handle(self, *args, since=None, limit=None, **options):
        if not translation.enabled():
            raise CommandError("TRANSLATION_PROVIDER is off: set it to google or bhashini first.")
        posts = Post.objects.filter(status=PostStatus.PUBLISHED).exclude(
            Exists(PostTranslation.objects.filter(post=OuterRef("pk")))
        )
        if since:
            try:
                day = datetime.strptime(since, "%Y-%m-%d").date()
            except ValueError:
                raise CommandError("--since takes a date like 2026-10-01.") from None
            posts = posts.filter(published_at__gte=timezone.make_aware(datetime.combine(day, time.min)))
        ids = posts.order_by("-published_at").values_list("pk", flat=True)
        if limit:
            ids = ids[:limit]
        queued = sum(queue_translation(pk) for pk in ids)
        self.stdout.write(self.style.SUCCESS(f"Queued {queued} post(s) for translation."))
