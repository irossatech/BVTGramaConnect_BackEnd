"""Sample data for development, demos and UAT.

2 districts × 2 mandals × 3 villages, one official per role, and posts in every
state: DRAFT, PENDING, PUBLISHED, RECHECK, DISCARDED, UNPUBLISHED. Posts are
made through posts.services, so they carry real review history and audit rows.

It also loads the statewide helplines and the mobile app's demo important numbers
(VRO, Sachivalayam, Tahsildar office …) with made-up numbers.

Owner decisions add three things: posts with an event date (Q7) for "Don't
miss", an urgent post (Q19) for the banner, and a media check that says
whether uploads can be stored and whether ffprobe and ffmpeg are installed
for video posts (Q8). Event dates are relative to today, so "Don't miss" is
never empty.

Safe to run again: geography goes through the idempotent importer, officials
are matched by username, and posts are only added once (each extra post is
matched by author and title). Each new official gets a generated temporary
password, printed once at the end, and must change it at first login, as an
account made by an admin would. --reset-passwords gives every sample official
a new one.
"""

import shutil
import tempfile
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.credentials import generate_password
from apps.accounts.models import RoleAssignment, RoleGroup, User
from apps.accounts.roles import Role
from apps.geography.areas import AreaRef
from apps.geography.importer import GeographyImporter
from apps.geography.models import District, Mandal, State, Village

HEADER = "state,state_te,district,district_te,mandal,mandal_te,village,village_te,latitude,longitude,pin_code\n"
VILLAGES = [
    ("Guntur", "గుంటూరు", "Tenali", "తెనాలి", "Kolakaluru", "కొలకలూరు", "16.2381", "80.5924", "522306"),
    ("Guntur", "గుంటూరు", "Tenali", "తెనాలి", "Nandivelugu", "నందివెలుగు", "16.2176", "80.6232", "522306"),
    ("Guntur", "గుంటూరు", "Tenali", "తెనాలి", "Pedaravuru", "పెదరావూరు", "16.2009", "80.5703", "522201"),
    ("Guntur", "గుంటూరు", "Mangalagiri", "మంగళగిరి", "Kaza", "కాజా", "16.4106", "80.5490", "522503"),
    ("Guntur", "గుంటూరు", "Mangalagiri", "మంగళగిరి", "Chinakakani", "చినకాకాని", "16.4512", "80.5581", "522508"),
    ("Guntur", "గుంటూరు", "Mangalagiri", "మంగళగిరి", "Nidamarru", "నిడమర్రు", "16.4650", "80.5290", "522503"),
    ("Krishna", "కృష్ణా", "Gannavaram", "గన్నవరం", "Nidamanuru", "నిడమానూరు", "16.5312", "80.7124", "521104"),
    ("Krishna", "కృష్ణా", "Gannavaram", "గన్నవరం", "Kesarapalli", "కేసరపల్లి", "16.5300", "80.8000", "521102"),
    ("Krishna", "కృష్ణా", "Gannavaram", "గన్నవరం", "Buddhavaram", "బుద్ధవరం", "16.5480", "80.7900", "521101"),
    ("Krishna", "కృష్ణా", "Penamaluru", "పెనమలూరు", "Poranki", "పోరంకి", "16.4740", "80.7050", "521137"),
    ("Krishna", "కృష్ణా", "Penamaluru", "పెనమలూరు", "Kanuru", "కానూరు", "16.4880", "80.6900", "520007"),
    ("Krishna", "కృష్ణా", "Penamaluru", "పెనమలూరు", "Yanamalakuduru", "యనమలకుదురు", "16.4880", "80.6680", "520007"),
]

# name, Telugu name, username, mobile, role, (level, English area name)
OFFICIALS = [
    ("Sita Mahalakshmi", "సీత మహాలక్ష్మి", "super.admin", "9100000001", Role.SUPER_ADMIN, ("STATE", "Andhra Pradesh")),
    ("Prasad Rao", "ప్రసాద్ రావు", "district.guntur", "9100000002", Role.DISTRICT_ADMIN, ("DISTRICT", "Guntur")),
    ("Lakshmi Devi", "లక్ష్మి దేవి", "mandal.tenali", "9100000003", Role.MANDAL_ADMIN, ("MANDAL", "Tenali")),
    ("Suresh Babu", "సురేష్ బాబు", "village.kolakaluru", "9100000004", Role.VILLAGE_ADMIN, ("VILLAGE", "Kolakaluru")),
    ("Ravi Kumar", "రవి కుమార్", "member.kolakaluru", "9100000005", Role.PANCHAYAT_MEMBER, ("VILLAGE", "Kolakaluru")),
]


