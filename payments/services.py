import logging

from django.db import transaction
from django.utils import timezone

from chamas.models import Contribution

from .providers import get_provider

logger = logging.getLogger(__name__)

FINAL = {Contribution.Status.CONFIRMED, Contribution.Status.FAILED}


@transaction.atomic
def apply_result(*, provider, provider_ref, status, receipt=None, reason=""):
    """Apply a payment outcome to its contribution, exactly once.

    Only a contribution that is still pending changes; repeats (duplicate webhooks, a
    webhook racing a status check) are no-ops. A `pending` status changes nothing.
    Returns the contribution, or None when nothing matches the reference.
    """
    contribution = (
        Contribution.objects.select_for_update()
        .filter(provider=provider, provider_ref=provider_ref)
        .first()
    )
    if contribution is None:
        logger.warning("No %s contribution for reference %s", provider, provider_ref)
        return None
    if status not in FINAL:
        return contribution

    if contribution.status != Contribution.Status.PENDING:
        # A status check can confirm a payment before its webhook lands without a receipt
        # number (Daraja's query has none), so let the webhook fill it in, nothing else.
        if (
            contribution.status == Contribution.Status.CONFIRMED
            and status == Contribution.Status.CONFIRMED
            and receipt
            and not contribution.receipt
            and not Contribution.objects.filter(receipt=receipt).exists()
        ):
            contribution.receipt = receipt
            contribution.save(update_fields=["receipt"])
        else:
            logger.info("Ignoring repeat result for contribution %s (%s)", contribution.pk, contribution.status)
        return contribution

    contribution.status = status
    contribution.result_desc = (reason or "")[:255]
    if receipt and Contribution.objects.filter(receipt=receipt).exclude(pk=contribution.pk).exists():
        # Receipts are unique, but Paystack test mode reuses the same one; never let
        # that stop a payment from being confirmed.
        logger.warning("Receipt %s already used; not stored on contribution %s", receipt, contribution.pk)
        receipt = None
    if status == Contribution.Status.CONFIRMED:
        contribution.receipt = receipt or None
        contribution.confirmed_at = timezone.now()
    contribution.save(update_fields=["status", "result_desc", "receipt", "confirmed_at"])
    return contribution


def refresh_status(contribution):
    """Ask the contribution's provider for its outcome and apply it if final."""
    status, receipt, reason = get_provider(contribution.provider).check_status(contribution.provider_ref)
    return (
        apply_result(
            provider=contribution.provider,
            provider_ref=contribution.provider_ref,
            status=status,
            receipt=receipt,
            reason=reason,
        )
        or contribution
    )
