"""The wallet's console settings, GET /app-config/, the off switch and the wallet pushes (BVTGC-129;
requirements section 7, FR-30, FR-31, FR-34; contract section 21, "Settings" and "Pushes and audit").
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import transaction
from django.urls import URLPattern
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import RoleAssignment, User
from apps.audit.models import AuditLog
from apps.citizens import services_account as accounts
from apps.citizens import urls as citizen_urls
from apps.citizens.models import CitizenAccount
from apps.citizens.services import hash_android_id
from apps.citizens.services_account import canonical_email, hash_mobile
from apps.geography.areas import AreaRef
from apps.geography.models import AreaLevel, Mandal
from apps.notifications.models import Device, Notification, NotificationTemplate
from apps.notifications.push import LocmemPushGateway
from apps.posts.models import CITIZEN_ROLE
from apps.wallet import services
from apps.wallet import urls as wallet_urls
from apps.wallet.models import Referral, Wallet, WalletEntry

pytestmark = pytest.mark.django_db

SETTINGS = "/api/v1/admin/settings/"
APP_CONFIG = "/api/v1/app-config/"
DEFAULTS = {
    "wallet_enabled": False, "wallet_pilot_mandals": [], "referral_reward_points": 50, "welcome_bonus_points": 20,
    "referral_hold_days": 7, "referral_active_days": 2, "referrals_per_month": 30, "welcome_bonuses_per_phone": 3,
}


@pytest.fixture(autouse=True)
def outbox():
    LocmemPushGateway.outbox.clear()
    yield LocmemPushGateway.outbox
    LocmemPushGateway.outbox.clear()


@pytest.fixture
def admin(api, world):
    return api(world.users["super"])


def err(response) -> str:
    return response.json()["error"]["code"]


def make_citizen(world, n: int, *, name: str | None = None, language: str = "te", phone: str | None = None) -> CitizenAccount:
    user = User.objects.create_user(
        username=f"citizen.s{n}", name=name or f"Citizen {n}", password="48213957", preferred_language=language
    )
    ref = world.ref("kolakaluru")
    RoleAssignment.objects.assign(user, CITIZEN_ROLE, AreaRef(AreaLevel.VILLAGE, ref.id))
    email, mobile = f"settings{n}@gmail.com", f"8{n:09d}"
    now = timezone.now()
    account = CitizenAccount.objects.create(
        user=user, village_id=ref.id, age_confirmed_at=now, consent_version="v1", consent_at=now,
        email=email, email_key=canonical_email(email), email_verified_at=now, mobile_hash=hash_mobile(mobile),
        mobile_last4=mobile[-4:], referral_code=f"SET{n:05d}", signup_android_id_hash=hash_android_id(phone or f"phone-{n}"),
    )
    Wallet.objects.create(account=account)
    Device.objects.create(user=user, fcm_token=f"tok-citizen-{n}", platform="android")
    return account


def pushes(outbox) -> dict[str, tuple[str, dict]]:
    """token -> (title, data) of every push sent."""
    return {message.token: (message.title, message.data) for message in outbox}


# --- The console settings -----------------------------------------------------------------------------------

def test_the_settings_show_the_wallet_defaults(admin):
    body = admin.get(SETTINGS).json()
    assert {key: body[key] for key in DEFAULTS} == DEFAULTS


def test_a_change_is_saved_audited_and_used_at_once(admin, world, settings):
    settings.WALLET_PILOT_MANDALS = []
    mandals = sorted(Mandal.objects.values_list("id", flat=True))[:2]
    change = {"wallet_enabled": True, "wallet_pilot_mandals": [mandals[1], mandals[0], mandals[1]],
              "referral_reward_points": 80, "welcome_bonus_points": 0, "referrals_per_month": 0}
    response = admin.patch(SETTINGS, change, format="json")
    assert response.status_code == 200
    assert response.json()["wallet_pilot_mandals"] == mandals  # sorted, without repeats
    entry = AuditLog.objects.get(action="setting.changed")
    assert entry.data["before"]["referral_reward_points"] == 50 and entry.data["after"]["referral_reward_points"] == 80
    assert entry.data["after"]["wallet_enabled"] is True

    assert accounts.wallet_enabled() is True
    assert sorted(accounts.pilot_mandals()) == mandals
    referral = services.start_referral(make_citizen(world, 2), make_citizen(world, 1))
    rows = WalletEntry.objects.filter(referral=referral).values_list("kind", "points")
    assert list(rows) == [(WalletEntry.Kind.REFERRAL_REWARD, 80)]  # 0 welcome points write no entry


@pytest.mark.parametrize("change", [
    {"referral_reward_points": -1}, {"referral_reward_points": 10_001}, {"welcome_bonus_points": 10_001},
    {"referral_hold_days": 0}, {"referral_hold_days": 91}, {"referral_active_days": 8},
    {"referral_hold_days": 1}, {"referral_hold_days": 10, "referral_active_days": 11},
    {"referrals_per_month": 1001}, {"welcome_bonuses_per_phone": 11}, {"wallet_pilot_mandals": [999_999]},
    {"wallet_pilot_mandals": ["x"]}, {"wallet_enabled": "maybe"},
])
def test_values_out_of_range_save_nothing(admin, change):
    response = admin.patch(SETTINGS, change, format="json")
    assert (response.status_code, err(response)) == (400, "validation_error")
    assert set(response.json()["error"]["fields"]) <= set(DEFAULTS)
    body = admin.get(SETTINGS).json()
    assert {key: body[key] for key in DEFAULTS} == DEFAULTS
    assert not AuditLog.objects.filter(action="setting.changed").exists()


def test_the_active_days_follow_the_saved_hold(admin):
    assert admin.patch(SETTINGS, {"referral_hold_days": 3}, format="json").status_code == 200
    assert err(admin.patch(SETTINGS, {"referral_active_days": 4}, format="json")) == "validation_error"
    assert admin.patch(SETTINGS, {"referral_hold_days": 10, "referral_active_days": 9}, format="json").status_code == 200


def test_an_inactive_mandal_is_refused(admin):
    mandal = Mandal.objects.first()
    Mandal.objects.filter(pk=mandal.pk).update(is_active=False)
    response = admin.patch(SETTINGS, {"wallet_pilot_mandals": [mandal.pk]}, format="json")
    assert response.json()["error"]["fields"]["wallet_pilot_mandals"]


def test_only_the_super_admin_changes_them(api, world):
    for name in ("da_guntur", "ma_tenali", "va_kolakaluru", "pm_kolakaluru"):
        response = api(world.users[name]).patch(SETTINGS, {"wallet_enabled": True}, format="json")
        assert response.status_code == 403
    assert accounts.wallet_enabled() is False


# --- GET /app-config/ ------------------------------------------------------------------------------------------

def test_app_config_tells_the_app_about_the_wallet(admin, settings):
    settings.CITIZEN_CONSENT_VERSION = "2026-11-25"
    data = APIClient().get(APP_CONFIG).json()
    assert (data["wallet_enabled"], data["wallet_pilot_mandals"], data["wallet_consent_version"]) == (False, [], "2026-11-25")
    mandal = Mandal.objects.order_by("pk").first().pk
    admin.patch(SETTINGS, {"wallet_enabled": True, "wallet_pilot_mandals": [mandal]}, format="json")
    data = APIClient().get(APP_CONFIG).json()
    assert (data["wallet_enabled"], data["wallet_pilot_mandals"]) == (True, [mandal])


# --- The switch hides every citizen wallet endpoint --------------------------------------------------------------

def citizen_wallet_routes() -> list[tuple[str, str]]:
    """(address, method) of every citizen-facing wallet route: the Super Admin's work while it is off."""
    found = []
    for module in (citizen_urls, wallet_urls):
        for pattern in module.urlpatterns:
            assert isinstance(pattern, URLPattern)
            route = str(pattern.pattern)
            if not route.startswith(("citizen/auth/", "citizen/account/", "wallet/")):
                continue
            view = pattern.callback.view_class
            for method in ("get", "post", "patch", "put", "delete"):
                if hasattr(view, method):
                    found.append(("/api/v1/" + route, method))
    return found


