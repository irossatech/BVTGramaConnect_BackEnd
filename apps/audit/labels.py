"""Readable words for each audit action, in the request's language (CROSS-CONV-002, BVTGC-100).

The audit log stores a code such as "post.published"; the API also sends `action_label`, a phrase
that follows the actor's name ("Ravi published a post"). The English is the console's own wording,
so nothing changes for English readers; the Telugu is in locale/te/LC_MESSAGES/django.po.

A code missing here still gets words: "other action (<code>)". grama/tests and
apps/audit/tests/test_action_labels.py fail when the server writes a code that has no label.
"""

from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

ACTION_LABELS = {
    # Posts
    "post.created": _("wrote a post"),
    "post.updated": _("edited a post"),
    "post.deleted": _("deleted a draft post"),
    "post.submitted": _("sent a post for review"),
    "post.resubmitted": _("sent a post again after fixing it"),
    "post.published": _("published a post"),
    "post.auto_published": _("published a post"),
    "post.recheck": _("sent a post for recheck"),
    "post.rechecked": _("sent a post for recheck"),
    "post.discarded": _("discarded a post"),
    "post.unpublished": _("took down a post"),
    "post.blocked": _("closed a citizen’s post by blocking the phone"),
    "post.reversed": _("changed a review decision"),
    "post.overridden": _("changed a review decision"),
    "post.escalated": _("escalated a post (waited past the time limit)"),
    "post.urgent_set": _("marked a post urgent"),
    "post.urgent_cleared": _("removed the urgent mark from a post"),
    # Users and roles
    "user.created": _("added a user"),
    "user.updated": _("edited a user’s details"),
    "user.username_changed": _("changed a user’s username"),
    "user.blocked": _("blocked a user"),
    "user.unblocked": _("unblocked a user"),
    "user.reassigned": _("moved a user to another area"),
    "user.assignment_added": _("gave a user a role"),
    "user.assignment_removed": _("removed a role from a user"),
    "user.assignment_changed": _("changed the area of a user’s role"),
    "user.password_reset": _("reset a user’s password"),
    "user.password_set": _("set a temporary password"),
    "user.password_changed": _("changed their password"),
    "user.unlocked": _("unlocked a user"),
    "user.super_admin_created": _("added a Super Admin"),
    "user.super_admin_role_added": _("gave the Super Admin role"),
    "user.super_admin_role_removed": _("removed the Super Admin role"),
    "group.permissions_changed": _("changed a role’s permissions"),
    "setting.group_permissions_changed": _("changed a role’s permissions"),
    "setting.role_permissions_synced": _("updated the roles’ permissions from the role list"),
    # Sign-in
    "auth.login": _("signed in"),
    "auth.logout": _("signed out"),
    "auth.login_failed": _("failed to sign in (wrong password)"),
    "auth.login_refused": _("was refused sign-in"),
    "auth.locked_out": _("was locked out after failed sign-ins"),
    "auth.ip_locked_out": _("was locked out (too many failed sign-ins from one network)"),
    "auth.password_change_failed": _("entered a wrong current password"),
    "auth.password_change_locked": _("was locked out of changing the password"),
    "auth.password_too_short": _("chose a password that was too short"),
    # Areas and settings
    "area.created": _("added an area"),
    "area.updated": _("edited an area"),
    "area.deactivated": _("switched off an area"),
    "area.reactivated": _("switched an area back on"),
    "area.imported": _("imported villages from a CSV file"),
    "setting.changed": _("changed a setting"),
    "setting.role_password_changed": _("changed a default password"),
    "category.created": _("added a post category"),
    "category.updated": _("changed a post category"),
    "template.updated": _("edited a notification text"),
    # Services, bills and requests
    "service.created": _("added a service"),
    "service.updated": _("edited a service"),
    "service.shown": _("showed a service on Home"),
    "service.hidden": _("hid a service from Home"),
    "service.reordered": _("changed the order of services"),
    "bill.created": _("added a bill / recharge"),
    "bill.updated": _("edited a bill / recharge"),
    "bill.shown": _("showed a bill on Home"),
    "bill.hidden": _("hid a bill from Home"),
    "bill.reordered": _("changed the order of bills"),
    "service_request.created": _("sent a service request"),
    "service_request.assigned": _("assigned a service request"),
    "service_request.status_changed": _("changed a service request’s status"),
    "service_request.cancelled": _("cancelled their service request"),
    # Banners and important numbers
    "ad.created": _("added a sponsored banner"),
    "ad.updated": _("edited a sponsored banner"),
    "ad.deleted": _("deleted a sponsored banner"),
    "contact.created": _("added an important number"),
    "contact.updated": _("edited an important number"),
    "contact.removed": _("removed an important number"),
    "contacts.imported": _("imported important numbers"),
    "contact_type.created": _("added a number type"),
    "contact_type.updated": _("changed a number type"),
    "contact_type.deleted": _("deleted a number type"),
    "contact.handled": _("marked a contact message handled"),
    "contact.reopened": _("marked a contact message as new"),
    # Citizens
    "citizen.registered": _("registered a citizen phone"),
    "citizen.blocked": _("blocked a citizen’s phone"),
    "citizen.unblocked": _("unblocked a citizen’s phone"),
    "citizen.block_ended": _("ended a citizen’s block (its time was up)"),
    "citizen.posts_discarded": _("discarded a citizen’s waiting posts"),
    # Citizen wallet accounts (BVTGC-106)
    "citizen.code_sent": _("sent a wallet code by email"),
    "citizen.account_created": _("created a citizen wallet account"),
    "citizen.password_reset": _("reset a forgotten wallet password with an email code"),
    "citizen.password_changed": _("changed their wallet password"),
    "citizen.email_changed": _("changed their wallet account’s email"),
    "citizen.mobile_changed": _("changed their wallet account’s mobile number"),
    "citizen.village_changed": _("moved their wallet account to another village"),
    # Reports and delivery
    "report.exported": _("exported a report"),
    "email.failed": _("email not delivered"),
    "email.skipped": _("email skipped"),
    "push.failed": _("notification not delivered"),
}


def action_label(action: str) -> str:
    """The words for an audit action in the active language; never empty, never the bare code alone."""
    label = ACTION_LABELS.get(action)
    if label is not None:
        return str(label)
    return gettext("other action (%(code)s)") % {"code": action}
