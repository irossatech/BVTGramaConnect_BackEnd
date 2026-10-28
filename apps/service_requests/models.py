"""Citizens' service requests: removed on 2026-10-28, to be rebuilt with services the Super Admin creates.

The app stays installed with no models so its migrations, which notifications
migration 0006 depends on, keep running; migration 0002 drops the old tables.
"""
