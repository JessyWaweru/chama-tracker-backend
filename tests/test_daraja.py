import base64
from datetime import date, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from chamas.models import Contribution
from payments.providers import daraja
from payments.services import apply_result

from .conftest import FakeResponse

HOOK = "/api/pay/hook/hook-secret/"
TOKEN = FakeResponse(200, {"access_token": "tok-123", "expires_in": "3599"})
ACCEPTED = FakeResponse(
    200,
    {
        "MerchantRequestID": "29115-34620561-1",
        "CheckoutRequestID": "ws_CO_28102026120000001",
        "ResponseCode": "0",
        "ResponseDescription": "Success. Request accepted for processing",
        "CustomerMessage": "Success. Request accepted for processing",
    },
)


@pytest.fixture(autouse=True)
def use_daraja(settings):
    settings.PAYMENT_PROVIDER = "daraja"


@pytest.fixture
def http():
    with mock.patch.object(daraja.requests, "get", return_value=TOKEN) as get, mock.patch.object(
        daraja.requests, "post", return_value=ACCEPTED
    ) as post:
        yield get, post


@pytest.fixture
def pending(chair):
    return Contribution.objects.create(
        member=chair, amount=1000, period=date(2026, 10, 1), method="mpesa", status="pending",
        provider="daraja", provider_ref="ws_CO_28102026120000001",
    )


def stk(client, chama, member):
    return client.post(
        f"/api/chamas/{chama.pk}/contributions/stk/",
        {"member_id": member.pk, "amount": 1000, "period": "2026-10"},
        format="json",
    )


def callback(checkout_id, code=0, receipt="TJS1ABC2DE", desc=None):
    body = {"MerchantRequestID": "29115-34620561-1", "CheckoutRequestID": checkout_id, "ResultCode": code,
            "ResultDesc": desc or ("The service request is processed successfully." if code == 0 else "Request cancelled by user")}
    if code == 0:
        body["CallbackMetadata"] = {"Item": [
            {"Name": "Amount", "Value": 1000},
            {"Name": "MpesaReceiptNumber", "Value": receipt},
            {"Name": "TransactionDate", "Value": 20261028120102},
            {"Name": "PhoneNumber", "Value": 254700000101},
        ]}
    return {"Body": {"stkCallback": body}}


# --- Client -------------------------------------------------------------------


def test_password_and_timestamp():
    ts = daraja.timestamp(datetime(2026, 10, 28, 9, 30, 5, tzinfo=ZoneInfo("UTC")))
    assert ts == "20261028123005"  # Nairobi is UTC+3
    assert base64.b64decode(daraja.password(ts, "174379", "pk")).decode() == f"174379pk{ts}"


def test_token_is_cached(http):
    get, _ = http
    assert daraja.get_access_token() == daraja.get_access_token() == "tok-123"
    assert get.call_count == 1


def test_callback_url_has_no_mpesa_in_it():
    assert daraja.callback_url() == "https://example.com/api/pay/hook/hook-secret/"
    assert "mpesa" not in daraja.callback_url().lower()


# --- STK push -----------------------------------------------------------------


def test_member_pays_for_themselves(chama, plain_member, client_for, http):
    user, member = plain_member
    _, post = http
    resp = stk(client_for(user), chama, member)
    assert resp.status_code == 201, resp.json()
    body = resp.json()
    assert (body["status"], body["provider"], body["provider_ref"]) == ("pending", "daraja", "ws_CO_28102026120000001")
    assert body["display_text"] == "Success. Request accepted for processing"

    payload = post.call_args.kwargs["json"]
    assert payload["PhoneNumber"] == payload["PartyA"] == "254700000104"
    assert payload["AccountReference"] == "UMOJA"
    assert len(payload["TransactionDesc"]) <= 13
    assert payload["CallBackURL"] == "https://example.com/api/pay/hook/hook-secret/"
    assert payload["BusinessShortCode"] == payload["PartyB"] == "174379"
    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer tok-123"}


def test_member_cannot_push_for_someone_else(chama, chair, plain_member, client_for, http):
    user, _ = plain_member
    assert stk(client_for(user), chama, chair).status_code == 403
    assert not Contribution.objects.exists()


