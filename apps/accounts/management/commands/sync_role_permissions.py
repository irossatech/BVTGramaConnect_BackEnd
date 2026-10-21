"""Re-apply the role matrix (apps/accounts/role_matrix.py) to the database.

    python manage.py sync_role_permissions          # apply and list the changes
    python manage.py sync_role_permissions --check  # list what would change; exit 1 if anything

`migrate` runs the same sync at the end, so a deploy applies the dict
automatically. Run this after changing SUPER_ADMIN_CAN_POST, or to undo
permission edits made by hand in Django admin. Changes are audit-logged.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.sync import UnknownPermission, sync_role_permissions
from apps.audit.services import record


class _DryRun(Exception):
    pass


class Command(BaseCommand):
    help = "Make the groups and their permissions match apps/accounts/role_matrix.py. Safe to run again."

    def add_arguments(self, parser):
        parser.add_argument("--check", action="store_true", help="Change nothing; exit 1 when the database differs.")

    def handle(self, *args, check=False, **options):
        try:
            if check:
                changes = []
                try:
                    with transaction.atomic():
                        changes = sync_role_permissions()
                        raise _DryRun
                except _DryRun:
                    pass
            else:
                with transaction.atomic():
                    changes = sync_role_permissions()
                    if changes:
                        record(
                            "setting.role_permissions_synced",
                            target_label="Role permissions",
                            via="system",
                            data={"changes": changes[:200]},
                        )
        except UnknownPermission as exc:
            raise CommandError(str(exc)) from None

        if not changes:
            self.stdout.write(self.style.SUCCESS("Groups and permissions already match the role matrix."))
            return
        verb = "Would change" if check else "Changed"
        self.stdout.write(f"{verb} {len(changes)} thing(s):")
        for line in changes:
            self.stdout.write(f"  {line}")
        if check:
            raise CommandError("The database differs from the role matrix. Run sync_role_permissions.")
        self.stdout.write(self.style.SUCCESS("Groups and permissions now match the role matrix."))
