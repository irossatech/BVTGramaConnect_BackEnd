"""Write /.well-known/assetlinks.json as a plain file.

Behind a hosting panel's nginx (deploy/qa/README.md, "Production on a hosting panel"), every
address with a file extension is served from the site folder and never reaches Django, so the
App Links file must sit there. The production deploy runs:

    manage.py write_assetlinks <site folder>/.well-known/assetlinks.json

It holds the same JSON as the view, from ANDROID_PACKAGE and ANDROID_CERT_SHA256.
"""

import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand

from apps.share.views import assetlinks_statements


class Command(BaseCommand):
    help = "Write the Android App Links file (assetlinks.json) to PATH."

    def add_arguments(self, parser):
        parser.add_argument("path", help="For example ~/htdocs/<site>/.well-known/assetlinks.json")

    def handle(self, *args, **options):
        target = Path(options["path"]).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".partial")
        partial.write_text(json.dumps(assetlinks_statements(), indent=2) + "\n", encoding="utf-8")
        os.replace(partial, target)  # never a half-written file in front of Google's check
        target.chmod(0o644)
        self.stdout.write(f"Wrote {target}")
