# chama-tracker

A REST API that lets a chama treasurer track members, contributions and arrears, and collect contributions over M-Pesa.

A *chama* is a Kenyan savings group. Members pay a fixed amount every week or month, and the treasurer tracks who has paid, who is behind and who has paid ahead. chama-tracker keeps that record and lets members pay by M-Pesa. It can use [Paystack](https://paystack.com) (the default) or Safaricom's [Daraja](https://developer.safaricom.co.ke) API.

![Python](https://img.shields.io/badge/python-3.12-blue) ![Django](https://img.shields.io/badge/django-5.2-green) ![DRF](https://img.shields.io/badge/DRF-3.18-red) ![Tests](https://img.shields.io/badge/tests-pytest-yellow)

## Contents

- [Features](#features)
- [Quick start](#quick-start)
- [Demo data](#demo-data)
- [API](#api)
- [Arrears rule](#arrears-rule)
- [Payment providers](#payment-providers)
- [Testing payments locally](#testing-payments-locally)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [Running the tests](#running-the-tests)
- [Deployment notes](#deployment-notes)
- [Known limitations](#known-limitations)

## Features

- **Chamas**: each has a short code (which doubles as the M-Pesa account reference), a fixed contribution in KES, and a monthly or weekly cycle. Whoever creates a chama becomes its chair.
- **Members** have one of four roles: chair, treasurer, secretary or member. Phone numbers are normalised to `2547XXXXXXXX` / `2541XXXXXXXX`. Members are deactivated rather than deleted, so their history is kept.
- **Contributions** can be paid by M-Pesa, cash or bank. Only `confirmed` contributions count as paid.
- **M-Pesa payment prompts** go through a swappable provider: Paystack or Daraja. Each provider has an authenticated webhook, plus a status-check fallback for when no webhook arrives.
- **Each result is applied exactly once.** It is applied only while the contribution is still `pending`, inside a transaction using `select_for_update`. Duplicate or late webhooks change nothing.
- **Arrears and credit** come from pure functions in `services/arrears.py`, which are easy to test and reason about.
- **Summary endpoint**: total collected all-time, the period's expected vs collected, and each member's paid, expected, arrears, credit and last payment date.
- **Access control**: you only see chamas you belong to. Officials manage members, and only the treasurer records cash or bank payments. Members can send a payment prompt only for themselves.
- **Admin and demo data**: the Django admin has filters for every model, and a `seed_demo` command loads six months of realistic history.

## Quick start

You need Python 3.12 or newer. These steps were tested on Ubuntu (WSL).

```bash
git clone git@github.com:JessyWaweru/chama-tracker.git
cd chama-tracker
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env              # then set DJANGO_SECRET_KEY and PAYSTACK_SECRET_KEY (see Configuration)
python manage.py migrate
python manage.py seed_demo        # demo user: demo / demo1234 (treasurer)
python manage.py createsuperuser  # optional, for /admin/
python manage.py runserver
```

Log in and read the summary:

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/token/ -H 'Content-Type: application/json' \
  -d '{"username":"demo","password":"demo1234"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["access"])')

curl -s "localhost:8000/api/chamas/1/summary/?period=2026-10" -H "Authorization: Bearer $TOKEN"
```

## Demo data

`python manage.py seed_demo` creates **Umoja Women Group** (short code `UMOJA`, KES 1,000 a month) with 12 members and six months of history, counted back from the current month. Running it again adds nothing.

| Member | Role | Situation |
| --- | --- | --- |
| Grace Wanjiru Kamau | chair | fully paid (cash) |
| Mary Achieng Otieno | treasurer | fully paid; linked to the `demo` login |
| Faith Muthoni Njoroge | secretary | fully paid |
| Esther Njeri Mwangi | member | **two months ahead** (credit 2,000) |
| Lucy Wambui Karanja | member | **missed two months** (arrears 2,000), plus one cancelled M-Pesa prompt |
| Agnes Akinyi Ouma | member | **missed one month** (arrears 1,000) |
| Mercy Nduta Gitau | member | **paid half one month** (arrears 500) |
| Ruth Jepkosgei Rotich | member | **deactivated** after three months |
| Caroline Mwikali Musyoka | member | joined three months ago; fully paid |
| Janet, Ann, Beatrice | member | fully paid (cash, bank, M-Pesa) |

All phone numbers in the demo are fake `254700000xxx` numbers.

## API

All endpoints except login and webhooks need an `Authorization: Bearer <access token>` header.

| Method | Path | Who can call it |
| --- | --- | --- |
| POST | `/api/auth/token/` · `/api/auth/token/refresh/` | anyone |
| GET, POST | `/api/chamas/` | lists your chamas; anyone can create one and becomes its chair |
| GET | `/api/chamas/{id}/` | members |
| GET, POST | `/api/chamas/{id}/members/` | members read; officials add |
| GET, PATCH, DELETE | `/api/chamas/{id}/members/{member_id}/` | officials edit; DELETE deactivates |
| GET | `/api/chamas/{id}/contributions/?member=&period=&status=` | members |
| POST | `/api/chamas/{id}/contributions/stk/` | officials, or a member paying for themselves |
| POST | `/api/chamas/{id}/contributions/cash/` | treasurer only |
| GET | `/api/contributions/{id}/status/` | members |
| GET | `/api/chamas/{id}/summary/?period=YYYY-MM` | members |
| POST | `/api/pay/paystack/` | Paystack (signed webhook) |
| POST | `/api/pay/hook/<MPESA_CALLBACK_TOKEN>/` | Daraja (callback) |

Someone who isn't a member of a chama gets **404**, not 403, for anything under it, so chama ids don't leak.

<details>
<summary>Request examples</summary>

Create a chama. You become its chair, and the short code is uppercased:

```json
POST /api/chamas/
{"name": "Tujenge", "short_code": "tujenge", "contribution_amount": 500, "frequency": "weekly", "chair_phone": "0722000000"}
```

Add a member (officials only). `username` is optional and links an existing login:

```json
POST /api/chamas/1/members/
{"full_name": "Janet Chebet", "phone": "+254 712 345 678", "role": "member", "joined_on": "2026-08-01"}
```

Prompt a phone to pay by M-Pesa. `period` defaults to the current period, and `phone` defaults to the member's own number:

```json
POST /api/chamas/1/contributions/stk/
{"member_id": 2, "amount": 1000, "period": "2026-10", "phone": "0710000000"}
```

The response is the contribution, plus the provider's `display_text` (for example, "Please complete authorization process on your mobile number"). If the provider rejects the request, you get a **400** with the reason, and the contribution is marked `failed`.

Record a cash or bank payment (treasurer only). It is confirmed immediately:

```json
POST /api/chamas/1/contributions/cash/
{"member_id": 5, "amount": 1000, "period": "2026-10", "method": "cash", "note": "Paid at the meeting"}
```

Check on a payment. While it is pending, this endpoint asks the provider once 20 seconds have passed:

```
GET /api/contributions/70/status/
```

</details>

<details>
<summary>Summary response (abridged)</summary>

```json
{
  "chama": {"id": 1, "name": "Umoja Women Group", "short_code": "UMOJA", "frequency": "monthly", "contribution_amount": 1000},
  "period": "2026-10-01",
  "total_collected_all_time": 64500,
  "period_expected": 11000,
  "period_collected": 10000,
  "period_outstanding": 1000,
  "members_in_arrears": 3,
  "total_arrears": 3500,
  "total_credit": 2000,
  "members": [
    {"member_id": 6, "full_name": "Lucy Wambui Karanja", "role": "member", "is_active": true,
     "periods_due": 6, "expected": 6000, "paid": 4000, "arrears": 2000, "credit": 0,
     "period_paid": 0, "last_payment_date": "2026-09-08"}
  ]
}
```

</details>

## Arrears rule

The rule is in [`services/arrears.py`](services/arrears.py). It is written as pure functions: dates and integers in, values out, with no database access.

- A **period** is a calendar month, stored as its 1st, or a week, stored as its Monday. A payment's period is moved to the start of its month or week.
- **expected** = `contribution_amount` × the number of periods from the member's `joined_on` period to the current period, counting both ends. Someone who joins on the 19th still owes that whole month.
- **paid** = the sum of the member's **confirmed** contributions, for any period. Pending and failed contributions never count.
- **arrears** = `max(expected − paid, 0)` and **credit** = `max(paid − expected, 0)`. Paying ahead shows up as credit.
- **Deactivated members** are not expected to pay, so their arrears and credit are 0. What they paid still counts towards the chama's totals.

The summary's `?period=YYYY-MM` shows balances as they stood at the end of that period. It counts only contributions confirmed before the next period started. Weekly chamas can pass `YYYY-MM-DD` for any day in the week. If you leave the period out, the current period is used.

## Payment providers

Choose the provider with `PAYMENT_PROVIDER` in `.env`. Both providers implement one interface, in [`payments/providers/base.py`](payments/providers/base.py):

```python
request_payment(contribution, phone) -> PaymentRequest      # provider_ref, display_text, status
check_status(provider_ref)           -> (status, receipt, reason)
parse_webhook(request)               -> (provider_ref, status, receipt, reason) | None
```

The rest of the app talks only to this interface. Each contribution records which provider handled it, so status checks and webhooks always go to the right one, even after you switch providers.

```mermaid
sequenceDiagram
    participant App as Client app
    participant API as chama-tracker
    participant P as Paystack / Daraja
    participant Phone as Member's phone
    App->>API: POST /contributions/stk/
    API->>API: create contribution (pending)
    API->>P: request_payment
    P-->>API: provider_ref, display_text
    API-->>App: 201 pending + display_text
    P->>Phone: M-Pesa prompt
    Phone->>P: enters PIN
    P->>API: webhook (signed / tokened)
    API->>API: apply once while pending → confirmed / failed
    App->>API: GET /contributions/{id}/status/
    Note over API,P: if still pending after 20s → check_status
```

### Paystack (default)

Paystack is the fastest way to go live: a chama needs only a Paystack account, not its own paybill or Safaricom's go-live approval.

- **Charge**: `POST https://api.paystack.co/charge` with `mobile_money: {phone: "+2547…", provider: "mpesa"}`, the amount in cents (KES × 100), and a unique reference `CH-<contribution id>-<random>`. The request also sends metadata: `chama_id`, `member_id` and `period`.
- **Email**: Paystack requires an email address, but members don't have one. The app sends `<phone>@members.<short_code>.example.com`. Paystack rejects a `.local` domain. `example.com` is reserved, so no receipt email ever reaches anyone. Set `PAYSTACK_MEMBER_EMAIL_DOMAIN` to a domain you own if you want those receipts.
- **Charge statuses**: `/charge` can reply with `pay_offline`, `send_otp` or similar. Its `display_text` is passed back to the client.
- **Status check**: `GET /transaction/verify/<reference>`.
- **Webhook**: `POST /api/pay/paystack/`. The `x-paystack-signature` header must match an HMAC-SHA512 of the raw request body, keyed with `PAYSTACK_SECRET_KEY` and compared in constant time. Otherwise the webhook gets a **401**. `charge.success` and `charge.failed` events are applied. Other events are acknowledged and ignored. Set the webhook URL in the Paystack dashboard under *Settings → API Keys & Webhooks*.

### Daraja

Use Daraja for chamas that have their own paybill or till.

- **STK push**: requests use an OAuth token cached until it expires, a timestamp in Nairobi time, and the password `base64(shortcode + passkey + timestamp)`. `AccountReference` is the chama's short code. `TransactionDesc` is at most 13 characters.
- **Callback**: `POST /api/pay/hook/<MPESA_CALLBACK_TOKEN>/`. The URL deliberately leaves out the word "mpesa", because Safaricom is widely reported to drop callbacks to such URLs. A wrong token gets a **403**. A valid callback always gets `{"ResultCode": 0, "ResultDesc": "Accepted"}`.
- **Status check**: `stk_query`. Its response carries no receipt number. When a payment is confirmed by query, its receipt is filled in only if the callback arrives later.

Secret keys and full webhook bodies are never logged.

## Testing payments locally

You don't need a public URL for either provider. Poll `GET /api/contributions/{id}/status/`. Once a payment has been pending for longer than `PAYMENT_STATUS_CHECK_AFTER_SECONDS` (20 by default), each status read asks the provider for the outcome and applies it.

- **Paystack**:
  - Put your `sk_test_…` key in `.env`.
  - Test mode accepts only its test number, **`0710000000`**. Charges to it succeed instantly. Real numbers, including Daraja's sandbox number, are declined with "Please use the test mobile money number".
  - To receive webhooks too, run `ngrok http 8000`, set the dashboard webhook URL to `https://<ngrok-host>/api/pay/paystack/`, and add that host to `DJANGO_ALLOWED_HOSTS`.
- **Daraja**:
  - Set `PAYMENT_PROVIDER=daraja` and add your Daraja app's consumer key and secret. The sandbox shortcode `174379` and the public sandbox passkey are already in `.env.example`.
  - Leave `MPESA_CALLBACK_BASE=https://example.com`. Daraja needs a valid HTTPS URL, but it doesn't have to reach you.
  - Test with `254708374149` or your own Safaricom number.

```bash
curl -s -X POST localhost:8000/api/chamas/1/contributions/stk/ -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"member_id": 2, "amount": 1000, "phone": "0710000000"}'

curl -s localhost:8000/api/contributions/<id>/status/ -H "Authorization: Bearer $TOKEN"
```

## Configuration

All settings are read from `.env`. Copy [`.env.example`](.env.example) to start. **Never commit `.env`.**

| Variable | Default | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | insecure dev key | Set a long random value |
| `DJANGO_DEBUG` | `True` | Set `False` in production |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1` | Comma-separated hostnames |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | — | Needed for the admin behind HTTPS |
| `DATABASE_URL` | SQLite `db.sqlite3` | e.g. `postgres://user:pass@host:5432/chama` |
| `JWT_ACCESS_MINUTES` / `JWT_REFRESH_DAYS` | `60` / `7` | How long tokens last |
| `PAYMENT_PROVIDER` | `paystack` | `paystack` or `daraja` |
| `PAYMENT_STATUS_CHECK_AFTER_SECONDS` | `20` | How long a payment waits before a status read asks the provider |
| `PAYSTACK_SECRET_KEY` | — | `sk_test_…` or `sk_live_…` |
| `PAYSTACK_MEMBER_EMAIL_DOMAIN` | `example.com` | Domain for the placeholder member emails |
| `MPESA_ENV` | `sandbox` | `sandbox` or `production` (Daraja) |
| `MPESA_CONSUMER_KEY` / `MPESA_CONSUMER_SECRET` | — | Your Daraja app's credentials |
| `MPESA_SHORTCODE` / `MPESA_PASSKEY` | `174379` / sandbox passkey | Your paybill or till and its passkey |
| `MPESA_CALLBACK_BASE` | `https://example.com` | Public HTTPS base URL for Daraja callbacks |
| `MPESA_CALLBACK_TOKEN` | — | Secret path segment in the callback URL |

The timezone is fixed to `Africa/Nairobi`.

## Project layout

```
config/                 settings (from .env), URLs, WSGI/ASGI
chamas/
  models.py             Chama, Member, Contribution
  views.py, urls.py     REST endpoints
  serializers.py        validation, phone and period parsing
  access.py             membership lookup and role checks
  summary.py            gathers data for the summary endpoint
  admin.py              admin with list displays and filters
  management/commands/seed_demo.py
payments/
  providers/base.py     PaymentProvider interface
  providers/paystack.py Paystack Charge API (default)
  providers/daraja.py   Daraja STK push
  services.py           apply_result (once only) and refresh_status
  views.py, urls.py     webhook endpoints
services/arrears.py     pure arrears and period functions
tests/                  pytest suite
```

## Running the tests

```bash
pytest
```

The suite covers:

- the arrears rules: new joiners, paying ahead, deactivated members, and weekly vs monthly
- Paystack: valid and invalid signatures, a tampered body, success, failure and duplicate webhooks
- Daraja: success, a 1032 cancel and duplicate callbacks, the token cache and the password
- permissions: a member can't record cash or add members, and an outsider gets 404
- the idempotency of `seed_demo`

Every Paystack and Daraja HTTP call is mocked, and any unmocked network call fails the test.

## Deployment notes

- Set `DJANGO_DEBUG=False`, a real `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS` and `DATABASE_URL`.
- For Postgres, run `pip install "psycopg[binary]"`.
- Serve the app with a WSGI server such as `gunicorn config.wsgi`. Run `python manage.py collectstatic` for the admin's static files.
- Webhooks must reach you over HTTPS: the Paystack dashboard URL, or `MPESA_CALLBACK_BASE` for Daraja.
- Use a long random `MPESA_CALLBACK_TOKEN`. It is the only thing that authenticates Daraja callbacks.

## Known limitations

- **No OTP endpoint**: if Paystack asks for an OTP (`send_otp`), its message is passed back to the client, but there's no endpoint yet to submit the OTP.
- **Missing receipts**: payments confirmed through Daraja's `stk_query` have no receipt unless the callback arrives later. Paystack test mode reuses the same receipt number on every charge, so after the first, receipts are stored as missing rather than blocking confirmation.
- **No deactivation date**: the app doesn't record when a member left, so a deactivated member's arrears from before they left aren't counted.
- **No pagination** on list endpoints yet.
