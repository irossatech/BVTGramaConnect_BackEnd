"""Login (app and console), passwords, /me and user management. docs/api-contract.md, section 3. Mounted under /api/v1/."""

from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views_auth, views_users

app_name = "accounts"

router = SimpleRouter()
router.register("users", views_users.UserViewSet, basename="user")

assignment_list = views_users.RoleAssignmentViewSet.as_view({"post": "create"})
assignment_detail = views_users.RoleAssignmentViewSet.as_view({"patch": "partial_update", "delete": "destroy"})

urlpatterns = [
    # Mobile app: JWT.
    path("auth/login/", views_auth.LoginView.as_view(), name="login"),
    path("auth/refresh/", views_auth.RefreshView.as_view(), name="token-refresh"),
    path("auth/logout/", views_auth.LogoutView.as_view(), name="logout"),
    # Web console: Django session with CSRF.
    path("auth/csrf/", views_auth.CsrfView.as_view(), name="csrf"),
    path("auth/session/login/", views_auth.SessionLoginView.as_view(), name="session-login"),
    path("auth/session/logout/", views_auth.SessionLogoutView.as_view(), name="session-logout"),
    # Both.
    path("auth/password/change/", views_auth.PasswordChangeView.as_view(), name="password-change"),
    path("me/", views_auth.MeView.as_view(), name="me"),
    path("users/<uuid:user_pk>/role-assignments/", assignment_list, name="role-assignments"),
    path("users/<uuid:user_pk>/role-assignments/<uuid:pk>/", assignment_detail, name="role-assignment"),
    *router.urls,
]