# Important numbers (docs/api-contract.md, section 14): the mobile app's demo data, made-up numbers.
# The Nandivelugu and Mangalagiri rows are what a Kolakaluru citizen must NOT see.
SAMPLE_CONTACTS_CSV = """type,name,name_te,phone,district,mandal,village,consent
vro,K. Srinivasa Rao,కె. శ్రీనివాసరావు,9000010001,Guntur,Tenali,Kolakaluru,yes
sachivalayam,Kolakaluru Grama Sachivalayam,కొలకలూరు గ్రామ సచివాలయం,9000010002,Guntur,Tenali,Kolakaluru,yes
meeseva,Mee Seva Centre Kolakaluru,మీ సేవ కేంద్రం కొలకలూరు,9000010003,Guntur,Tenali,Kolakaluru,yes
revenue,"Tahsildar Office, Tenali","తహసీల్దార్ కార్యాలయం, తెనాలి",9000010004,Guntur,Tenali,,yes
official,"MPDO Office, Tenali","ఎంపీడీఓ కార్యాలయం, తెనాలి",9000010005,Guntur,Tenali,,yes
vro,P. Lakshmi,పి. లక్ష్మి,9000010006,Guntur,Tenali,Nandivelugu,yes
official,"Collectorate, Guntur","కలెక్టరేట్, గుంటూరు",9000010007,Guntur,,,yes
revenue,"RDO Office, Tenali Division","ఆర్డీఓ కార్యాలయం, తెనాలి డివిజన్",9000010008,Guntur,,,yes
revenue,"Tahsildar Office, Mangalagiri","తహసీల్దార్ కార్యాలయం, మంగళగిరి",9000010009,Guntur,Mangalagiri,,yes
"""


def area_ref(level: str, name: str) -> AreaRef:
    model = {"STATE": State, "DISTRICT": District, "MANDAL": Mandal, "VILLAGE": Village}[level]
    return AreaRef(level, model.objects.get(name_en=name).id)


