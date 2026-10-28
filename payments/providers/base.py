"""The interface every payment provider implements.

Providers speak in three statuses, matching Contribution.Status: pending, confirmed, failed.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Tuple

PENDING = "pending"
CONFIRMED = "confirmed"
FAILED = "failed"

# (status, receipt, reason)
StatusResult = Tuple[str, Optional[str], str]
# (provider_ref, status, receipt, reason)
WebhookResult = Tuple[str, str, Optional[str], str]


class PaymentError(Exception):
    """The provider rejected the request, or could not be reached."""


class WebhookRejected(Exception):
    """A webhook failed authentication (bad token or signature)."""


@dataclass(frozen=True)
class PaymentRequest:
    """What request_payment hands back.

    provider_ref is the id used to match later status checks and webhooks. display_text
    is any instruction for the payer ("Check your phone ..."). status is normally pending,
    but a provider may report a final outcome straight away.
    """

    provider_ref: str
    display_text: str = ""
    status: str = PENDING
    receipt: Optional[str] = None
    reason: str = ""


class PaymentProvider(ABC):
    name: str

    @abstractmethod
    def request_payment(self, contribution, phone: str) -> PaymentRequest:
        """Prompt `phone` (2547XXXXXXXX) to pay for `contribution`. Raises PaymentError."""

    @abstractmethod
    def check_status(self, provider_ref: str) -> StatusResult:
        """Ask the provider for the outcome. Returns pending when it isn't final yet."""

    @abstractmethod
    def parse_webhook(self, request) -> Optional[WebhookResult]:
        """Authenticate and parse a webhook.

        Raises WebhookRejected when authentication fails. Returns None for events that
        carry no payment outcome (or can't be parsed), which the caller acknowledges and ignores.
        """
