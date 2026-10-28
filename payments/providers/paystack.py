"""M-Pesa through Paystack's Charge API. The default provider: no paybill of your own needed."""
import hashlib
import hmac
import json
import logging
import secrets

import requests
from django.conf import settings

from .base import CONFIRMED, FAILED, PENDING, PaymentError, PaymentProvider, PaymentRequest, WebhookRejected

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "HTTP_X_PAYSTACK_SIGNATURE"
# Transaction statuses from /transaction/verify that are final.
VERIFY_CONFIRMED = {"success"}
VERIFY_FAILED = {"failed", "abandoned", "reversed"}


def member_email(phone, short_code):
    """Members have no email, but Paystack requires one.

    Paystack rejects a .local TLD, so the domain is a setting; the default example.com
    is reserved (RFC 2606) and Paystack's customer receipts can never reach anyone.
    """
    return f"{phone}@members.{short_code.lower()}.{settings.PAYSTACK_MEMBER_EMAIL_DOMAIN}"


def new_reference(contribution_id):
    return f"CH-{contribution_id}-{secrets.token_hex(4)}"


def signature(raw_body, secret=None):
    secret = secret or settings.PAYSTACK_SECRET_KEY
    return hmac.new(secret.encode(), raw_body, hashlib.sha512).hexdigest()


class PaystackProvider(PaymentProvider):
    name = "paystack"

    def request_payment(self, contribution, phone):
        chama = contribution.member.chama
        reference = new_reference(contribution.pk)
        payload = {
            "email": member_email(phone, chama.short_code),
            "amount": int(contribution.amount) * 100,  # KES in cents
            "currency": "KES",
            "mobile_money": {"phone": f"+{phone}", "provider": "mpesa"},
            "reference": reference,
            "metadata": {
                "chama_id": chama.pk,
                "member_id": contribution.member_id,
                "period": contribution.period.isoformat(),
            },
        }
        body = self._call("post", "/charge", json=payload)
        data = body.get("data") or {}
        status = data.get("status", "")
        display_text = data.get("display_text") or body.get("message", "")

        if status == "failed":
            raise PaymentError(data.get("gateway_response") or data.get("message") or "Charge failed")
        if status == "success":
            return PaymentRequest(
                provider_ref=data.get("reference", reference),
                display_text=display_text,
                status=CONFIRMED,
                receipt=data.get("receipt_number"),
                reason=data.get("gateway_response", ""),
            )
        # pay_offline (approve on phone), send_otp, pending, ...: not final yet.
        return PaymentRequest(provider_ref=data.get("reference", reference), display_text=display_text)

    def check_status(self, provider_ref):
        try:
            body = self._call("get", f"/transaction/verify/{provider_ref}")
        except PaymentError as exc:
            return PENDING, None, str(exc)
        data = body.get("data") or {}
        status = data.get("status", "")
        reason = data.get("gateway_response") or body.get("message", "")
        if status in VERIFY_CONFIRMED:
            return CONFIRMED, data.get("receipt_number"), reason
        if status in VERIFY_FAILED:
            return FAILED, None, reason
        return PENDING, None, reason

    def parse_webhook(self, request):
        raw = request.body
        sent = request.META.get(SIGNATURE_HEADER, "")
        if not settings.PAYSTACK_SECRET_KEY or not hmac.compare_digest(sent, signature(raw)):
            raise WebhookRejected("Bad Paystack signature")

        try:
            event = json.loads(raw)
            name = event["event"]
            data = event["data"]
            reference = data["reference"]
        except (ValueError, KeyError, TypeError):
            logger.warning("Unparseable Paystack webhook")
            return None

        if name == "charge.success":
            return reference, CONFIRMED, data.get("receipt_number"), data.get("gateway_response", "")
        if name == "charge.failed":
            return reference, FAILED, None, data.get("gateway_response") or data.get("message", "") or "Charge failed"
        logger.info("Ignoring Paystack event %s for %s", name, reference)
        return None

    def _call(self, method, path, **kwargs):
        if not settings.PAYSTACK_SECRET_KEY:
            raise PaymentError("Paystack is not configured: set PAYSTACK_SECRET_KEY")
        try:
            resp = requests.request(
                method,
                f"{settings.PAYSTACK_BASE_URL}{path}",
                headers={"Authorization": f"Bearer {settings.PAYSTACK_SECRET_KEY}"},
                timeout=settings.PAYMENT_TIMEOUT_SECONDS,
                **kwargs,
            )
        except requests.RequestException as exc:
            raise PaymentError(f"Could not reach Paystack: {type(exc).__name__}") from exc
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400 or body.get("status") is False:
            # On a declined charge the top-level message is just "Charge attempted";
            # the reason is in data.message / data.gateway_response.
            data = body.get("data") if isinstance(body.get("data"), dict) else {}
            message = (
                data.get("message")
                or data.get("gateway_response")
                or body.get("message")
                or f"Paystack returned HTTP {resp.status_code}"
            )
            logger.warning("Paystack %s %s failed (%s): %s", method.upper(), path, resp.status_code, message)
            raise PaymentError(message)
        return body