def test_the_walk_finds_every_route():
    routes = {address for address, _method in citizen_wallet_routes()}
    assert len(routes) == 15 and "/api/v1/wallet/" in routes and "/api/v1/citizen/auth/delete/" in routes


@pytest.mark.parametrize("address, method", citizen_wallet_routes())
def test_every_citizen_wallet_endpoint_is_hidden_while_off(api, world, settings, address, method):
    settings.WALLET_ENABLED = True
    citizen = make_citizen(world, 1)
    settings.WALLET_ENABLED = False
    for client in (api(), api(citizen.user)):
        response = getattr(client, method)(address, {}, format="json")
        assert (response.status_code, err(response)) == (404, "not_found"), (address, method)


def test_the_console_switch_opens_them(admin, api, world, settings):
    settings.WALLET_ENABLED = False
    citizen = make_citizen(world, 1)
    assert api(citizen.user).get("/api/v1/wallet/").status_code == 404
    admin.patch(SETTINGS, {"wallet_enabled": True}, format="json")
    assert api(citizen.user).get("/api/v1/wallet/").status_code == 200


# --- Pushes (FR-30, FR-31) -----------------------------------------------------------------------------------------

@pytest.fixture
def pair(world, settings):
    settings.WALLET_ENABLED = True
    return make_citizen(world, 1, name="లక్ష్మి"), make_citizen(world, 2, name="Ravi", language="en")


def test_the_referrer_hears_that_a_friend_joined(pair, outbox, django_capture_on_commit_callbacks):
    referrer, friend = pair
    with django_capture_on_commit_callbacks(execute=True):
        referral = services.start_referral(friend, referrer)
    sent = pushes(outbox)
    assert list(sent) == ["tok-citizen-1"]  # the friend is not told
    title, data = sent["tok-citizen-1"]
    assert title == "Ravi మీ ఆహ్వాన కోడ్‌తో గ్రామ కనెక్ట్‌లో చేరారు. మీ వాలెట్ చూడండి."
    assert data == {"type": "WALLET", "event": "referral_joined", "referral_id": str(referral.id)}
    assert not Notification.objects.exists()  # push only: the wallet history is the record