def test_daraja_rejection_marks_failed(chama, chair, client_for):
    rejected = FakeResponse(400, {"requestId": "x", "errorCode": "400.002.02", "errorMessage": "Bad Request - Invalid Amount"})
    with mock.patch.object(daraja.requests, "get", return_value=TOKEN), mock.patch.object(
        daraja.requests, "post", return_value=rejected
    ):
        resp = stk(client_for(chair.user), chama, chair)
    assert resp.status_code == 400
    assert resp.json()["detail"] == "Bad Request - Invalid Amount"
    contribution = Contribution.objects.get()
    assert (contribution.status, contribution.result_desc) == ("failed", "Bad Request - Invalid Amount")


def test_status_queries_daraja_after_20_seconds(pending, chair, client_for):
    client = client_for(chair.user)
    query_ok = FakeResponse(200, {"ResponseCode": "0", "ResultCode": "0",
                                  "ResultDesc": "The service request is processed successfully."})
    with mock.patch.object(daraja.requests, "get", return_value=TOKEN), mock.patch.object(
        daraja.requests, "post", return_value=query_ok
    ) as post:
        assert client.get(f"/api/contributions/{pending.pk}/status/").json()["status"] == "pending"
        assert post.call_count == 0  # too fresh to query

        Contribution.objects.filter(pk=pending.pk).update(created_at=pending.created_at - timedelta(seconds=25))
        body = client.get(f"/api/contributions/{pending.pk}/status/").json()
    assert post.call_args.kwargs["json"]["CheckoutRequestID"] == pending.provider_ref
    assert body["status"] == "confirmed"


def test_status_stays_pending_while_daraja_is_processing(pending, chair, client_for):
    Contribution.objects.filter(pk=pending.pk).update(created_at=pending.created_at - timedelta(seconds=30))
    busy = FakeResponse(500, {"errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"})
    with mock.patch.object(daraja.requests, "get", return_value=TOKEN), mock.patch.object(
        daraja.requests, "post", return_value=busy
    ):
        body = client_for(chair.user).get(f"/api/contributions/{pending.pk}/status/").json()
    assert body["status"] == "pending"


# --- Callback -----------------------------------------------------------------


def post_callback(client, body, url=HOOK):
    return client.post(url, body, content_type="application/json")


def test_callback_success_confirms_and_saves_receipt(client, pending):
    resp = post_callback(client, callback(pending.provider_ref))
    assert resp.json() == {"ResultCode": 0, "ResultDesc": "Accepted"}
    pending.refresh_from_db()
    assert (pending.status, pending.receipt) == ("confirmed", "TJS1ABC2DE")


def test_callback_1032_cancel_marks_failed(client, pending):
    resp = post_callback(client, callback(pending.provider_ref, code=1032))
    assert resp.json() == {"ResultCode": 0, "ResultDesc": "Accepted"}
    pending.refresh_from_db()
    assert (pending.status, pending.result_desc, pending.receipt) == ("failed", "Request cancelled by user", None)


def test_duplicate_callback_is_a_no_op(client, pending):
    post_callback(client, callback(pending.provider_ref))
    pending.refresh_from_db()
    first = (pending.status, pending.receipt, pending.confirmed_at)
    assert post_callback(client, callback(pending.provider_ref)).json()["ResultCode"] == 0
    assert post_callback(client, callback(pending.provider_ref, code=1032)).json()["ResultCode"] == 0
    pending.refresh_from_db()
    assert (pending.status, pending.receipt, pending.confirmed_at) == first


def test_wrong_token_is_forbidden(client, pending):
    assert post_callback(client, callback(pending.provider_ref), url="/api/pay/hook/wrong/").status_code == 403
    pending.refresh_from_db()
    assert pending.status == "pending"


def test_unknown_id_and_garbage_are_still_accepted(client, db):
    assert post_callback(client, callback("ws_CO_unknown")).json()["ResultCode"] == 0
    resp = client.post(HOOK, "not json", content_type="application/json")
    assert (resp.status_code, resp.json()["ResultCode"]) == (200, 0)


def test_late_callback_fills_receipt_after_query_confirmed(pending):
    apply_result(provider="daraja", provider_ref=pending.provider_ref, status="confirmed", reason="ok")
    apply_result(provider="daraja", provider_ref=pending.provider_ref, status="confirmed", receipt="TJS9ZZZ")
    pending.refresh_from_db()
    assert (pending.status, pending.receipt) == ("confirmed", "TJS9ZZZ")
