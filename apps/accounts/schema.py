"""OpenAPI descriptions of the two ways to authenticate (drf-spectacular extensions)."""

from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme
from drf_spectacular.extensions import OpenApiAuthenticationExtension


class GramaJWTScheme(SimpleJWTScheme):
    """The mobile app: Authorization: Bearer <access token>."""

    target_class = "apps.accounts.authentication.GramaJWTAuthentication"
    name = "jwtAuth"


class GramaSessionScheme(OpenApiAuthenticationExtension):
    """The web console: the sessionid cookie, plus X-CSRFToken on writes."""

    target_class = "apps.accounts.authentication.GramaSessionAuthentication"
    name = "consoleSession"

    def get_security_definition(self, auto_schema):
        return {"type": "apiKey", "in": "cookie", "name": "sessionid"}
