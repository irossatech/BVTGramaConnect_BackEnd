"""The database cache table the login throttle counts in (security gap G1, settings CACHES["throttle"]).

Django's own createcachetable makes it, on SQLite, MySQL and PostgreSQL alike, and skips a
table that exists. With a memory cache (the tests) there is nothing to make.
"""

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command("createcachetable", database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):
    dependencies = [("accounts", "0007_rolegroup_password_min_length")]

    operations = [migrations.RunPython(create_cache_table, migrations.RunPython.noop)]
