"""Safaricom Daraja STK push (Lipa Na M-Pesa Online), for chamas with their own paybill or till."""
import base64
import hmac
import json
import logging
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from django.conf import settings

from .base import CONFIRMED, FAILED, PENDING, PaymentError, PaymentProvider, PaymentRequest, WebhookRejected

logger = logging.getLogger(__name__)

NAIROBI = ZoneInfo("Africa/Nairobi")
BASE_URLS = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
}
TRANSACTION_DESC_MAX = 13
ACCOUNT_REFERENCE_MAX = 12
# stk_query answers 4999 while the push is still being processed.
STILL_PROCESSING = {4999}

_token_lock = threading.Lock()
_token_cache = {"token": None, "expires_at": 0.0}


class MpesaError(PaymentError):
    """Daraja rejected the request, or could not be reached."""


def base_url():
    return BASE_URLS.get(settings.MPESA_ENV, BASE_URLS["sandbox"])


def clear_token_cache():
    with _token_lock:
        _token_cache.update(token=None, expires_at=0.0)


def get_access_token():
    """OAuth token, cached until shortly before Daraja says it expires."""
    with _token_lock:
        if _token_cache["token"] and time.monotonic() < _token_cache["expires_at"]:
            return _token_cache["token"]

        if not (settings.MPESA_CONSUMER_KEY and settings.MPESA_CONSUMER_SECRET):
            raise MpesaError("Daraja is not configured: set MPESA_CONSUMER_KEY and MPESA_CONSUMER_SECRET")
        try:
            resp = requests.get(
                f"{base_url()}/oauth/v1/generate",
                params={"grant_type": "client_credentials"},
                auth=(settings.MPESA_CONSUMER_KEY, settings.MPESA_CONSUMER_SECRET),
                timeout=settings.PAYMENT_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            raise MpesaError(f"Could not reach Daraja for a token: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise MpesaError(f"Daraja token request failed ({resp.status_code}): {_error_text(resp)}")

        data = resp.json()
        expires_in = int(data.get("expires_in", 3599))
        _token_cache["token"] = data["access_token"]
        # Refresh a minute early so a token never expires mid-request.
        _token_cache["expires_at"] = time.monotonic() + max(expires_in - 60, 0)
        return _token_cache["token"]


def timestamp(now=None):
    """YYYYMMDDHHMMSS in Nairobi time, as Daraja expects."""
    now = now or datetime.now(tz=NAIROBI)
    return now.astimezone(NAIROBI).strftime("%Y%m%d%H%M%S")


def password(ts, shortcode=None, passkey=None):
    shortcode = shortcode or settings.MPESA_SHORTCODE
    passkey = passkey or settings.MPESA_PASSKEY
    return base64.b64encode(f"{shortcode}{passkey}{ts}".encode()).decode()


def callback_url():
    # No "mpesa" in the path: Safaricom is widely reported to drop callbacks to such URLs.
    return f"{settings.MPESA_CALLBACK_BASE}/api/pay/hook/{settings.MPESA_CALLBACK_TOKEN}/"


def stk_push(*, phone, amount, account_reference, transaction_desc):
    """Send an STK push. Returns Daraja's JSON (MerchantRequestID, CheckoutRequestID, ...)."""
    ts = timestamp()
    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": password(ts),
        "Timestamp": ts,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": int(amount),
        "PartyA": phone,
        "PartyB": settings.MPESA_SHORTCODE,
        "PhoneNumber": phone,
        "CallBackURL": callback_url(),
        "AccountReference": account_reference[:ACCOUNT_REFERENCE_MAX],
        "TransactionDesc": transaction_desc[:TRANSACTION_DESC_MAX],
    }
    data = _post("/mpesa/stkpush/v1/processrequest", payload)
    if str(data.get("ResponseCode")) != "0" or not data.get("CheckoutRequestID"):
        raise MpesaError(data.get("ResponseDescription") or data.get("errorMessage") or "STK push rejected")
    return data


def stk_query(checkout_request_id):
    """Ask Daraja for the outcome of an STK push.

    While the customer is still deciding Daraja answers with an error
    ("The transaction is being processed"), which is raised as MpesaError.
    """
    ts = timestamp()
    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": password(ts),
        "Timestamp": ts,
        "CheckoutRequestID": checkout_request_id,
    }
    return _post("/mpesa/stkpushquery/v1/query", payload)


def _post(path, payload):
    token = get_access_token()
    try:
        resp = requests.post(
            f"{base_url()}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=settings.PAYMENT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise MpesaError(f"Could not reach Daraja: {type(exc).__name__}") from exc

    if resp.status_code == 401:
        clear_token_cache()
    if resp.status_code != 200:
        message = _error_text(resp)
        logger.warning("Daraja %s returned %s: %s", path, resp.status_code, message)
        raise MpesaError(message)
    return resp.json()


def _error_text(resp):
    try:
        data = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}"
    return data.get("errorMessage") or data.get("ResponseDescription") or f"HTTP {resp.status_code}"


def _outcome(result_code):
    return CONFIRMED if int(result_code) == 0 else FAILED


class DarajaProvider(PaymentProvider):
    name = "daraja"

    def request_payment(self, contribution, phone):
        data = stk_push(
            phone=phone,
            amount=contribution.amount,
            account_reference=contribution.member.chama.short_code,
            transaction_desc=f"Contrib {contribution.period:%b%y}",
        )
        return PaymentRequest(provider_ref=data["CheckoutRequestID"], display_text=data.get("CustomerMessage", ""))

    def check_status(self, provider_ref):
        try:
            data = stk_query(provider_ref)
        except MpesaError as exc:
            return PENDING, None, str(exc)
        try:
            code = int(data.get("ResultCode"))
        except (TypeError, ValueError):
            return PENDING, None, data.get("ResponseDescription", "")
        if code in STILL_PROCESSING:
            return PENDING, None, data.get("ResultDesc", "")
        # The query response carries no MpesaReceiptNumber; the callback adds it if it arrives.
        return _outcome(code), None, data.get("ResultDesc", "")

    def parse_webhook(self, request):
        token = (request.resolver_match.kwargs if request.resolver_match else {}).get("token", "")
        if not hmac.compare_digest(str(token), str(settings.MPESA_CALLBACK_TOKEN)):
            raise WebhookRejected("Bad callback token")
        try:
            callback = json.loads(request.body)["Body"]["stkCallback"]
            checkout_request_id = callback["CheckoutRequestID"]
            result_code = int(callback["ResultCode"])
        except (ValueError, KeyError, TypeError):
            logger.warning("Unparseable Daraja callback")
            return None

        items = (callback.get("CallbackMetadata") or {}).get("Item") or []
        metadata = {item.get("Name"): item.get("Value") for item in items if isinstance(item, dict)}
        return checkout_request_id, _outcome(result_code), metadata.get("MpesaReceiptNumber"), callback.get(
            "ResultDesc", ""
        )
