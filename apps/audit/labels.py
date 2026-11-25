"""What each audit action means, in words, for the audit screens (BVTGC-100).

Every audit entry the API sends carries `action_label`: a short phrase that reads after the person's name
("Ravi Kumar published a post", "రవి కుమార్ పోస్ట్‌ను ప్రచురించారు"), in the language of the request
(Telugu by default). The console and the app show it instead of keeping their own lists.

The English is the console's approved wording (BVTGConnect src/console/logic/audit.ts); the Telugu is in
locale/te/LC_MESSAGES/django.po. Keys no longer recorded stay here, because the audit log is never
cleaned and old entries still carry them. A new action needs its label here:
apps/audit/tests/test_action_labels.py fails until it has one.
"""

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy

ACTION_LABELS = {
    # Posts
    "post.created": gettext_lazy("wrote a post"),
    "post.updated": gettext_lazy("edited a post"),
    "post.deleted": gettext_lazy("deleted a draft post"),
    "post.submitted": gettext_lazy("sent a post for review"),
    "post.resubmitted": gettext_lazy("sent a post again after fixing it"),
    "post.published": gettext_lazy("published a post"),
    "post.auto_published": gettext_lazy("published a post"),
    "post.recheck": gettext_lazy("sent a post for recheck"),
    "post.rechecked": gettext_lazy("sent a post for recheck"),
    "post.discarded": gettext_lazy("discarded a post"),
    "post.unpublished": gettext_lazy("took down a post"),
    "post.blocked": gettext_lazy("closed a citizen's post by blocking the phone"),
    "post.reversed": gettext_lazy("changed a review decision"),
    "post.overridden": gettext_lazy("changed a review decision"),
    "post.escalated": gettext_lazy("escalated a post (waited past the time limit)"),
    "post.urgent_set": gettext_lazy("marked a post urgent"),
    "post.urgent_cleared": gettext_lazy("removed the urgent mark from a post"),
    # Users and roles
    "user.created": gettext_lazy("added a user"),
    "user.updated": gettext_lazy("edited a user's details"),
    "user.username_changed": gettext_lazy("changed a user's username"),
    "user.blocked": gettext_lazy("blocked a user"),
    "user.unblocked": gettext_lazy("unblocked a user"),
    "user.reassigned": gettext_lazy("moved a user to another area"),
    "user.assignment_added": gettext_lazy("gave a user a role"),
    "user.assignment_removed": gettext_lazy("removed a role from a user"),
    "user.assignment_changed": gettext_lazy("changed the area of a user's role"),
    "user.password_reset": gettext_lazy("reset a user's password"),
    "user.password_set": gettext_lazy("set a temporary password"),
    "user.password_changed": gettext_lazy("changed their password"),
    "user.unlocked": gettext_lazy("unlocked a user"),
    "user.super_admin_created": gettext_lazy("added a Super Admin"),
    "user.super_admin_role_added": gettext_lazy("gave the Super Admin role"),
    "user.super_admin_role_removed": gettext_lazy("removed the Super Admin role"),
    "group.permissions_changed": gettext_lazy("changed a role's permissions"),
    "setting.group_permissions_changed": gettext_lazy("changed a role's permissions"),
    "setting.role_permissions_synced": gettext_lazy("brought the roles' permissions up to date"),
    # Sign-in
    "auth.login": gettext_lazy("signed in"),
    "auth.logout": gettext_lazy("signed out"),
    "auth.login_failed": gettext_lazy("failed to sign in (wrong password)"),
    "auth.login_refused": gettext_lazy("was refused sign-in"),
    "auth.locked_out": gettext_lazy("was locked out after failed sign-ins"),
    "auth.ip_locked_out": gettext_lazy("was locked out (too many failed sign-ins from one network)"),
    "auth.password_change_failed": gettext_lazy("entered a wrong current password"),
    "auth.password_change_locked": gettext_lazy("was locked out of changing the password"),
    "auth.password_too_short": gettext_lazy("chose a password that was too short"),
    # Areas
    "area.created": gettext_lazy("added an area"),
    "area.updated": gettext_lazy("edited an area"),
    "area.deactivated": gettext_lazy("switched off an area"),
    "area.reactivated": gettext_lazy("switched an area back on"),
    "area.imported": gettext_lazy("imported villages from a CSV file"),
    # Settings, categories, texts
    "setting.changed": gettext_lazy("changed a setting"),
    "setting.role_password_changed": gettext_lazy("changed a default password"),
    "category.created": gettext_lazy("added a post category"),
    "category.updated": gettext_lazy("changed a post category"),
    "template.updated": gettext_lazy("edited a notification text"),
    # Services, bills, service requests
    "service.created": gettext_lazy("added a service"),
    "service.updated": gettext_lazy("edited a service"),
    "service.shown": gettext_lazy("showed a service on Home"),
    "service.hidden": gettext_lazy("hid a service from Home"),
    "service.reordered": gettext_lazy("changed the order of services"),
    "bill.created": gettext_lazy("added a bill / recharge"),
    "bill.updated": gettext_lazy("edited a bill / recharge"),
    "bill.shown": gettext_lazy("showed a bill on Home"),
    "bill.hidden": gettext_lazy("hid a bill from Home"),
    "bill.reordered": gettext_lazy("changed the order of bills"),
    "service_request.created": gettext_lazy("sent a service request"),
    "service_request.assigned": gettext_lazy("assigned a service request"),
    "service_request.status_changed": gettext_lazy("changed a service request's status"),
    "service_request.cancelled": gettext_lazy("cancelled their service request"),
    # Banners, important numbers, contact messages
    "ad.created": gettext_lazy("added a sponsored banner"),
    "ad.updated": gettext_lazy("edited a sponsored banner"),
    "ad.deleted": gettext_lazy("deleted a sponsored banner"),
    "contact.created": gettext_lazy("added an important number"),
    "contact.updated": gettext_lazy("edited an important number"),
    "contact.removed": gettext_lazy("removed an important number"),
    "contacts.imported": gettext_lazy("imported important numbers"),
    "contact_type.created": gettext_lazy("added a number type"),
    "contact_type.updated": gettext_lazy("changed a number type"),
    "contact_type.deleted": gettext_lazy("deleted a number type"),
    "contact.handled": gettext_lazy("marked a contact message handled"),
    "contact.reopened": gettext_lazy("marked a contact message as new"),
    # Citizens
    "citizen.registered": gettext_lazy("registered a citizen phone"),
    "citizen.blocked": gettext_lazy("blocked a citizen's phone"),
    "citizen.unblocked": gettext_lazy("unblocked a citizen's phone"),
    "citizen.block_ended": gettext_lazy("ended a citizen's block (its time was up)"),
    "citizen.posts_discarded": gettext_lazy("discarded a citizen's pending posts"),
    # Reports and the system
    "report.exported": gettext_lazy("exported a report"),
    "email.failed": gettext_lazy("email not delivered"),
    "email.skipped": gettext_lazy("email skipped"),
    "push.failed": gettext_lazy("notification not delivered"),
}


def action_label(action: str) -> str:
    """The words for `action` in the active language; never the raw code."""
    label = ACTION_LABELS.get(action)
    return str(label) if label is not None else _("other activity")
