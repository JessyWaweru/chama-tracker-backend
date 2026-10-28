import json
from datetime import date, timedelta
from unittest import mock

import pytest

from chamas.models import Contribution
from payments.providers import paystack

from .conftest import FakeResponse

WEBHOOK = "/api/pay/paystack/"

CHARGE_PAY_OFFLINE = FakeResponse(
    200,
    {
        "status": True,
        "message": "Charge attempted",
        "data": {
            "reference": "CH-1-abcd1234",
            "status": "pay_offline",
            "display_text": "Please complete authorization process on your mobile number",
        },
    },
)


@pytest.fixture
def pending(chair):
    return Contribution.objects.create(
        member=chair, amount=1000, period=date(2026, 10, 1), method="mpesa", status="pending",
        provider="paystack", provider_ref="CH-1-abcd1234",
    )


def send_webhook(client, event, secret="sk_test_dummy", signature=None):
    raw = json.dumps(event).encode()
    sig = signature if signature is not None else paystack.signature(raw, secret)
    return client.post(WEBHOOK, raw, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)


def success_event(reference="CH-1-abcd1234", receipt="TJS1ABC2DE"):
    return {
        "event": "charge.success",
        "data": {
            "reference": reference,
            "status": "success",
            "amount": 100000,
            "currency": "KES",
            "channel": "mobile_money",
            "gateway_response": "Approved",
            "receipt_number": receipt,
        },
    }


def failed_event(reference="CH-1-abcd1234"):
    return {
        "event": "charge.failed",
        "data": {"reference": reference, "status": "failed", "gateway_response": "Transaction was cancelled by user"},
    }


# --- Charge -----------------------------------------------------------------


def test_stk_endpoint_charges_through_paystack(chama, chair, client_for):
    with mock.patch.object(paystack.requests, "request", return_value=CHARGE_PAY_OFFLINE) as call:
        resp = client_for(chair.user).post(
            f"/api/chamas/{chama.pk}/contributions/stk/",
            {"member_id": chair.pk, "amount": 1000, "period": "2026-10"},
            format="json",
        )
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert body["status"] == "pending"
    assert body["provider"] == "paystack"
    assert body["provider_ref"] == "CH-1-abcd1234"
    assert body["display_text"] == "Please complete authorization process on your mobile number"

    method, url = call.call_args.args
    assert (method, url) == ("post", "https://api.paystack.co/charge")
    assert call.call_args.kwargs["headers"] == {"Authorization": "Bearer sk_test_dummy"}
    payload = call.call_args.kwargs["json"]
    contribution = Contribution.objects.get()
    assert payload["amount"] == 100000  # KES 1,000 in cents
    assert payload["currency"] == "KES"
    assert payload["mobile_money"] == {"phone": "+254700000101", "provider": "mpesa"}
    assert payload["email"] == "254700000101@members.umoja.example.com"
    assert payload["reference"].startswith(f"CH-{contribution.pk}-")
    assert payload["metadata"] == {"chama_id": chama.pk, "member_id": chair.pk, "period": "2026-10-01"}


def test_references_are_unique():
    assert paystack.new_reference(7) != paystack.new_reference(7)


