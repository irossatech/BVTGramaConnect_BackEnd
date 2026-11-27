"""The database cache table the Telugu name suggestions are kept in (settings CACHES["names"], BE-PERF-202).

Django's own createcachetable makes it, on SQLite, MySQL and PostgreSQL alike, and skips a table that
exists, as accounts 0008 does for the throttle cache. With a memory cache (laptops, tests) there is
nothing to make. The reverse step leaves the table: it holds nothing that must go.
"""

from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    call_command("createcachetable", database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):
    dependencies = [("web_admin", "0001_console_permissions")]

    operations = [migrations.RunPython(create_cache_table, migrations.RunPython.noop)]
