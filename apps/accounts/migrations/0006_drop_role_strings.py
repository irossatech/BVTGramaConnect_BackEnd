"""Roles become Django groups (step 3 of 3: drop what 0005 replaced).

- RoleAssignment.role (the old role string): the group says it now.
- User.status: is_active says it now.
- RoleDefaultPassword (emailed role passwords) and LoginAttempt (the old
  throttle): replaced by temporary passwords set per person and by django-axes.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_roles_to_groups"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="roleassignment", name="unique_role_assignment"),
        migrations.RemoveIndex(model_name="roleassignment", name="accounts_ro_role_b4ac75_idx"),
        migrations.RenameIndex(
            model_name="roleassignment",
            new_name="accounts_ra_area_idx",
            old_name="accounts_ro_area_le_d89219_idx",
        ),
        migrations.RemoveField(model_name="roleassignment", name="role"),
        migrations.AlterField(
            model_name="roleassignment",
            name="group",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="role_assignments",
                to="auth.group",
                verbose_name="role",
            ),
        ),
        migrations.AddConstraint(
            model_name="roleassignment",
            constraint=models.UniqueConstraint(
                fields=("user", "group", "area_level", "area_id"), name="unique_group_assignment"
            ),
        ),
        migrations.AddIndex(
            model_name="roleassignment",
            index=models.Index(fields=["group", "area_level", "area_id"], name="accounts_ra_group_area_idx"),
        ),
        migrations.RemoveField(model_name="user", name="status"),
        migrations.DeleteModel(name="RoleDefaultPassword"),
        migrations.DeleteModel(name="LoginAttempt"),
    ]