def test_charge_rejected_marks_failed_and_returns_400(chama, chair, client_for):
    rejected = FakeResponse(400, {"status": False, "message": "Invalid phone number"})
    with mock.patch.object(paystack.requests, "request", return_value=rejected):
        resp = client_for(chair.user).post(
            f"/api/chamas/{chama.pk}/contributions/stk/", {"member_id": chair.pk, "amount": 1000}, format="json"
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid phone number"
    assert Contribution.objects.get().status == "failed"


def test_declined_charge_reports_the_real_reason(chama, chair, client_for):
    declined = FakeResponse(400, {"status": False, "message": "Charge attempted", "data": {
        "reference": "CH-x", "status": "failed",
        "message": "Declined. Please use the test mobile money number since you are doing a test transaction."}})
    with mock.patch.object(paystack.requests, "request", return_value=declined):
        resp = client_for(chair.user).post(
            f"/api/chamas/{chama.pk}/contributions/stk/", {"member_id": chair.pk, "amount": 1000}, format="json"
        )
    assert resp.status_code == 400
    assert resp.json()["detail"].startswith("Declined. Please use the test mobile money number")


def test_immediate_success_is_confirmed(chama, chair, client_for):
    ok = FakeResponse(200, {"status": True, "message": "Charge attempted", "data": {
        "reference": "CH-y", "status": "success", "receipt_number": "10101", "gateway_response": "Approved"}})
    with mock.patch.object(paystack.requests, "request", return_value=ok):
        resp = client_for(chair.user).post(
            f"/api/chamas/{chama.pk}/contributions/stk/", {"member_id": chair.pk, "amount": 1000}, format="json"
        )
    assert resp.status_code == 201
    assert (resp.json()["status"], resp.json()["receipt"]) == ("confirmed", "10101")


def test_charge_failed_status_returns_400(chama, chair, client_for):
    failed = FakeResponse(200, {"status": True, "message": "Charge attempted",
                                "data": {"reference": "CH-x", "status": "failed", "gateway_response": "Declined"}})
    with mock.patch.object(paystack.requests, "request", return_value=failed):
        resp = client_for(chair.user).post(
            f"/api/chamas/{chama.pk}/contributions/stk/", {"member_id": chair.pk, "amount": 1000}, format="json"
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Declined"


# --- Status check -------------------------------------------------------------


@pytest.mark.parametrize(
    "verify_status, expected", [("success", "confirmed"), ("abandoned", "failed"), ("ongoing", "pending")]
)
def test_status_verifies_with_paystack_after_20s(pending, chair, client_for, verify_status, expected):
    Contribution.objects.filter(pk=pending.pk).update(created_at=pending.created_at - timedelta(seconds=25))
    verified = FakeResponse(200, {"status": True, "message": "Verification successful",
                                  "data": {"status": verify_status, "reference": pending.provider_ref,
                                           "gateway_response": "Approved", "receipt_number": "TJS1ABC2DE"}})
    with mock.patch.object(paystack.requests, "request", return_value=verified) as call:
        body = client_for(chair.user).get(f"/api/contributions/{pending.pk}/status/").json()
    assert call.call_args.args == ("get", "https://api.paystack.co/transaction/verify/CH-1-abcd1234")
    assert body["status"] == expected
    if expected == "confirmed":
        assert body["receipt"] == "TJS1ABC2DE"


def test_fresh_payment_is_not_verified_yet(pending, chair, client_for):
    with mock.patch.object(paystack.requests, "request") as call:
        body = client_for(chair.user).get(f"/api/contributions/{pending.pk}/status/").json()
    assert call.call_count == 0
    assert body["status"] == "pending"


# --- Webhook -----------------------------------------------------------------


def test_valid_signature_success_confirms(client, pending):
    resp = send_webhook(client, success_event())
    assert resp.status_code == 200
    pending.refresh_from_db()
    assert pending.status == "confirmed"
    assert pending.receipt == "TJS1ABC2DE"
    assert pending.confirmed_at is not None


def test_invalid_signature_is_rejected(client, pending):
    assert send_webhook(client, success_event(), secret="sk_test_wrong").status_code == 401
    assert send_webhook(client, success_event(), signature="").status_code == 401
    pending.refresh_from_db()
    assert pending.status == "pending"


def test_tampered_body_is_rejected(client, pending):
    raw = json.dumps(failed_event()).encode()
    sig = paystack.signature(raw, "sk_test_dummy")
    tampered = json.dumps(success_event()).encode()
    resp = client.post(WEBHOOK, tampered, content_type="application/json", HTTP_X_PAYSTACK_SIGNATURE=sig)
    assert resp.status_code == 401
    pending.refresh_from_db()
    assert pending.status == "pending"


def test_charge_failed_marks_failed(client, pending):
    assert send_webhook(client, failed_event()).status_code == 200
    pending.refresh_from_db()
    assert pending.status == "failed"
    assert pending.result_desc == "Transaction was cancelled by user"
    assert pending.receipt is None


def test_duplicate_webhook_is_a_no_op(client, pending):
    send_webhook(client, success_event())
    pending.refresh_from_db()
    first = (pending.status, pending.receipt, pending.confirmed_at, pending.result_desc)

    assert send_webhook(client, success_event()).status_code == 200
    assert send_webhook(client, failed_event()).status_code == 200
    pending.refresh_from_db()
    assert (pending.status, pending.receipt, pending.confirmed_at, pending.result_desc) == first


def test_other_events_and_unknown_references_are_acknowledged(client, pending):
    assert send_webhook(client, {"event": "transfer.success", "data": {"reference": "x"}}).status_code == 200
    assert send_webhook(client, success_event(reference="CH-999-unknown")).status_code == 200
    pending.refresh_from_db()
    assert pending.status == "pending"


def test_webhook_does_not_touch_daraja_rows(client, pending):
    Contribution.objects.filter(pk=pending.pk).update(provider="daraja")
    send_webhook(client, success_event())
    pending.refresh_from_db()
    assert pending.status == "pending"


def test_reused_receipt_still_confirms(client, chair, pending):
    Contribution.objects.create(member=chair, amount=1000, period=date(2026, 9, 1), method="mpesa",
                                status="confirmed", provider="paystack", receipt="10101")
    send_webhook(client, success_event(receipt="10101"))
    pending.refresh_from_db()
    assert pending.status == "confirmed"
    assert pending.receipt is None
