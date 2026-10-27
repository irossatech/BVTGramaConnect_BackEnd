"""The role matrix: which Django group gets which permission. The one place it is written down.

Each official's access comes from their role assignments: one group (a role)
in one area. A group's permissions apply only inside the assignment's area and
the areas below it (State > District > Mandal > Village); apps/accounts/authz.py
does that check. A platform-wide group (Super Admin) covers every area.

`python manage.py sync_role_permissions` makes the database match this dict.
`migrate` runs the same sync at the end, so a deploy always applies it.
Nothing else in the code may test a group's name or key; code asks for a
permission instead (authz.can(user, "posts.publish_post", area)).

Fields:
- name, name_te: the Django group name and its Telugu label.
- level: the area level the group is assigned at.
- rank: 0 is the highest. An admin may only assign, block, reset or unlock
  people whose groups rank strictly below their own.
- platform_wide: the assignment covers every area, whatever area it names.
- manage_permission: the permission an admin needs to give this group to
  someone (None: nobody can give it through the API).
- permissions: "app_label.codename" strings.
- requires_setting: permissions the group gets only while a Django setting is
  true. Checked by the sync and again on every request.

To add a permission: add it to a model's Meta.permissions (and a migration),
list it here for the groups that need it, run sync_role_permissions, and ask
for it in code with authz.can() or HasPerm. docs/authorization.md has the steps.
"""

POSTING = ["posts.create_post", "posts.submit_post"]
REVIEWING = [
    "posts.review_post",
    "posts.publish_post",
    "posts.recheck_post",
    "posts.discard_post",
    "posts.takedown_post",
    "posts.reverse_decision",
    "posts.view_review_queue",
    "posts.mark_urgent",
]
USER_ACTIONS = [
    "accounts.block_user",
    "accounts.reset_password",
    "accounts.reassign_user",
    "accounts.unlock_user",
]
# Important numbers citizens call (docs/api-contract.md, section 14): every official, in their own
# area; Panchayat members too (decision C1). Statewide helplines need a STATE area, so only the Super Admin.
CONTACTS = ["contacts.manage_contact"]
PLATFORM_ADMIN = [
    "accounts.manage_groups",
    "geography.manage_master_data",
    "audit.view_audit_log",
    "audit.export_audit_log",
    "web_admin.access_web_console",
    "web_admin.view_dashboard",
    "web_admin.view_reports",
    "notifications.manage_templates",
    "web_public.manage_contact_messages",
    "contacts.manage_contact_types",
    "posts.manage_categories",
    "posts.manage_settings",
]

ROLE_GROUPS: dict[str, dict] = {
    "SUPER_ADMIN": {
        "name": "Super Admin",
        "name_te": "సూపర్ అడ్మిన్",
        "level": "STATE",
        "rank": 0,
        "platform_wide": True,
        "manage_permission": None,
        "permissions": [
            *POSTING,
            *REVIEWING,
            # Citizen posts (docs/api-contract.md, section 15): only the Super Admin blocks or unblocks
            # a citizen's phone and discards all its pending posts (lead's decision of 2026-10-27).
            "posts.block_citizen",
            "posts.view_stats",
            "accounts.manage_district_admin",
            "accounts.manage_mandal_admin",
            "accounts.manage_village_admin",
            "accounts.manage_panchayat_member",
            *USER_ACTIONS,
            *CONTACTS,
            *PLATFORM_ADMIN,
        ],
        # CLAUDE.md "Configurable rules": the Super Admin posts only when this is on.
        "requires_setting": {perm: "SUPER_ADMIN_CAN_POST" for perm in POSTING},
    },
    "DISTRICT_ADMIN": {
        "name": "District Admin",
        "name_te": "జిల్లా అడ్మిన్",
        "level": "DISTRICT",
        "rank": 1,
        "platform_wide": False,
        "manage_permission": "accounts.manage_district_admin",
        "permissions": [
            *POSTING,
            *REVIEWING,
            "posts.view_stats",
            "accounts.manage_mandal_admin",
            "accounts.manage_village_admin",
            "accounts.manage_panchayat_member",
            *USER_ACTIONS,
            *CONTACTS,
        ],
    },
    "MANDAL_ADMIN": {
        "name": "Mandal Admin",
        "name_te": "మండల అడ్మిన్",
        "level": "MANDAL",
        "rank": 2,
        "platform_wide": False,
        "manage_permission": "accounts.manage_mandal_admin",
        "permissions": [
            *POSTING,
            *REVIEWING,
            "posts.view_stats",
            "accounts.manage_village_admin",
            "accounts.manage_panchayat_member",
            *USER_ACTIONS,
            *CONTACTS,
        ],
    },
    "VILLAGE_ADMIN": {
        "name": "Village Admin",
        "name_te": "గ్రామ అడ్మిన్",
        "level": "VILLAGE",
        "rank": 3,
        "platform_wide": False,
        "manage_permission": "accounts.manage_village_admin",
        "permissions": [
            *POSTING,
            *REVIEWING,
            "posts.view_stats",
            "accounts.manage_panchayat_member",
            *USER_ACTIONS,
            *CONTACTS,
        ],
    },
    "PANCHAYAT_MEMBER": {
        "name": "Panchayat Member",
        "name_te": "పంచాయతీ సభ్యుడు",
        "level": "VILLAGE",
        "rank": 4,
        "platform_wide": False,
        "manage_permission": "accounts.manage_panchayat_member",
        # Their posts wait for review: they lack publish_post (posts/services.py).
        "permissions": [*POSTING, "posts.view_stats", *CONTACTS],
    },
}

#: Every permission the dict uses, for the tests and the docs table.
ALL_PERMISSIONS: list[str] = sorted({perm for spec in ROLE_GROUPS.values() for perm in spec["permissions"]})
