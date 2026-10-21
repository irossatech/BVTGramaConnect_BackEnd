from django.db import models
from django.utils.translation import gettext_lazy as _


class ContactMessage(models.Model):
    """A message from the public website's Contact page.

    People use it to ask for their village to be added, to ask for a login,
    or to report a problem. Staff read and close them in Django admin.
    """

    name = models.CharField(_("name"), max_length=120)
    mobile = models.CharField(_("mobile number"), max_length=10)
    village_text = models.CharField(_("village / mandal"), max_length=200, blank=True)
    message = models.TextField(_("message"), max_length=2000)
    language = models.CharField(_("language"), max_length=2, default="te")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    handled = models.BooleanField(_("handled"), default=False)
    handled_note = models.TextField(_("note"), blank=True)

    class Meta:
        verbose_name = _("contact message")
        verbose_name_plural = _("contact messages")
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["ip_address", "created_at"])]

    def __str__(self):
        return f"{self.name} ({self.mobile}) {self.created_at:%Y-%m-%d}"
