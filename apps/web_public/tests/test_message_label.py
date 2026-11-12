"""A contact message's label never shows the full mobile number (BE_02, BE-SEC-106).

Django admin stores this label in its change history (LogEntry.object_repr), which is never cleaned.
"""

import pytest

from apps.web_public.models import ContactMessage

pytestmark = pytest.mark.django_db


def test_label_masks_the_mobile_number():
    message = ContactMessage.objects.create(name="రవి", mobile="9876543210", message="మా గ్రామాన్ని చేర్చండి")
    label = str(message)
    assert "9876543210" not in label
    assert label.startswith("రవి (******3210) ")
    assert message.created_at.strftime("%Y-%m-%d") in label
