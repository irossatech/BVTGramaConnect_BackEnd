from django.urls import path

from . import views, views_admin

app_name = "wallet"

urlpatterns = [
    # The citizen's wallet (contract section 21, BVTGC-126).
    path("wallet/", views.WalletView.as_view(), name="wallet"),
    # The Super Admin (BVTGC-128).
    path("admin/referrals/", views_admin.ReferralsView.as_view(), name="admin-referrals"),
    path(
        "admin/referrals/<uuid:pk>/confirm/", views_admin.ReferralDecisionView.as_view(confirm=True),
        name="admin-referral-confirm",
    ),
    path(
        "admin/referrals/<uuid:pk>/reject/", views_admin.ReferralDecisionView.as_view(confirm=False),
        name="admin-referral-reject",
    ),
    path("admin/wallets/<uuid:account_id>/", views_admin.AdminWalletView.as_view(), name="admin-wallet"),
    path("admin/wallets/<uuid:account_id>/adjust/", views_admin.AdjustWalletView.as_view(), name="admin-wallet-adjust"),
    path("admin/wallet/entries/", views_admin.WalletEntriesView.as_view(), name="admin-wallet-entries"),
    path("admin/wallet/export/", views_admin.WalletExportView.as_view(), name="admin-wallet-export"),
]
