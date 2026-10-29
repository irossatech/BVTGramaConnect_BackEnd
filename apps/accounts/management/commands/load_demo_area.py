"""Demo content for one real mandal and a few of its villages, for testing on the qa server.

    python manage.py load_demo_area                                   # Kandukuru mandal, Prakasam, 4 villages
    python manage.py load_demo_area --mandal Kandukuru --villages 6
    python manage.py load_demo_area --village Kandukur --village Palur

Unlike seed_sample_data, it adds no geography: it uses the mandal and villages already loaded
(the state's real list) and picks villages that have coordinates, so "Use my location" near the
mandal finds them. A village with the mandal's name (the mandal town) comes first.

For the mandal and each village it adds:
- officials: a Mandal Admin, and per village a Village Admin and a Panchayat member, all with
  usernames starting "demo." and a generated temporary password, printed once and changed at
  first login;
- posts through posts.services: published village news, a Gram Sabha with an event date, a
  Panchayat member's post waiting for review, and for the mandal a pension deadline, a health
  camp in English and an urgent rain warning;
- important numbers through the contacts import: VRO and Sachivalayam per village, Tahsildar and
  MPDO offices for the mandal. Their phone numbers are 0 + the area's STD code + "000…":
  valid by the phone rules, but no Indian exchange gives out numbers starting with 000, so a
  tester can never ring a real person.

Safe to run again: officials are matched by username, posts by author and title, numbers by
type, phone and area.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from apps.accounts.credentials import generate_password
from apps.accounts.models import RoleAssignment, RoleGroup, User
from apps.accounts.roles import Role
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel, District, Mandal, Village, name_key


def telugu(area) -> str:
    return area.name_te or area.name_en


class Command(BaseCommand):
    help = "Load demo officials, posts and important numbers for one mandal and a few of its villages."

    def add_arguments(self, parser):
        parser.add_argument("--district", default="Prakasam")
        parser.add_argument("--mandal", default="Kandukuru")
        parser.add_argument("--villages", type=int, default=4, help="How many villages (default 4).")
        parser.add_argument("--village", action="append", default=[], help="A village by name; repeat for more.")
        parser.add_argument("--std-code", default="08598", help="Landline prefix for the demo numbers (Kandukur 08598).")

    def handle(self, *args, district, mandal, villages, village, std_code, **options):
        out = getattr(self.stdout, "_out", None)
        if hasattr(out, "reconfigure"):
            out.reconfigure(errors="backslashreplace")

        mandal_obj = self._mandal(district, mandal)
        chosen = self._villages(mandal_obj, villages, village)
        self.stdout.write(f"Mandal {mandal_obj.name_en} ({telugu(mandal_obj)}), {mandal_obj.district.name_en}.")
        self.stdout.write("Villages: " + ", ".join(f"{v.name_en} ({telugu(v)})" for v in chosen))

        passwords: dict[str, str] = {}
        with transaction.atomic():
            mandal_admin = self._official(
                f"demo.{slugify(mandal_obj.name_en)}.mandal", f"Mandal Admin {mandal_obj.name_en}",
                Role.MANDAL_ADMIN, AreaRef(AreaLevel.MANDAL, mandal_obj.id), passwords,
            )
            people = []
            seen: set[str] = set()
            for v in chosen:
                slug = slugify(v.name_en)
                if slug in seen:  # twin villages: same name in one mandal
                    slug = f"{slug}-{v.id}"
                seen.add(slug)
                ref = AreaRef(AreaLevel.VILLAGE, v.id)
                people.append((
                    v,
                    self._official(f"demo.{slug}.admin", f"Village Admin {v.name_en}", Role.VILLAGE_ADMIN, ref, passwords),
                    self._official(f"demo.{slug}.member", f"Panchayat Member {v.name_en}", Role.PANCHAYAT_MEMBER, ref, passwords),
                ))

        posts = self._mandal_posts(mandal_admin, mandal_obj)
        for v, admin, member in people:
            posts += self._village_posts(admin, member, v)
        self.stdout.write(self.style.SUCCESS(f"Posts: {posts} created.") if posts else "Posts: already present.")

        numbers = self._numbers(mandal_obj, chosen, std_code)
        self.stdout.write(
            self.style.SUCCESS(f"Important numbers: {numbers} created.") if numbers else "Important numbers: already present."
        )

        if passwords:
            User.objects.filter(username__in=list(passwords)).update(must_change_password=True)
        self.stdout.write("Officials. Temporary passwords are shown once; each must be changed at first login:")
        for username in [mandal_admin.username] + [u.username for _v, a, m in people for u in (a, m)]:
            self.stdout.write(f"  {username:<40} {passwords.get(username, '(unchanged)')}")

    # --- Areas --------------------------------------------------------------------------------

    def _mandal(self, district: str, mandal: str) -> Mandal:
        found = District.objects.active().filter(name_key=name_key(district)).first()
        if found is None:
            raise CommandError(f"No active district named {district!r}.")
        result = Mandal.objects.active().select_related("district").filter(district=found, name_key=name_key(mandal)).first()
        if result is None:
            names = ", ".join(Mandal.objects.active().filter(district=found).order_by("name_en").values_list("name_en", flat=True))
            raise CommandError(f"No active mandal named {mandal!r} in {found.name_en}. Its mandals: {names}")
        return result

    def _villages(self, mandal: Mandal, count: int, names: list[str]) -> list[Village]:
        rows = Village.objects.active().filter(mandal=mandal)
        if names:
            chosen = []
            for name in names:
                found = rows.filter(name_key=name_key(name)).first()
                if found is None:
                    raise CommandError(f"No active village named {name!r} in {mandal.name_en}.")
                chosen.append(found)
            return chosen
        located = list(rows.filter(latitude__isnull=False, longitude__isnull=False).order_by("name_en", "id"))
        # The mandal town first ("Kandukur" for Kandukuru mandal), then the others by name.
        town = mandal.name_key.rstrip("u")
        located.sort(key=lambda v: (v.name_key.rstrip("u") != town, v.name_en))
        if not located:
            raise CommandError(f"No village in {mandal.name_en} has coordinates; name them with --village.")
        return located[:max(count, 1)]

    # --- Officials ----------------------------------------------------------------------------

    def _official(self, username: str, name: str, role: str, area: AreaRef, passwords: dict) -> User:
        user, created = User.objects.get_or_create(username=username, defaults={"name": name})
        group = RoleGroup.objects.get(key=role).group
        RoleAssignment.objects.get_or_create(user=user, group=group, area_level=area.level, area_id=area.id)
        if created:
            passwords[username] = generate_password()
            user.set_password(passwords[username])
            user.must_change_password = False  # switched on once the demo posts are written
            user.save(update_fields=["password", "must_change_password", "updated_at"])
        return user

    # --- Posts ---------------------------------------------------------------------------------

    def _post(self, user, key, title, body, target: AreaRef, *, language="te", event=None, urgent=False) -> int:
        from apps.posts.models import Category, Post
        from apps.posts.services import PostInput, create_post

        if Post.objects.filter(author=user, title=title).exists():
            return 0
        create_post(user, PostInput(
            category=Category.objects.get(key=key), language=language, title=title, body=body,
            target=target, event=event, is_urgent=urgent,
        ))
        return 1

    @staticmethod
    def _event(kind, start_in_days, end_in_days=None) -> dict:
        today = timezone.localdate()
        return {
            "kind": kind,
            "start_date": today + timedelta(days=start_in_days),
            "end_date": today + timedelta(days=end_in_days) if end_in_days is not None else None,
        }

    def _mandal_posts(self, admin, mandal: Mandal) -> int:
        area = AreaRef(AreaLevel.MANDAL, mandal.id)
        m = telugu(mandal)
        return (
            self._post(admin, "schemes", f"{m} మండలం – పింఛన్ పంపిణీ చివరి తేదీ",
                       "ఈ నెల పింఛన్ సచివాలయంలో తీసుకోవడానికి చివరి తేదీ. ఆధార్ కార్డు తీసుకురండి.",
                       area, event=self._event("deadline", 7))
            + self._post(admin, "news", f"Free eye camp – {mandal.name_en} PHC",
                         "A free eye check-up camp will be held at the Primary Health Centre from 9 am to 4 pm. "
                         "Spectacles at no cost for those who need them.",
                         area, language="en", event=self._event("camp", 5, 6))
            + self._post(admin, "announce", f"భారీ వర్ష హెచ్చరిక – {m} మండలం",
                         "రాబోయే రెండు రోజులు భారీ వర్షాలు. వాగులు, చెరువుల దగ్గరకు వెళ్లవద్దు. పశువులను సురక్షిత ప్రదేశంలో ఉంచండి.",
                         area, urgent=True)
        )

    def _village_posts(self, admin, member, village: Village) -> int:
        area = AreaRef(AreaLevel.VILLAGE, village.id)
        v = telugu(village)
        return (
            self._post(admin, "announce", f"{v} – తాగునీటి సరఫరా సమయం మార్పు",
                       "రేపటి నుంచి ఉదయం 6 నుంచి 8 గంటల వరకు, సాయంత్రం 5 నుంచి 6 గంటల వరకు తాగునీరు సరఫరా అవుతుంది.", area)
            + self._post(admin, "news", f"{v} గ్రామ సభ",
                         "పంచాయతీ కార్యాలయంలో ఉదయం 10 గంటలకు గ్రామ సభ. రోడ్లు, తాగునీరు, పింఛన్లపై చర్చ. అందరూ హాజరు కావాలి.",
                         area, event=self._event("meeting", 3))
            + self._post(admin, "updates", f"{v} – రేషన్ పంపిణీ",
                         "ఈ నెల రేషన్ సరుకులు చౌక ధరల దుకాణంలో అందుబాటులో ఉన్నాయి. ఉదయం 8 నుంచి మధ్యాహ్నం 12 వరకు.", area)
            # A Panchayat member's post goes to review: it waits in the Village Admin's queue.
            + self._post(member, "news", f"{v} – వీధి దీపాలు పనిచేయడం లేదు",
                         "ప్రధాన వీధిలో వారం రోజులుగా వీధి దీపాలు వెలగడం లేదు. మరమ్మతు చేయించాలి.", area)
        )

    # --- Important numbers ---------------------------------------------------------------------

    def _numbers(self, mandal: Mandal, villages: list[Village], std_code: str) -> int:
        from apps.contacts.services import import_contacts

        def phone(n: int) -> str:
            return f"{std_code}{n:0{11 - len(std_code)}d}"  # 08598 + 000101: no exchange starts with 000

        def where(village: Village | None) -> str:
            if village is None:
                return f"{mandal.district.name_en},{mandal.name_en},,"
            if village.lgd_code:
                return f"{mandal.district.name_en},{mandal.name_en},,{village.lgd_code}"
            return f"{mandal.district.name_en},{mandal.name_en},{village.name_en},"

        def quoted(value: str) -> str:
            return '"' + value.replace('"', '""') + '"'

        m, mte = mandal.name_en, telugu(mandal)
        rows = [
            ("revenue", f"Tahsildar Office, {m}", f"తహసీల్దార్ కార్యాలయం, {mte}", phone(101), None),
            ("official", f"MPDO Office, {m}", f"ఎంపీడీఓ కార్యాలయం, {mte}", phone(102), None),
        ]
        for index, village in enumerate(villages):
            ven, vte = village.name_en, telugu(village)
            rows += [
                ("vro", f"VRO, {ven}", f"వీఆర్వో, {vte}", phone(201 + index * 10), village),
                ("sachivalayam", f"{ven} Grama Sachivalayam", f"{vte} గ్రామ సచివాలయం", phone(202 + index * 10), village),
            ]
        text = "type,name,name_te,phone,district,mandal,village,village_lgd\n" + "".join(
            f"{kind},{quoted(name)},{quoted(name_te)},{number},{where(village)}\n"
            for kind, name, name_te, number, village in rows
        )
        report = import_contacts(None, text, via="system")
        if not report.ok:
            raise CommandError("\n".join(f"row {e['row']} {e['field']}: {e['message']}" for e in report.errors))
        return report.created
