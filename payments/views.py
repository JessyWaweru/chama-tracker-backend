import logging

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .providers import WebhookRejected
from .providers.daraja import DarajaProvider
from .providers.paystack import PaystackProvider
from .services import apply_result

logger = logging.getLogger(__name__)

DARAJA_ACCEPTED = {"ResultCode": 0, "ResultDesc": "Accepted"}


def _handle(provider, request):
    """Authenticate, parse and apply a webhook. Returns False if authentication failed."""
    try:
        parsed = provider.parse_webhook(request)
    except WebhookRejected as exc:
        logger.warning("Rejected %s webhook: %s", provider.name, exc)
        return False
    if parsed:
        provider_ref, status, receipt, reason = parsed
        apply_result(provider=provider.name, provider_ref=provider_ref, status=status, receipt=receipt, reason=reason)
    return True


@csrf_exempt
@require_POST
def daraja_callback(request, token):
    """Daraja STK callback. Always acknowledged once the URL token checks out."""
    if not _handle(DarajaProvider(), request):
        return JsonResponse({"detail": "Forbidden"}, status=403)
    return JsonResponse(DARAJA_ACCEPTED)


@csrf_exempt
@require_POST
def paystack_webhook(request):
    """Paystack events, authenticated by the x-paystack-signature header."""
    if not _handle(PaystackProvider(), request):
        return JsonResponse({"detail": "Invalid signature"}, status=401)
    return JsonResponse({"status": "ok"})
