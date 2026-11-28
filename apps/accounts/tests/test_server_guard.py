"""The sample-data commands refuse on a server (DEBUG off) unless --allow-server is given."""

from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import User
from apps.geography.models import Village
from apps.posts.models import Post

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "command, args",
    [
        ("seed_sample_data", []),
        ("load_demo_area", ["--district", "Guntur", "--mandal", "Tenali"]),
    ],
)
def test_refused_on_a_server_and_nothing_is_written(world, settings, command, args):
    settings.DEBUG = False
    users, villages, posts = User.objects.count(), Village.objects.count(), Post.objects.count()
    with pytest.raises(CommandError, match="--allow-server"):
        call_command(command, *args, stdout=StringIO())
    assert (User.objects.count(), Village.objects.count(), Post.objects.count()) == (users, villages, posts)


def test_seed_runs_on_a_server_only_when_asked(settings):
    settings.DEBUG = False
    call_command("seed_sample_data", "--allow-server", stdout=StringIO())
    assert User.objects.filter(username="super.admin").exists()


def test_demo_area_runs_on_a_server_only_when_asked(world, settings):
    settings.DEBUG = False
    call_command("load_demo_area", "--district", "Guntur", "--mandal", "Tenali", "--allow-server", stdout=StringIO())
    assert User.objects.filter(username__startswith="demo.").exists()


def test_create_super_admin_has_no_guard():
    """The real first Super Admin is made on the server: that command must keep working there."""
    from apps.accounts.management.commands import create_super_admin

    parser = create_super_admin.Command().create_parser("manage.py", "create_super_admin")
    assert "--allow-server" not in parser.format_help()