class Command(BaseCommand):
    help = "Load sample geography, one official per role and posts in every state. Safe to run again."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-passwords", action="store_true",
            help="Give every sample official a new temporary password and print it.",
        )

    def handle(self, *args, reset_passwords=False, **options):
        out = getattr(self.stdout, "_out", None)
        if hasattr(out, "reconfigure"):
            out.reconfigure(errors="backslashreplace")

        csv_text = HEADER + "".join(
            f"Andhra Pradesh,ఆంధ్రప్రదేశ్,{d},{dte},{m},{mte},{v},{vte},{lat},{lng},{pin}\n"
            for d, dte, m, mte, v, vte, lat, lng, pin in VILLAGES
        )
        report = GeographyImporter().run_text(csv_text)
        if not report.ok:
            raise SystemExit("\n".join(report.errors))
        self.stdout.write(f"Geography: {Village.objects.count()} villages in the database.")

        users = {}
        passwords = {}
        with transaction.atomic():
            for name, name_te, username, mobile, role, (level, area_name) in OFFICIALS:
                user, created = User.objects.get_or_create(
                    username=username,
                    defaults={
                        "name": name,
                        "name_te": name_te,
                        "email": f"{username}@example.org",
                        "mobile": mobile,
                    },
                )
                ref = area_ref(level, area_name)
                group = RoleGroup.objects.get(key=role).group
                RoleAssignment.objects.get_or_create(user=user, group=group, area_level=ref.level, area_id=ref.id)
                if created or reset_passwords:
                    passwords[username] = generate_password()
                    user.set_password(passwords[username])
                    # The forced change is switched on after the sample posts are written.
                    user.must_change_password = False
                    user.save(update_fields=["password", "must_change_password", "updated_at"])
                users[role] = user

        made = self._posts(users)
        self.stdout.write(self.style.SUCCESS(f"Posts: {made} created." if made else "Posts: already present."))
        extra = self._event_and_urgent_posts(users)
        self.stdout.write(
            self.style.SUCCESS(f"Event and urgent posts: {extra} created.") if extra
            else "Event and urgent posts: already present."
        )
        contacts = self._contacts()
        self.stdout.write(
            self.style.SUCCESS(f"Important numbers: {contacts} created.") if contacts
            else "Important numbers: already present."
        )
        self._media_check()
        if passwords:
            User.objects.filter(username__in=list(passwords)).update(must_change_password=True)
        self.stdout.write(
            "Officials. Temporary passwords are shown once; each must be changed at first login:"
        )
        for name, _te, username, _mobile, role, (level, area_name) in OFFICIALS:
            password = passwords.get(username, "(unchanged)")
            self.stdout.write(f"  {username:<20} {password:<14} {role:<17} {area_name}  ({name})")

    def _contacts(self) -> int:
        """The statewide helplines and the app's demo area numbers, through the import (same rules, idempotent)."""
        from apps.contacts.management.commands.seed_helplines import DEFAULT_FILE
        from apps.contacts.services import import_contacts

        made = 0
        for text in (DEFAULT_FILE.read_text(encoding="utf-8-sig"), SAMPLE_CONTACTS_CSV):
            report = import_contacts(None, text, via="system")
            if not report.ok:
                raise SystemExit("\n".join(f"row {e['row']} {e['field']}: {e['message']}" for e in report.errors))
            made += report.created
        return made

    def _posts(self, users) -> int:
        from apps.posts.models import Category, Post
        from apps.posts.services import PostInput, create_post, review

        if Post.objects.filter(author__in=users.values()).exists():
            return 0

        def cat(key):
            return Category.objects.get(key=key)

        kolakaluru = area_ref("VILLAGE", "Kolakaluru")
        tenali = area_ref("MANDAL", "Tenali")
        guntur = area_ref("DISTRICT", "Guntur")
        member, village_admin = users[Role.PANCHAYAT_MEMBER], users[Role.VILLAGE_ADMIN]
        mandal_admin, district_admin = users[Role.MANDAL_ADMIN], users[Role.DISTRICT_ADMIN]

        def post(user, key, title, body, target, *, submit=True, language="te"):
            return create_post(
                user,
                PostInput(category=cat(key), language=language, title=title, body=body, target=target),
                submit=submit,
            )

        count = 0
        # PUBLISHED: admin posts publish at once (ADMIN_POSTS_AUTO_PUBLISH).
        post(village_admin, "announce", "తాగునీటి సరఫరా సమయం మార్పు",
             "రేపటి నుంచి ఉదయం 6 నుంచి 8 గంటల వరకు మాత్రమే తాగునీరు సరఫరా అవుతుంది.", kolakaluru)
        post(mandal_admin, "updates", "తెనాలి – కొలకలూరు బస్సు సమయాలు",
             "ప్రతి గంటకు ఒక బస్సు. మొదటి బస్సు ఉదయం 5:30కి.", tenali)
        post(district_admin, "schemes", "రేషన్ కార్డు శిబిరం – అక్టోబర్ 18",
             "కొత్త రేషన్ కార్డుల కోసం మండల కార్యాలయంలో దరఖాస్తు చేయండి.", guntur)
        post(district_admin, "news", "Free health camp on Sunday",
             "A free health camp will be held at the Primary Health Centre from 9 am.", guntur, language="en")
        count += 4

        # PENDING: a Panchayat member's post waits for review.
        post(member, "news", "గ్రామ సభ సమావేశం – అక్టోబర్ 20",
             "అక్టోబర్ 20న ఉదయం 10 గంటలకు పంచాయతీ కార్యాలయంలో గ్రామ సభ.", kolakaluru)
        count += 1

        # RECHECK: sent back with a comment.
        recheck = post(member, "announce", "రోడ్డు మరమ్మతు నోటీసు",
                       "ప్రధాన రహదారి మరమ్మతు పనులు జరుగుతున్నాయి.", kolakaluru)
        review(village_admin, recheck, "recheck", "తేదీలు మరియు ప్రత్యామ్నాయ మార్గం జోడించండి.")
        count += 1

        # DISCARDED: rejected with a reason.
        discard = post(member, "updates", "పాత సమాచారం", "ఈ సమాచారం ఇప్పటికే ప్రచురించబడింది.", kolakaluru)
        review(village_admin, discard, "discard", "ఇదే సమాచారం ఇప్పటికే ఉంది.")
        count += 1

        # UNPUBLISHED: published, then taken down by a higher admin.
        taken = post(village_admin, "announce", "విద్యుత్ కోత – తప్పు తేదీ",
                     "శనివారం విద్యుత్ కోత ఉంటుంది.", kolakaluru)
        review(mandal_admin, taken, "unpublish", "తేదీ తప్పు. సరైన తేదీతో మళ్ళీ పోస్ట్ చేయండి.")
        count += 1

        # DRAFT: saved, not submitted.
        post(member, "schemes", "పింఛన్ దరఖాస్తులు", "వివరాలు త్వరలో.", kolakaluru, submit=False)
        count += 1
        return count

    def _event_and_urgent_posts(self, users) -> int:
        """Posts with an event date (owner decision Q7) and an urgent one (Q19)."""
        from apps.posts.models import Category, Post
        from apps.posts.services import PostInput, create_post

        today = timezone.localdate()
        kolakaluru = area_ref("VILLAGE", "Kolakaluru")
        tenali = area_ref("MANDAL", "Tenali")
        guntur = area_ref("DISTRICT", "Guntur")
        village_admin, mandal_admin = users[Role.VILLAGE_ADMIN], users[Role.MANDAL_ADMIN]
        district_admin = users[Role.DISTRICT_ADMIN]

        def event(kind, start_in_days, end_in_days=None):
            return {
                "kind": kind,
                "start_date": today + timedelta(days=start_in_days),
                "end_date": today + timedelta(days=end_in_days) if end_in_days is not None else None,
            }

        wanted = [
            (village_admin, "announce", "గ్రామ సభ – పంచాయతీ కార్యాలయం",
             "ఉదయం 10 గంటలకు గ్రామ సభ. అందరూ హాజరు కావాలి.", kolakaluru, event("meeting", 3), False),
            (district_admin, "schemes", "ఆధార్ నమోదు శిబిరం",
             "కొత్త ఆధార్ నమోదు మరియు మార్పుల కోసం రెండు రోజుల శిబిరం.", guntur, event("camp", 5, 6), False),
            (mandal_admin, "schemes", "రేషన్ పంపిణీ – ఈ నెల చివరి తేదీ",
             "ఈ నెల రేషన్ తీసుకోవడానికి చివరి తేదీ.", tenali, event("deadline", 10), False),
            (village_admin, "announce", "పాఠశాలకు సెలవు",
             "భారీ వర్షాల కారణంగా పాఠశాలకు సెలవు.", kolakaluru, event("closure", 1), False),
            (mandal_admin, "announce", "వరద హెచ్చరిక – కాలువ గట్ల దగ్గరకు వెళ్లవద్దు",
             "కృష్ణా కాలువలో నీటి మట్టం పెరుగుతోంది. పిల్లలను, పశువులను గట్ల దగ్గరకు పంపవద్దు.", tenali, None, True),
        ]
        made = 0
        for user, key, title, body, target, when, urgent in wanted:
            if Post.objects.filter(author=user, title=title).exists():
                continue
            create_post(
                user,
                PostInput(
                    category=Category.objects.get(key=key), language="te", title=title, body=body,
                    target=target, event=when, is_urgent=urgent,
                ),
            )
            made += 1
        return made

    def _media_check(self) -> None:
        """Say whether uploads can be stored and whether video posts get server-side help (Q8)."""
        media_root = Path(settings.MEDIA_ROOT)
        try:
            media_root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=media_root, prefix=".write-check-"):
                pass
            self.stdout.write(f"Media: {media_root} is writable.")
        except OSError as error:
            self.stdout.write(self.style.ERROR(f"Media: {media_root} is not writable ({error}). Photo and video uploads will fail."))
        if not settings.VIDEO_POSTS_ENABLED:
            self.stdout.write("Video posts: switched off (VIDEO_POSTS_ENABLED=False).")
            return
        missing = [tool for tool in ("ffprobe", "ffmpeg") if not shutil.which(tool)]
        if missing:
            self.stdout.write(self.style.WARNING(
                f"Video posts: {' and '.join(missing)} not found. Phones must send duration_s and a poster photo."
            ))
        else:
            self.stdout.write(
                f"Video posts: on, up to {settings.VIDEO_MAX_SECONDS} s and {settings.MAX_VIDEO_UPLOAD_MB} MB; ffprobe and ffmpeg found."
            )
