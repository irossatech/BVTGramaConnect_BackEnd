"""Sample data for development, demos and UAT.

2 districts × 2 mandals × 3 villages, one official per role, and posts in every
state: DRAFT, PENDING, PUBLISHED, RECHECK, DISCARDED, UNPUBLISHED. Posts are
made through posts.services, so they carry real review history and audit rows.

Safe to run again: geography goes through the idempotent importer, officials
are matched by username, and posts are only added once. Each official starts
with their role's default password (printed at the end), as an invite would.
"""

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.credentials import give_default_password, role_default
from apps.accounts.models import RoleAssignment, User
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


def area_ref(level: str, name: str) -> AreaRef:
    model = {"STATE": State, "DISTRICT": District, "MANDAL": Mandal, "VILLAGE": Village}[level]
    return AreaRef(level, model.objects.get(name_en=name).id)


class Command(BaseCommand):
    help = "Load sample geography, one official per role and posts in every state. Safe to run again."

    def handle(self, *args, **options):
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
                RoleAssignment.objects.get_or_create(user=user, role=role, area_level=ref.level, area_id=ref.id)
                if created:
                    give_default_password(user, role)
                users[role] = user

        made = self._posts(users)
        self.stdout.write(self.style.SUCCESS(f"Posts: {made} created." if made else "Posts: already present."))
        self.stdout.write(
            "Officials. New ones start with their role's default password"
            + (" and must change it at first login:" if settings.FORCE_PASSWORD_CHANGE_ON_FIRST_LOGIN else ":")
        )
        for name, _te, username, _mobile, role, (level, area_name) in OFFICIALS:
            self.stdout.write(f"  {username:<20} {role_default(role):<14} {role:<17} {area_name}  ({name})")

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
