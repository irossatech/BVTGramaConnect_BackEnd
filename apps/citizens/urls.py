from django.urls import path

from . import views, views_account, views_admin

app_name = "citizens"

urlpatterns = [
    path("citizen/challenge/", views.ChallengeView.as_view(), name="challenge"),
    path("citizen/register/", views.RegisterView.as_view(), name="register"),
    path("citizen/me/", views.MeView.as_view(), name="me"),
    path("citizen/posts/", views.PostsView.as_view(), name="posts"),
    # Citizens' wallet accounts (contract section 21, BVTGC-123 on).
    path("citizen/auth/signup/", views_account.SignupView.as_view(), name="signup"),
    path("citizen/auth/signup/resend/", views_account.SignupResendView.as_view(), name="signup-resend"),
    path("citizen/auth/signup/verify/", views_account.SignupVerifyView.as_view(), name="signup-verify"),
    path("citizen/auth/login/", views_account.LoginView.as_view(), name="login"),
    path("citizen/auth/password/forgot/", views_account.ForgotPasswordView.as_view(), name="password-forgot"),
    path("citizen/auth/password/reset/", views_account.ResetPasswordView.as_view(), name="password-reset"),
    path("citizen/account/", views_account.AccountView.as_view(), name="account"),
    path("citizen/account/password/", views_account.AccountPasswordView.as_view(), name="account-password"),
    path("citizen/account/email/", views_account.AccountEmailView.as_view(), name="account-email"),
    path("citizen/account/email/verify/", views_account.AccountEmailVerifyView.as_view(), name="account-email-verify"),
    path("citizen/account/mobile/", views_account.AccountMobileView.as_view(), name="account-mobile"),
    path("citizen/account/delete/", views_account.AccountDeleteView.as_view(), name="account-delete"),
    path("citizen/auth/delete/code/", views_account.DeleteCodeView.as_view(), name="delete-code"),
    path("citizen/auth/delete/", views_account.DeleteWithCodeView.as_view(), name="delete"),
    # The Super Admin's blocked citizens (owner's request of 2026-11-21).
    path("admin/citizens/blocked/", views_admin.BlockedCitizensView.as_view(), name="admin-blocked"),
    path("admin/citizens/<uuid:pk>/blocks/", views_admin.CitizenBlocksView.as_view(), name="admin-blocks"),
    path("admin/citizens/<uuid:pk>/unblock/", views_admin.UnblockCitizenView.as_view(), name="admin-unblock"),
]
