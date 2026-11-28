"""Switch from SMS OTP login to username + password.

- Users get a username (existing users: their mobile number), an optional
  unique email, must_change_password and password_changed_at.
- The mobile number becomes optional.
- OTP requests are dropped; role default passwords and login attempts added.
"""

import uuid

import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def fill_usernames(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    taken = set()
    for user in User.objects.all().only("id", "mobile"):
        candidate = (user.mobile or "").strip() or f"user-{uuid.uuid4().hex[:8]}"
        while candidate in taken:
            candidate = f"user-{uuid.uuid4().hex[:8]}"
        taken.add(candidate)
        User.objects.filter(pk=user.pk).update(username=candidate)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="username",
            field=models.CharField(max_length=150, null=True, verbose_name="username"),
        ),
        migrations.AddField(
            model_name="user",
            name="email",
            field=models.EmailField(blank=True, max_length=254, null=True, unique=True, verbose_name="email"),
        ),
        migrations.AddField(
            model_name="user",
            name="must_change_password",
            field=models.BooleanField(
                default=False,
                help_text="Set after an invite or a reset; cleared when the person picks their own password.",
                verbose_name="must change password",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="password_changed_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="password changed"),
        ),
        migrations.AlterField(
            model_name="user",
            name="mobile",
            field=models.CharField(
                blank=True,
                help_text="10 digits, without +91. Optional.",
                max_length=10,
                null=True,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Enter a 10-digit mobile number starting with 6, 7, 8 or 9.",
                        regex="^[6-9]\\d{9}$",
                    )
                ],
                verbose_name="mobile number",
            ),
        ),
        migrations.RunPython(fill_usernames, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="username",
            field=models.CharField(
                help_text="Stored in lower case. People may also log in with their email.",
                max_length=150,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Use 3 to 150 letters, digits or @ . _ + - characters.",
                        regex="^[a-z0-9@._+-]{3,150}$",
                    )
                ],
                verbose_name="username",
            ),
        ),
        migrations.DeleteModel(name="OtpRequest"),
        migrations.CreateModel(
            name="RoleDefaultPassword",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("SUPER_ADMIN", "Super Admin"),
                            ("DISTRICT_ADMIN", "District Admin"),
                            ("MANDAL_ADMIN", "Mandal Admin"),
                            ("VILLAGE_ADMIN", "Village Admin"),
                            ("PANCHAYAT_MEMBER", "Panchayat Member"),
                        ],
                        max_length=20,
                        unique=True,
                        verbose_name="role",
                    ),
                ),
                ("password", models.CharField(max_length=128, verbose_name="default password")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "default password for a role",
                "verbose_name_plural": "default passwords for roles",
            },
        ),
        migrations.CreateModel(
            name="LoginAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("username", models.CharField(db_index=True, max_length=254)),
                ("ip_address", models.GenericIPAddressField(blank=True, null=True)),
                ("success", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
    ]
