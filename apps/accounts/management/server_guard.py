"""Guard for the sample-data commands (seed_sample_data, load_demo_area).

They add officials (seed_sample_data even a Super Admin), places, numbers and published posts
that are not real. On a laptop (DEBUG on) they run as they always did. On a server (DEBUG off:
qa and production) they refuse unless --allow-server is given, so they never run there by a
mistyped or copied command. The qa deploy script passes it for SEED=1; production never does.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import CommandError

FLAG = "--allow-server"


def add_allow_server(parser) -> None:
    parser.add_argument(
        FLAG, action="store_true",
        help="Run on a server (DEBUG off). Only for qa; never on production.",
    )


def refuse_on_server(command_name: str, allow_server: bool) -> None:
    if settings.DEBUG or allow_server:
        return
    raise CommandError(
        f"Refusing to run {command_name}: this looks like a server (DEBUG is off), and it writes "
        f"sample officials and posts that are not real. On qa, add {FLAG} if you really mean it. "
        "Never run it on production."
    )
