"""Settings package.

- base.py: shared by every environment.
- dev.py: local development on SQLite. Default for manage.py.
- prod.py: the Linux server. Default for wsgi.py and asgi.py. Reads secrets from env.
- test.py: pytest.

Pick one with DJANGO_SETTINGS_MODULE, for example grama.settings.prod.
"""
