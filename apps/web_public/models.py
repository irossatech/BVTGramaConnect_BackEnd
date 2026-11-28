from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class ContactMessage(models.Model):
    """A message from the public website's Contact page.

    People use it to ask for their village to be added, to ask for a login,
    or to report a problem. Super Admins read and close them in the console
    (owner decision Q16); Django admin shows them too.
    """

    name = models.CharField(_("name"), max_length=120)
    mobile = models.CharField(_("mobile number"), max_length=10)
    village_text = models.CharField(_("village / mandal"), max_length=200, blank=True)
    message = models.TextField(_("message"), max_length=2000)
    language = models.CharField(_("language"), max_length=2, default="te")
    # No sender IP address: its only use, the per-hour limit, went on 2026-11-20, and messages are kept
    # forever, so it is not stored at all (BVTGC-103).
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    handled = models.BooleanField(_("handled"), default=False)
    handled_note = models.TextField(_("note"), blank=True)
    handled_at = models.DateTimeField(_("handled at"), null=True, blank=True)
    handled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        verbose_name = _("contact message")
        verbose_name_plural = _("contact messages")
        ordering = ["-created_at"]
        permissions = [("manage_contact_messages", "Read and close contact messages")]

    def __str__(self):
        # Django admin keeps this text in its change history: never the full mobile number.
        from apps.contacts.phones import mask

        return f"{self.name} ({mask(self.mobile)}) {self.created_at:%Y-%m-%d}"
