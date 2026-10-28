from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from .base import PaymentError, PaymentProvider, PaymentRequest, WebhookRejected  # noqa: F401
from .daraja import DarajaProvider
from .paystack import PaystackProvider

PROVIDERS = {
    PaystackProvider.name: PaystackProvider,
    DarajaProvider.name: DarajaProvider,
}


def get_provider(name=None) -> PaymentProvider:
    """The named provider, or the one PAYMENT_PROVIDER selects."""
    name = name or settings.PAYMENT_PROVIDER
    try:
        return PROVIDERS[name]()
    except KeyError:
        raise ImproperlyConfigured(f"Unknown payment provider {name!r}; use one of {sorted(PROVIDERS)}")
