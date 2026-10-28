from datetime import date

import pytest
import requests
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from chamas.models import Chama, Member
from payments.providers import daraja


@pytest.fixture(autouse=True)
def no_real_http(monkeypatch):
    """Every Daraja call must be mocked; fail loudly if one slips through."""

    def blocked(*args, **kwargs):
        raise AssertionError(f"Unmocked HTTP call: {args} {kwargs.get('url', '')}")

    monkeypatch.setattr(requests.Session, "request", blocked)


@pytest.fixture(autouse=True)
def payment_settings(settings):
    settings.PAYMENT_PROVIDER = "paystack"
    settings.PAYSTACK_SECRET_KEY = "sk_test_dummy"
    settings.PAYSTACK_BASE_URL = "https://api.paystack.co"
    settings.PAYSTACK_MEMBER_EMAIL_DOMAIN = "example.com"
    settings.MPESA_ENV = "sandbox"
    settings.MPESA_CONSUMER_KEY = "test-key"
    settings.MPESA_CONSUMER_SECRET = "test-secret"
    settings.MPESA_SHORTCODE = "174379"
    settings.MPESA_PASSKEY = "test-passkey"
    settings.MPESA_CALLBACK_BASE = "https://example.com"
    settings.MPESA_CALLBACK_TOKEN = "hook-secret"
    daraja.clear_token_cache()
    yield settings
    daraja.clear_token_cache()


@pytest.fixture
def make_user(db):
    def _make(username):
        return get_user_model().objects.create_user(username=username, password="pass12345")

    return _make


@pytest.fixture
def chama(make_user):
    chair = make_user("chair")
    chama = Chama.objects.create(
        name="Umoja", short_code="UMOJA", contribution_amount=1000, frequency="monthly", created_by=chair
    )
    Member.objects.create(chama=chama, user=chair, full_name="Grace", phone="0700000101", role="chair",
                          joined_on=date(2026, 1, 1))
    return chama


@pytest.fixture
def treasurer(chama, make_user):
    user = make_user("treasurer")
    Member.objects.create(chama=chama, user=user, full_name="Mary", phone="0700000102", role="treasurer",
                          joined_on=date(2026, 1, 1))
    return user


@pytest.fixture
def plain_member(chama, make_user):
    user = make_user("member")
    member = Member.objects.create(chama=chama, user=user, full_name="Esther", phone="0700000104",
                                   role="member", joined_on=date(2026, 1, 1))
    return user, member


@pytest.fixture
def client_for():
    def _client(user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    return _client


class FakeResponse:
    def __init__(self, status_code, data):
        self.status_code = status_code
        self._data = data
        self.text = str(data)

    def json(self):
        return self._data


@pytest.fixture
def chair(chama):
    return chama.members.get(role="chair")
