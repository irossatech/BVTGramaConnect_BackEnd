"""Create the first Super Admin (or give the role to an existing account) from the server shell.

    python manage.py create_super_admin --username sita.admin --name "Sita Mahalakshmi"

Prints a temporary password once; the person must change it at first login
to the console. The role is the platform-wide group of role_matrix.py,
assigned at the state (--state-id, or the only state). Nobody can give this
role through the API, and Django admin lets only Super Admins edit roles, so
this command is how the first one is made. Audit-logged.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.credentials import generate_password
from apps.accounts.models import RoleAssignment, RoleGroup, User, normalize_login, username_validator
from apps.audit.services import record
from apps.geography.areas import AreaRef
from apps.geography.models import State


class Command(BaseCommand):
    help = "Create a Super Admin with a temporary password shown once, or give the role to an existing user."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--name", default="", help="Needed when the user does not exist yet.")
        parser.add_argument("--state-id", type=int, default=None, help="Defaults to the only state.")

    def handle(self, *args, username, name, state_id, **options):
        username = normalize_login(username)
        try:
            username_validator(username)
        except Exception as exc:  # noqa: BLE001  (Django ValidationError)
            raise CommandError(f"Bad username: {exc}") from None
        role = RoleGroup.objects.filter(platform_wide=True).order_by("rank").select_related("group").first()
        if role is None:
            raise CommandError("No platform-wide role. Run migrate (or sync_role_permissions) first.")
        states = State.objects.filter(pk=state_id) if state_id else State.objects.all()
        if states.count() != 1:
            raise CommandError("Give --state-id: load the master data first, or pick one of several states.")
        area = AreaRef("STATE", states.get().pk)

        with transaction.atomic():
            user = User.objects.filter(username=username).first()
            password = None
            if user is None:
                if not name.strip():
                    raise CommandError("--name is required for a new user.")
                password = generate_password()
                user = User.objects.create_user(username=username, name=name.strip(), password=password)
                User.objects.filter(pk=user.pk).update(must_change_password=True)
            assignment, created = RoleAssignment.objects.get_or_create(
                user=user, group=role.group, area_level=area.level, area_id=area.id, defaults={"is_active": True}
            )
            if not created and not assignment.is_active:
                assignment.is_active = True
                assignment.save(update_fields=["is_active"])
            record(
                "user.assignment_added" if created else "user.updated",
                target=user,
                target_label=user.name,
                area=area,
                via="system",
                data={"role": role.key, "by": "create_super_admin"},
            )

        self.stdout.write(self.style.SUCCESS(f"{user.username} is a {role.group.name} for the whole platform."))
        if password:
            self.stdout.write(f"Temporary password (shown once; changed at first login): {password}")
        else:
            self.stdout.write("Existing account: the password was not changed.")
