"""Login, passwords, /me and user management. See docs/api-contract.md, section 3. Mounted under /api/v1/."""

from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views_auth, views_users

app_name = "accounts"

router = SimpleRouter()
router.register("users", views_users.UserViewSet, basename="user")

assignment_list = views_users.RoleAssignmentViewSet.as_view({"post": "create"})
assignment_detail = views_users.RoleAssignmentViewSet.as_view({"patch": "partial_update", "delete": "destroy"})

urlpatterns = [
    path("auth/login/", views_auth.LoginView.as_view(), name="login"),
    path("auth/password/change/", views_auth.PasswordChangeView.as_view(), name="password-change"),
    path("auth/refresh/", views_auth.RefreshView.as_view(), name="token-refresh"),
    path("auth/logout/", views_auth.LogoutView.as_view(), name="logout"),
    path("me/", views_auth.MeView.as_view(), name="me"),
    path("admin/role-passwords/", views_auth.RolePasswordListView.as_view(), name="role-passwords"),
    path("admin/role-passwords/<str:role>/", views_auth.RolePasswordDetailView.as_view(), name="role-password"),
    path("users/<uuid:user_pk>/role-assignments/", assignment_list, name="role-assignments"),
    path("users/<uuid:user_pk>/role-assignments/<uuid:pk>/", assignment_detail, name="role-assignment"),
    *router.urls,
]