def test_nothing_is_sent_when_the_sign_up_fails(pair, outbox, django_capture_on_commit_callbacks):
    referrer, friend = pair
    with django_capture_on_commit_callbacks(execute=True):
        try:
            with transaction.atomic():
                services.start_referral(friend, referrer)
                raise RuntimeError("the sign-up failed after the referral")
        except RuntimeError:
            pass
    assert outbox == [] and not Referral.objects.exists()


def test_both_hear_their_confirmed_points_in_their_language(pair, outbox, django_capture_on_commit_callbacks):
    referrer, friend = pair
    referral = services.start_referral(friend, referrer)
    with django_capture_on_commit_callbacks(execute=True):
        services.decide(referral, confirm=True)
    sent = pushes(outbox)
    assert sent["tok-citizen-1"][0] == "మీ వాలెట్‌లో 50 పాయింట్లు నిర్ధారించబడ్డాయి."
    assert sent["tok-citizen-2"][0] == "20 points are confirmed in your wallet."
    assert sent["tok-citizen-2"][1] == {"type": "WALLET", "event": "points_confirmed", "referral_id": str(referral.id)}


def test_not_confirmed_carries_the_reason_for_an_edited_text(pair, outbox, django_capture_on_commit_callbacks):
    referrer, friend = pair
    NotificationTemplate.objects.filter(key="points_not_confirmed").update(text_en="{points} points: {reason}")
    referral = services.start_referral(friend, referrer)
    Referral.objects.filter(pk=referral.pk).update(confirm_after=timezone.now() - timedelta(hours=1))
    with django_capture_on_commit_callbacks(execute=True):
        services.decide_due_referrals()
    sent = pushes(outbox)
    assert sent["tok-citizen-2"][0] == "20 points: Not confirmed: the new citizen did not use the app on enough days."
    assert sent["tok-citizen-1"][0] == "50 పాయింట్లు నిర్ధారించబడలేదు. కారణం మీ వాలెట్‌లో చూడండి."
    assert sent["tok-citizen-1"][1]["event"] == "points_not_confirmed"


def test_a_super_admins_rejection_sends_the_comment(pair, world, outbox, django_capture_on_commit_callbacks):
    referrer, friend = pair
    NotificationTemplate.objects.filter(key="points_not_confirmed").update(text_en="{reason}")
    referral = services.start_referral(friend, referrer)
    with django_capture_on_commit_callbacks(execute=True):
        services.decide(referral, confirm=False, reason_key="rejected", by=world.users["super"], comment="Same family")
    assert pushes(outbox)["tok-citizen-2"][0] == "Same family"


def test_a_deleted_or_blocked_account_hears_nothing(pair, world, outbox, django_capture_on_commit_callbacks):
    referrer, friend = pair
    referral = services.start_referral(friend, referrer)
    with django_capture_on_commit_callbacks(execute=True):
        accounts.delete_account(friend, via="app")
    sent = pushes(outbox)
    assert list(sent) == ["tok-citizen-1"] and sent["tok-citizen-1"][1]["event"] == "points_not_confirmed"
    outbox.clear()

    blocked = make_citizen(world, 3)
    User.objects.filter(pk=blocked.user_id).update(is_active=False)
    other = services.start_referral(make_citizen(world, 4), blocked)
    with django_capture_on_commit_callbacks(execute=True):
        services.decide(other, confirm=False, reason_key="blocked")
    assert list(pushes(outbox)) == ["tok-citizen-4"]
    assert referral.pk  # the first referral was decided by the deletion


def test_one_phone_family_only_the_welcome_bonus_is_told(world, settings, outbox, django_capture_on_commit_callbacks):
    settings.WALLET_ENABLED = True
    referrer, friend = make_citizen(world, 1, phone="family"), make_citizen(world, 2, phone="family")
    referral = services.start_referral(friend, referrer)  # the reward is CANCELLED at once (same_phone)
    with django_capture_on_commit_callbacks(execute=True):
        services.decide(referral, confirm=True)
    assert list(pushes(outbox)) == ["tok-citizen-2"]


def test_the_texts_are_seeded_and_editable(admin):
    keys = set(NotificationTemplate.objects.filter(recipient="wallet").values_list("key", flat=True))
    assert keys == {"referral_joined", "points_confirmed", "points_not_confirmed"}
    bad = admin.patch("/api/v1/admin/notification-templates/referral_joined/",
                      {"text_te": "{points}", "text_en": "{points}"}, format="json")
    assert err(bad) == "validation_error"
    good = admin.patch("/api/v1/admin/notification-templates/referral_joined/",
                       {"text_te": "{name} చేరారు", "text_en": "{name} joined"}, format="json")
    assert good.status_code == 200
