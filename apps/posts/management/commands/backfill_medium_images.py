"""Make the 640 px file (POST_MEDIUM_PX) for photos and video posters uploaded before it existed.

    python manage.py backfill_medium_images --dry-run    # count only
    python manage.py backfill_medium_images

Reads each stored 1280 px WebP and writes `…-640.webp` next to it, in batches
of 200. Safe to run again: rows that already have the file are skipped. Until
a row has it, the feed's image_url sends the 1280 px file, so nothing breaks
while it runs. A missing or unreadable large file is reported and left alone.
"""

from django.core.management.base import BaseCommand

from apps.posts.media import add_medium
from apps.posts.models import PostMedia

BATCH = 200


class Command(BaseCommand):
    help = "Make the 640 px file for photos and video posters that do not have one yet. Safe to run again."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Only count the rows that need the file.")

    def handle(self, *args, dry_run, **options):
        missing = PostMedia.objects.filter(medium="")
        if dry_run:
            self.stdout.write(f"{missing.count()} photo(s) or poster(s) need the medium file. Nothing written.")
            return
        made = failed = 0
        last = None
        while True:
            batch = missing.select_related("post").order_by("pk")
            if last is not None:
                batch = batch.filter(pk__gt=last)
            rows = list(batch[:BATCH])
            if not rows:
                break
            for media in rows:
                if add_medium(media):
                    made += 1
                else:
                    failed += 1
                    self.stdout.write(self.style.WARNING(f"Could not read the large file of media {media.pk}."))
            last = rows[-1].pk
        self.stdout.write(self.style.SUCCESS(f"Medium files made: {made}. Could not read: {failed}."))
