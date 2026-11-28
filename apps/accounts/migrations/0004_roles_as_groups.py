"""Roles become Django groups (step 1 of 3: new columns next to the old ones).

- RoleGroup: the level, rank and manage permission of each group that is a role.
- RoleAssignment gets a group, is_active, assigned_by and assigned_at.
- User gets a real is_active field (it replaces status in 0006), email is a
  plain contact field (no longer unique, no longer a login name), and the
  user-management permissions.
0005 fills the new columns; 0006 drops the old ones.
"""

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0003_login_attempt_indexes"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="RoleGroup",
            fields=[
                (
                    "group",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="role_group",
                        serialize=False,
                        to="auth.group",
                    ),
                ),
                ("key", models.CharField(help_text="Sent to clients as the role.", max_length=20, unique=True, verbose_name="key")),
                ("name_te", models.CharField(blank=True, max_length=60, verbose_name="name in Telugu")),
                (
                    "level",
                    models.CharField(
                        choices=[("STATE", "State"), ("DISTRICT", "District"), ("MANDAL", "Mandal"), ("VILLAGE", "Village")],
                        max_length=10,
                        verbose_name="area level",
                    ),
                ),
                ("rank", models.PositiveSmallIntegerField(help_text="0 is the highest.", unique=True, verbose_name="rank")),
                ("platform_wide", models.BooleanField(default=False, verbose_name="platform-wide")),
                (
                    "manage_permission",
                    models.CharField(
                        blank=True,
                        help_text="Empty: nobody can give it through the API.",
                        max_length=100,
                        verbose_name="permission needed to give this role",
                    ),
                ),
                (
                    "requires_setting",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        help_text='For example {"posts.create_post": "SUPER_ADMIN_CAN_POST"}.',
                        verbose_name="permissions that need a setting",
                    ),
                ),
            ],
            options={
                "verbose_name": "role group",
                "verbose_name_plural": "role groups",
                "ordering": ["rank"],
            },
        ),
        migrations.AddField(
            model_name="roleassignment",
            name="group",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="role_assignments",
                to="auth.group",
                verbose_name="role",
            ),
        ),
        migrations.AddField(
            model_name="roleassignment",
            name="is_active",
            field=models.BooleanField(default=True, verbose_name="active"),
        ),
        migrations.RenameField(model_name="roleassignment", old_name="created_by", new_name="assigned_by"),
        migrations.RenameField(model_name="roleassignment", old_name="created_at", new_name="assigned_at"),
        migrations.AlterField(
            model_name="roleassignment",
            name="assigned_at",
            field=models.DateTimeField(default=django.utils.timezone.now, verbose_name="assigned"),
        ),
        migrations.AlterField(
            model_name="roleassignment",
            name="assigned_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterModelOptions(
            name="roleassignment",
            options={
                "ordering": ["assigned_at"],
                "verbose_name": "role assignment",
                "verbose_name_plural": "role assignments",
            },
        ),
        migrations.AddField(
            model_name="user",
            name="is_active",
            field=models.BooleanField(
                db_index=True,
                default=True,
                help_text="Untick to block: every session and token stops working at once.",
                verbose_name="active",
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="email",
            field=models.EmailField(blank=True, help_text="Contact only.", max_length=254, null=True, verbose_name="email"),
        ),
        migrations.AlterField(
            model_name="user",
            name="mobile",
            field=models.CharField(
                blank=True,
                help_text="10 digits, without +91. Contact only, optional.",
                max_length=10,
                null=True,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Enter a 10-digit mobile number starting with 6, 7, 8 or 9.", regex="^[6-9]\\d{9}$"
                    )
                ],
                verbose_name="mobile number",
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="must_change_password",
            field=models.BooleanField(
                default=False,
                help_text="Set for a new account or after a reset; cleared when the person picks their own password.",
                verbose_name="must change password",
            ),
        ),
        migrations.AlterField(
            model_name="user",
            name="username",
            field=models.CharField(
                help_text="Stored in lower case; login ignores case.",
                max_length=150,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Use 3 to 150 letters, digits or @ . _ + - characters.", regex="^[a-z0-9@._+-]{3,150}$"
                    )
                ],
                verbose_name="username",
            ),
        ),
        migrations.AlterModelOptions(
            name="user",
            options={
                "ordering": ["name"],
                "permissions": [
                    ("manage_district_admin", "Give, move and remove the District Admin role"),
                    ("manage_mandal_admin", "Give, move and remove the Mandal Admin role"),
                    ("manage_village_admin", "Give, move and remove the Village Admin role"),
                    ("manage_panchayat_member", "Give, move and remove the Panchayat Member role"),
                    ("block_user", "Block and unblock officials"),
                    ("reset_password", "Reset the password of an official"),
                    ("reassign_user", "Move the role of an official to another area"),
                    ("unlock_user", "Unlock an official locked out by failed logins"),
                    ("manage_groups", "Edit groups and role assignments in Django admin"),
                ],
                "verbose_name": "user",
                "verbose_name_plural": "users",
            },
        ),
    ]
