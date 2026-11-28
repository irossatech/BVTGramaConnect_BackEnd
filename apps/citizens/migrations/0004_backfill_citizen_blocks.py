"""Copy the blocks phones carry today into CitizenBlock (owner's request of 2026-11-21).

One row per phone (its installs share the hashed Android ID, as services.same_phone): the block on the
install with the latest end. A block whose time is already up is written as ended then, with no one as
`ended_by`, so the citizens.tasks job does not announce it again. Blocks lifted early before this change
left nothing on the phone, so they cannot be copied; the audit log still has them.

Reverse step: delete the copied rows (the phones' own fields are not touched either way).
"""

from django.db import migrations
from django.utils import timezone

BATCH = 500


def copy_blocks(apps, schema_editor):
    CitizenDevice = apps.get_model("citizens", "CitizenDevice")
    CitizenBlock = apps.get_model("citizens", "CitizenBlock")
    now = timezone.now()
    chosen = {}
    for device in CitizenDevice.objects.filter(blocked_at__isnull=False).order_by("pk").iterator():
        phone = device.android_id_hash or str(device.pk)
        best = chosen.get(phone)
        # No end date is the longest block; then the latest end.
        key = (device.blocked_until is None, device.blocked_until or now)
        if best is None or key > (best.blocked_until is None, best.blocked_until or now):
            chosen[phone] = device
    rows = []
    for phone, device in chosen.items():
        ended = device.blocked_until is not None and device.blocked_until <= now
        rows.append(
            CitizenBlock(
                phone=phone, device_id=device.pk, village_id=device.village_id, blocked_at=device.blocked_at,
                blocked_until=device.blocked_until, blocked_by_id=device.blocked_by_id, reason=device.block_reason,
                ended_at=device.blocked_until if ended else None,
            )
        )
    CitizenBlock.objects.bulk_create(rows, batch_size=BATCH)


def remove_copies(apps, schema_editor):
    apps.get_model("citizens", "CitizenBlock").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("citizens", "0003_citizen_block"),
    ]

    operations = [
        migrations.RunPython(copy_blocks, remove_copies),
    ]
