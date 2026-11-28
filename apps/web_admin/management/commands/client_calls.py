"""Write docs/client-calls.json: every API call of the web repo and the mobile repo (BVTGC-97).

    python manage.py client_calls                      # ../BVTGramaConnect and ../BVTGramaConnect_Mobile
    python manage.py client_calls --web D:/x/BVTGramaConnect --app D:/x/BVTGramaConnect_Mobile
    python manage.py client_calls --check              # fail when the file is out of date (no write)

Run it on the clients' current `dev` when they change, and commit the file. Each call is also checked
against this server's URLs here, so a client calling an address that does not exist shows at once.
"""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError, no_translations

from grama import clientcalls


class Command(BaseCommand):
    help = "Read the web and mobile repos' API calls into docs/client-calls.json and check them against this server."

    def add_arguments(self, parser):
        parent = Path(settings.BASE_DIR).parent
        parser.add_argument("--web", type=Path, default=parent / "BVTGramaConnect", help="the web repo (reads src/)")
        parser.add_argument("--app", type=Path, default=parent / "BVTGramaConnect_Mobile", help="the mobile repo (reads lib/)")
        parser.add_argument("--check", action="store_true", help="only compare with the committed file")

    @no_translations
    def handle(self, *args, web: Path, app: Path, check: bool, **options):
        for name, root, sub in (("--web", web, "src"), ("--app", app, "lib")):
            if not (root / sub).is_dir():
                raise CommandError(f"{name}: no {sub}/ folder in {root}. Give the repo's folder with {name}.")
        calls, unchecked = clientcalls.scan(web, app)
        document = clientcalls.as_document(calls, unchecked)
        text = clientcalls.dumps(document)

        words = clientcalls.url_words()
        broken = [
            f"  {c['method']} {c['path']}: {issue} ({', '.join(f for files in c['used_by'].values() for f in files)})"
            for c in document["calls"]
            if (issue := clientcalls.problem(c["path"], c["method"], words))
        ]
        self.stdout.write(f"{len(document['calls'])} calls, {len(document['unchecked'])} unreadable (listed as unchecked).")
        if broken:
            self.stdout.write(self.style.WARNING("Calls this server does not answer:\n" + "\n".join(broken)))

        current = clientcalls.OUTPUT.read_text(encoding="utf-8") if clientcalls.OUTPUT.exists() else ""
        if check:
            if current != text:
                raise CommandError(f"{clientcalls.OUTPUT.name} is out of date: run python manage.py client_calls and commit it.")
            self.stdout.write("Up to date.")
            return
        if current == text:
            self.stdout.write("No change.")
            return
        clientcalls.OUTPUT.write_text(text, encoding="utf-8", newline="\n")
        self.stdout.write(f"Wrote {clientcalls.OUTPUT.relative_to(Path(settings.BASE_DIR))}.")
