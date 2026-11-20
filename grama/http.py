from django.conf import settings


def client_ip(request) -> str | None:
    """The caller's IP. Behind Nginx, trust X-Real-IP only when TRUST_X_REAL_IP is on."""
    if settings.TRUST_X_REAL_IP:
        forwarded = request.META.get("HTTP_X_REAL_IP", "").strip()
        if forwarded:
            return forwarded
    return request.META.get("REMOTE_ADDR") or None
