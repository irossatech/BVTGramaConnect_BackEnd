"""The wallet's scheduled jobs (BVTGC-126), registered by `manage.py ensure_schedules` on every deploy."""

from __future__ import annotations

DECIDE_REFERRALS_SCHEDULE = "decide-due-referrals"
CHECK_WALLETS_SCHEDULE = "check-wallet-caches"


def decide_referrals() -> dict:
    """Hourly: confirm or reject every PENDING referral past its hold."""
    from .services import decide_due_referrals

    return decide_due_referrals()


def check_wallets() -> int:
    """Daily: audit any wallet whose cache differs from the sum of its entries."""
    from .services import check_wallets as run

    return run()


def ensure_wallet_schedules() -> None:
    from django_q.models import Schedule

    from grama.schedules import ensure_schedule

    ensure_schedule(DECIDE_REFERRALS_SCHEDULE, func="apps.wallet.tasks.decide_referrals", schedule_type=Schedule.HOURLY, repeats=-1)
    ensure_schedule(CHECK_WALLETS_SCHEDULE, func="apps.wallet.tasks.check_wallets", schedule_type=Schedule.DAILY, repeats=-1)
