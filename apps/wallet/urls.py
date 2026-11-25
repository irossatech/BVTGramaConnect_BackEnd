from django.urls import path

from . import views

app_name = "wallet"

urlpatterns = [
    # The citizen's wallet (contract section 21, BVTGC-126).
    path("wallet/", views.WalletView.as_view(), name="wallet"),
]
