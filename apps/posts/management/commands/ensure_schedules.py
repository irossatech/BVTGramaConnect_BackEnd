from django.core.management.base import BaseCommand

from apps.citizens.tasks import BLOCK_ENDINGS_SCHEDULE, ensure_block_endings_schedule
from apps.posts.tasks import ESCALATION_SCHEDULE, ensure_escalation_schedule
from apps.wallet.tasks import CHECK_WALLETS_SCHEDULE, DECIDE_REFERRALS_SCHEDULE, ensure_wallet_schedules
from grama.housekeeping import HOUSEKEEPING_SCHEDULE, ensure_housekeeping_schedule


class Command(BaseCommand):
    help = (
        "Create or update the django-q2 schedules (review escalation, citizen block endings, daily housekeeping, "
        "the wallet's hourly referral decisions and daily check). "
        "Safe to run on every deploy."
    )

    def handle(self, *args, **options):
        ensure_escalation_schedule()
        ensure_block_endings_schedule()
        ensure_housekeeping_schedule()
        ensure_wallet_schedules()
        self.stdout.write(
            self.style.SUCCESS(
                f"Schedules ready: {ESCALATION_SCHEDULE}, {BLOCK_ENDINGS_SCHEDULE}, {HOUSEKEEPING_SCHEDULE}, "
                f"{DECIDE_REFERRALS_SCHEDULE}, {CHECK_WALLETS_SCHEDULE}"
            )
        )
