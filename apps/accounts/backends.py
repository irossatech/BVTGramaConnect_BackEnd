from django.contrib.auth.backends import ModelBackend


class OfficialBackend(ModelBackend):
    """ModelBackend whose login step also returns blocked users, so the login view can say "account_blocked".

    Only authenticate() lets them through; get_user() still refuses them, so a
    blocked user's session never authenticates, and Django admin refuses them
    at login with its usual "inactive" message. Usernames are matched without
    case (UserManager.get_by_natural_key). django-axes runs before this
    backend (AUTHENTICATION_BACKENDS) and refuses locked-out logins.
    """

    def user_can_authenticate(self, user):
        return True

    def get_user(self, user_id):
        user = super().get_user(user_id)
        return user if user is not None and user.is_active else None
